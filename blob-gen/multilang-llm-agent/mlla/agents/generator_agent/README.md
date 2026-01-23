# Generator Agent

The Generator Agent is a specialized vulnerability exploitation agent within the MLLA system that **probabilistically reaches target vulnerability points** through intelligent payload generation. Its core innovation is creating Python generator functions that produce multiple payload variations, dramatically increasing the likelihood of successful target reaching compared to single-blob approaches.

## Purpose & Role

The Generator Agent is one of three specialized payload generation agents coordinated by the Orchestrator Agent:
- **BlobGen Agent**: Single binary blob generation
- **Generator Agent**: Multi-payload generation strategies (this agent)
- **Mutator Agent**: Payload mutation and variation

**Core Mission**: **Probabilistically reach Bug Inducing Things (BITs)** by creating generator functions that produce multiple payload variations, solving the fundamental challenge that single LLM-generated payloads often fail to reach their targets due to non-deterministic generation.

## How It Works

### Input → Process → Output Flow

**Input**: Vulnerability context (AttributeCG, harness info) OR source code files (standalone mode)
**Process**: Plan generation strategy → Create `generate(rnd: random.Random) -> bytes` function → Execute multiple variations → Analyze coverage → Iteratively improve
**Output**: Generator function that **probabilistically reaches targets** by producing multiple payload variations with high success probability

### Core Workflow

```
┌─────────────────┐
│ Select          │ (Optional - standalone mode only)
│ Sanitizer       │
└────────┬────────┘
         │
         ▼
┌──────────────────┐    ┌─────────────────┐    ┌──────────────────┐
│ Plan             │───▶│ Create/Improve  │───▶│ Collect          │
│ Generator        │    │ Generator       │    │ Coverage         │
└──────────────────┘    └─────────────────┘    └─────────┬────────┘
                                 ▲                       │
      ┌─────────────────────────▶│                       │
      │                          │                       ▼
      │                 ┌─────────────────┐    ┌──────────────────┐
      │                 │ Analyze         │◀───│ Update           │
      │                 │ Coverage        │    │ Interesting      │
      │                 └────────┬────────┘    │ Functions        │
      │                          │             └──────────────────┘
      │                          ▼
      │                 ┌─────────────────┐
      │                 │ Finalize        │
      │                 └─────────────────┘
      │
  Iterative Improvement Loop
  (up to BGA_GENERATOR_MAX_ITERATION times)
```

The agent operates through six key phases:

1. **Sanitizer Selection** (Optional): Chooses appropriate sanitizers for standalone mode
2. **Generator Planning**: Analyzes code paths and vulnerability requirements to create generation strategy
3. **Generator Creation**: Creates Python `generate(rnd: random.Random) -> bytes` functions using LLM
4. **Coverage Collection**: Executes multiple generated payload variations and collects merged coverage data
5. **Function Updates**: Identifies and updates functions showing promising coverage patterns
6. **Coverage Analysis**: Analyzes effectiveness and provides feedback for iterative improvement

## Key Capabilities

### Dual Operation Modes

**Standalone Mode**
- Functions as a general fuzzer for vulnerability discovery
- Input: Source code files and optional diff files
- Use case: Broad fuzzing campaigns, exploratory testing
- Activation: Set `standalone=True` and provide `source_path`

**Guided Mode** (Normal Mode)
- Functions as a targeted fuzzer for specific vulnerability exploitation
- Input: AttributeCG data with source and destination function information
- Use case: Targeted exploitation of known vulnerabilities
- Activation: Provide `attr_cg`, `src_func`, and `dst_func` parameters

### Probabilistic Target Reaching
- **Core Innovation**: **Probabilistically reach target vulnerability points** through multiple payload variations
- **Problem**: Single LLM-generated blobs frequently fail to reach targets due to non-deterministic nature
- **Solution**: Creates generator functions that produce multiple variations using controlled randomness, ensuring at least one variation probabilistically reaches the target
- **Benefit**: Dramatically higher target-reaching success rate through systematic variation exploration
- **Output**: Self-contained Python functions that probabilistically navigate to and trigger vulnerabilities

### Coverage-Guided Iterative Improvement
- Executes multiple generated payload variations against target harnesses
- Merges coverage information from all variations for comprehensive analysis
- Uses coverage gaps and execution traces to improve generation strategies
- Attempts up to `BGA_GENERATOR_MAX_ITERATION` times with refined approaches
- Integrates with AddressSanitizer, MemorySanitizer, and other sanitizers

### Code Validation & Execution
- Validates generated functions execute successfully and produce valid binary outputs
- Maintains valid input formats while exploring mutation strategies
- Uses UniAFL's executor to monitor code execution paths and verify target reaching
- Stores successful payloads that cause crashes using unified artifact storage system

## Architecture & Implementation

### State Management

The agent operates with three main state structures:

```python
# Input: What the agent needs
GeneratorAgentInputState = {
    "harness_name": str,                    # Target harness
    "attr_cg": AttributeCG,                 # Vulnerability context (guided mode)
    "src_func": AttributeFuncInfo,          # Source function (guided mode)
    "dst_func": AttributeFuncInfo,          # Destination function (guided mode)
    "sanitizer": str,                       # Base sanitizer type
    "selected_sanitizers": List[str],       # Selected sanitizers
    "standalone": bool,                     # Enable standalone mode
    "source_path": str,                     # Source code path (standalone mode)
    "diff_path": str,                       # Optional diff file (standalone mode)
    "run_sanitizer_selection": bool,        # Whether to run sanitizer selection
    "bit": Optional[BugInducingThing]       # Bug details
}

# Output: What the agent produces
GeneratorAgentOutputState = {
    "crashed_blobs": Dict[str, bytes],      # Successful payloads that caused crashes
    "error": Dict[str, str]                 # Error information
}

# Generator Payload: The core artifact
GeneratorPayload = {
    "generator_code": str,                  # Generated Python generator function
    "generator_desc": str,                  # Generator description
    "generator_hash": str,                  # Hash of the generator code
    "generator_blobs": List[bytes],         # Generated payload variations
    "coverage_results": List[Dict],         # Coverage info for each variation
    "merged_coverage": Dict,                # Merged coverage from all variations
    "prev_coverage_info": Dict,             # Previous coverage for comparison
    "coverage_diff": Dict,                  # Coverage differences
    "coverage_stats": Dict,                 # Coverage statistics
    "coverage_diff_str": str                # Human-readable coverage diff
}
```

### Node Architecture

```
mlla/agents/generator_agent/
├── agent.py              # Main agent implementation
├── graph.py              # LangGraph workflow definition
├── state.py              # State type definitions
├── utils.py              # Utility functions
├── nodes/                # Individual processing nodes
│   ├── plan_generator.py
│   ├── create_generator.py
│   ├── collect_coverage.py
│   ├── analyze_coverage.py
│   ├── update_interesting_functions.py
│   ├── select_sanitizer.py
│   └── common.py
└── prompts/              # LLM prompt templates
    ├── build_prompts.py
    ├── system_prompts.py
    ├── plan_prompts.py
    ├── create_prompts.py
    ├── improve_prompts.py
    ├── analyze_prompts.py
    ├── update_interesting_prompts.py
    └── sanitizer_prompts.py
```

### Execution Environment

- **Docker Integration**: All payload execution happens in isolated Docker containers
- **Async Processing**: Coverage collection uses async execution for efficiency
- **Artifact Storage**: Unified storage system tracks generators, coverage data, and crashed blobs
- **Resource Management**: Configurable timeouts and iteration limits for processing control

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BGA_GENERATOR_MODEL` | `claude-sonnet-4-20250514` | LLM model used for generator creation |
| `BGA_GENERATOR_TEMPERATURE` | `0.4` | LLM generation temperature |
| `BGA_GENERATOR_MAX_TOKENS` | `64000` | Maximum tokens for LLM responses |
| `BGA_GENERATOR_MAX_ITERATION` | `4` | Maximum improvement iterations |
| `BGA_GENERATOR_MAX_RETRIES` | `3` | Maximum retries for LLM calls |
| `BGA_GENERATOR_SEED_NUM` | `31337` | Seed for reproducible generation |
| `BGA_GENERATOR_NUM_BLOBS` | `20` | Number of payload variations to generate |
| `BGA_GENERATOR_NUM_TEST_BLOBS` | `3` | Number of test blobs for validation |
| `BGA_GENERATOR_STANDALONE_TIMEOUT` | `1000` | Timeout for standalone mode processing |
| `BGA_GENERATOR_STANDALONE_EVAL_NUM_TEST` | `3` | Number of test evaluations in standalone mode |

### Example Configuration
```bash
# Generator Settings
BGA_GENERATOR_MODEL=claude-sonnet-4-20250514
BGA_GENERATOR_TEMPERATURE=0.4
BGA_GENERATOR_MAX_TOKENS=64000
BGA_GENERATOR_SEED_NUM=31337
BGA_GENERATOR_NUM_BLOBS=20
BGA_GENERATOR_NUM_TEST_BLOBS=3
BGA_GENERATOR_MAX_RETRIES=3
BGA_GENERATOR_MAX_ITERATION=4
BGA_GENERATOR_STANDALONE_TIMEOUT=1000
```

## Development & Usage

### Basic Testing

```bash
# Standalone mode test
export BGA_GENERATOR_MODEL="gpt-4o-mini"
export BGA_GENERATOR_MAX_ITERATION="2"
python -m mlla.main --cp <test-project> --harness <test-harness> \
  --generator-standalone --source-path <source-file>

# Guided mode test
python -m mlla.main --cp <test-project> --harness <test-harness> \
  --use-generator --attr-cg <cg-file>
```

### Extending the Agent

**Add New Node Types**: Create functions in `nodes/` and update `graph.py` workflow
**Modify Generation Strategies**: Edit templates in `prompts/` for different approaches
**Enhance State Tracking**: Update `state.py` for additional data and metrics
**Improve Analysis Logic**: Extend coverage analysis and feedback mechanisms
**Add New Modes**: Implement additional operation modes beyond standalone/guided

### Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| Generator function fails | Check Python syntax and imports in generated code |
| No coverage improvement | Increase `BGA_GENERATOR_MAX_ITERATION` or adjust temperature |
| Memory errors during generation | Reduce `BGA_GENERATOR_NUM_BLOBS` or optimize generator logic |
| Standalone mode timeout | Increase `BGA_GENERATOR_STANDALONE_TIMEOUT` |
| No target reached | Verify vulnerability context and function information |
| Generator produces invalid payloads | Review format requirements and validation logic |

## Comparison with BlobGen Agent

| Aspect | BlobGen Agent | Generator Agent |
|--------|---------------|-----------------|
| **Target Reaching** | Single attempt, may miss target | **Probabilistically reaches targets** through multiple variations |
| **Approach** | Single binary blob generation | Multiple payload variation generation |
| **Strategy** | Deterministic single attempt | **Probabilistic target reaching** through multiple attempts |
| **Success Rate** | Depends on single blob accuracy | **Higher target-reaching probability** due to variation exploration |
| **Output** | One blob per iteration | Generator function producing many target-seeking variations |
| **Coverage** | Single payload coverage | Merged coverage from multiple variations |
| **Use Case** | Targeted single-shot exploitation | **Probabilistic target reaching** and broad exploration |

## Limitations & Extension Opportunities

### Current Capabilities
- Dual-mode operation for different fuzzing scenarios
- Probabilistic payload generation with multiple variations
- Coverage-guided iterative improvement
- Format-aware mutation strategies
- Integration with multiple sanitizers

### Extension Possibilities
- **Advanced Mutation Strategies**: Implement more sophisticated payload variation techniques
- **Cross-Format Support**: Extend beyond current format types
- **Machine Learning Integration**: Use ML models to guide generation strategies
- **Distributed Generation**: Scale generator execution across multiple instances
- **Hybrid Approaches**: Combine with other fuzzing techniques for enhanced effectiveness
- **Real-time Adaptation**: Dynamic strategy adjustment based on target behavior

---

**Note**: The Generator Agent represents a fundamental breakthrough in automated payload generation by **probabilistically reaching target vulnerability points**. Unlike deterministic single-blob approaches that often miss their targets, this agent creates generator functions that systematically explore variations until targets are probabilistically reached, dramatically improving success rates in vulnerability exploitation scenarios.
