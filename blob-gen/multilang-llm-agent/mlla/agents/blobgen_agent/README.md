# BlobGen Agent

The BlobGen Agent is a specialized vulnerability exploitation agent within the MLLA system. Its core innovation is **generating a synthetic Python script to create a targeted binary payload**, rather than directly generating the blob itself. This script-based approach allows for more complex, structured, and reproducible payload creation. The agent then uses iterative improvement based on execution feedback and coverage analysis to refine the script until the generated payload successfully triggers the vulnerability.

## Purpose & Role

The BlobGen Agent is one of three specialized payload generation agents coordinated by the Orchestrator Agent:
- **BlobGen Agent**: Single binary blob generation (this agent)
- **Generator Agent**: Multi-payload generation strategies  
- **Mutator Agent**: Payload mutation and variation

**Core Mission**: **Generate a Python script that produces a binary blob** capable of reaching a specific Bug Inducing Thing (BIT). This indirect generation method is the key to creating precise and effective exploits.

## How It Works

### Input → Process → Output Flow

**Input**: Vulnerability context (AttributeCG, harness info, sanitizer configuration)
**Process**: Generate a Python `create_payload() -> bytes` script → Execute the script to get a blob → Run the blob against the target → Analyze results → Refine the script
**Output**: Binary blob that successfully triggers the target vulnerability

### Core Workflow

```
┌─────────────────┐
│ Select          │ (Optional - skipped if sanitizers pre-selected)
│ Sanitizer       │
└────────┬────────┘
         │
         ▼
┌──────────────────┐    ┌─────────────────┐    ┌──────────────────┐
│ Generate/Improve │───▶│ Collect         │───▶│ Analyze          │
│ Payload          │    │ Coverage        │    │ Failure          │
└──────────────────┘    └─────────────────┘    └─────────┬────────┘
         ▲                       │                       │
         │                       ▼                       │
         │               ┌─────────────────┐             │
         │               │ Finalize        │◀────────────│
         │               └─────────────────┘             │
         │                                               │
         └───────────────────────────────────────────────┘
           Retry Generation (up to BGA_MAX_ITERATION times)
```

The agent operates through five key phases:

1. **Sanitizer Selection** (Optional): Chooses appropriate sanitizers if not pre-configured
2. **Payload Script Generation**: Creates a Python `create_payload() -> bytes` script using an LLM. This is the critical step where the agent defines the logic for building the exploit payload.
3. **Coverage Collection**: Executes payloads in Docker environment and collects execution data
4. **Failure Analysis**: Analyzes why payloads failed to trigger vulnerabilities  
5. **Iterative Refinement**: Repeats generation with insights from previous attempts

## Key Capabilities

### Python-based Payload Generation
- **Core Strategy**: Instead of generating a raw binary blob, the agent **generates a Python script** that programmatically constructs the blob.
- **Flexibility & Precision**: This script-based approach allows for precise control over the payload's structure, including complex data formats, checksums, and dynamic values.
- **Reproducibility**: The generated script provides a reproducible recipe for the exploit payload.
- **LLM-Powered**: Uses structured LLM prompts to create the Python script, incorporating vulnerability context (AttributeCG data, bug annotations, sanitizer info).
- **Validation**: The agent validates that the generated Python script executes successfully and produces a valid binary output before testing it against the target.

### Iterative Improvement System
- Analyzes execution results to understand payload failures
- Generates specific improvement suggestions based on coverage data and execution traces
- Retries up to `BGA_MAX_ITERATION` times with refined approaches
- Uses previous attempt results to inform subsequent payload generation

### Execution & Verification
- Executes payloads against target harnesses using Docker-based execution environment
- Monitors code execution paths and verifies target buggy points are reached
- Detects sanitizer triggers (AddressSanitizer, MemorySanitizer, etc.)
- Captures and analyzes crash information when vulnerabilities are exploited

### Code Validation & Constraints
- Code validation before execution with error handling
- Memory and size constraints: 1GB memory limit, 1MB blob size limit
- Sanitizer integration for vulnerability detection
- Artifact storage system for tracking all generated payloads

## Architecture & Implementation

### State Management

The agent operates with three main state structures:

```python
# Input: What the agent needs
BlobGenAgentInputState = {
    "harness_name": str,                    # Target harness
    "sanitizer": str,                       # Base sanitizer type
    "selected_sanitizers": List[str],       # Selected sanitizers for this CG-BIT mapping
    "cg_name": str,                         # Name of the call graph
    "attr_cg": AttributeCG,                 # Vulnerability context
    "bit": Optional[BugInducingThing],      # Bug details
    "run_sanitizer_selection": bool         # Whether to run sanitizer selection
}

# Output: What the agent produces
BlobGenAgentOutputState = {
    "payload_dict": Dict[str, BlobGenPayload],  # All generated payloads
    "crashed_blobs": Dict[str, BlobGenPayload], # Successful payloads that caused crashes
    "status": str,                              # Overall processing status
    "error": Dict                               # Error information
}

# Payload: The core artifact
BlobGenPayload = {
    "code": str,                            # Generated Python code
    "desc": str,                            # Payload description
    "blob": bytes,                          # Binary payload
    "blob_hash": str,                       # Hash of the blob
    "crashed": bool,                        # Whether this payload caused a crash
    "coverage_info": Dict,                  # Coverage specific to this payload
    "failure_explanation": str,             # Explanation of failure (if any)
    "sanitizer_info": Optional[str],        # Sanitizer information
    "run_pov_result": Optional[RunPovResult] # Result of running the payload
}
```

### Node Architecture

```
mlla/agents/blobgen_agent/
├── agent.py              # Main agent implementation
├── graph.py              # LangGraph workflow definition
├── state.py              # State type definitions
├── nodes/                # Individual processing nodes
│   ├── payload_generation.py    # LLM-based payload creation
│   ├── collect_coverage.py      # Docker execution & coverage
│   ├── failure_analysis.py      # Result analysis & feedback
│   └── select_sanitizer.py      # Sanitizer selection logic
└── prompts/              # LLM prompt templates
    ├── build_prompts.py         # Prompt construction
    ├── system_prompt.py         # System-level prompts
    ├── create_prompt.py         # Payload creation prompts
    ├── coverage_prompt.py       # Coverage analysis prompts
    ├── failure_analysis.py      # Failure analysis prompts
    └── sanitizer_selector.py    # Sanitizer selection prompts
```

### Execution Environment

- **Docker Integration**: All payload execution happens in isolated Docker containers
- **Async Processing**: Coverage collection uses async execution for efficiency
- **Artifact Storage**: Unified storage system tracks all payloads, coverage data, and results
- **Resource Constraints**: Memory limits (1GB) and size constraints (1MB) for payload generation

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BGA_MODEL` | `claude-sonnet-4-20250514` | LLM model used for payload generation |
| `BGA_TEMPERATURE` | `0.4` | LLM generation temperature |
| `BGA_MAX_TOKENS` | `64000` | Maximum tokens for LLM responses |
| `BGA_MAX_ITERATION` | `4` | Maximum improvement attempts |
| `BGA_MAX_RETRIES` | `3` | Maximum retries for LLM calls |
| `BGA_MAX_BLOB_SIZE` | `1048576` | Maximum blob size (1MB) |
| `BGA_CG_TIMEOUT` | `1000` | Maximum time in seconds for processing each CG |

### Example Configuration
```bash
# BGA Settings
BGA_MAX_BLOB_SIZE=1048576
BGA_MODEL=claude-sonnet-4-20250514
BGA_TEMPERATURE=0.4
BGA_MAX_TOKENS=64000
BGA_MAX_ITERATION=4
BGA_MAX_RETRIES=3
BGA_CG_TIMEOUT=1000  # Maximum time in seconds for processing each CG
```

## Development & Usage

### Basic Testing
```bash
# Quick test with minimal iterations
export BGA_MODEL="gpt-4o-mini"
export BGA_MAX_ITERATION="1"
python -m mlla.main --cp <test-project> --harness <test-harness>
```

### Extending the Agent

**Add New Node Types**: Create functions in `nodes/` and update `graph.py` with new conditional logic
**Modify Prompts**: Edit templates in `prompts/` for different generation strategies  
**Enhance State**: Update `state.py` for additional data tracking
**Improve Analysis**: Extend failure analysis logic for better feedback

### Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| Blob size too large | Increase `BGA_MAX_BLOB_SIZE` or generate smaller payloads |
| Memory errors | Reduce payload complexity or check Docker memory limits |
| No target reached | Verify vulnerability context, increase iterations |
| Execution failures | Check Docker setup and harness configuration |
| Timeout errors | Increase `BGA_CG_TIMEOUT` for complex targets |

## Limitations & Extension Opportunities

### Current Capabilities
- Single binary blob generation with iterative improvement
- Coverage-based feedback and failure analysis
- Integration with multiple sanitizers
- Code validation with basic resource constraints
- Docker-based isolated execution

### Extension Possibilities
- **Multi-stage Payloads**: Generate sequences of inputs for complex exploitation
- **Advanced Feedback**: More sophisticated analysis of execution traces and coverage patterns
- **Dynamic Adaptation**: Adjust strategy based on target characteristics and runtime behavior
- **Payload Optimization**: Size/complexity optimization for specific targets
- **Cross-Language Support**: Extend beyond current language targets
- **Parallel Generation**: Multiple payload strategies running concurrently

---

**Note**: This agent represents a focused approach to automated payload generation with emphasis on iterative improvement. The architecture is designed to be extensible for more sophisticated vulnerability exploitation scenarios while maintaining reliability through basic resource constraints.
