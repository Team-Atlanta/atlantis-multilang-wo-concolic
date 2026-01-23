# Orchestrator Agent

A master coordination agent that orchestrates the execution of three specialized vulnerability exploitation agents in the MLLA (Multi-Language LLM Agent) system. The Orchestrator Agent serves as the central command center that manages context creation, concurrent execution, and result aggregation across multiple payload generation strategies for automated vulnerability discovery in open-source software projects.

## What is MLLA?

MLLA is an automated vulnerability exploitation system that uses Large Language Models (LLMs) to generate payloads for fuzzing and vulnerability discovery. The system analyzes:

- **Call Graphs (CGs)**: Representations of function call relationships in source code
- **Bug Inducing Things (BITs)**: Specific code locations or patterns that may contain vulnerabilities
- **AttributeCGs**: Enhanced call graphs with vulnerability context and annotations

## Core Concept

The Orchestrator Agent's primary goal is **coordinating multiple specialized approaches** to maximize vulnerability exploitation effectiveness. It manages three distinct agent types:

### **BlobGenAgent** - Single Binary Payload Generation
- Creates one targeted binary blob designed to reach specific buggy code points
- Uses LLM-powered Python code generation with iterative improvement
- Best for: Direct, focused exploitation attempts

### **GeneratorAgent** - Probabilistic Payload Generation
- Creates generator functions that produce multiple payload variations
- Operates in standalone mode (general fuzzing) or guided mode (targeted exploitation)
- Best for: Broad exploration and probabilistic success through variation

### **MutatorAgent** - Function Transition Targeting
- Creates mutation functions for specific function-to-function transitions
- Designed to handle long call paths that exceed LLM context limits
- Best for: Surgical precision targeting in complex codebases

## Orchestration Process

Given vulnerability context from call graphs and bug-inducing things, the Orchestrator:

1. **Context Creation**: Transforms raw vulnerability data into structured contexts for each agent
2. **Concurrent Execution**: Runs enabled agents simultaneously using asyncio for maximum efficiency
3. **Intelligent Filtering**: Applies sophisticated filtering logic to avoid redundant work
4. **Result Aggregation**: Collects and consolidates results from all agents into unified output
5. **Resource Management**: Controls concurrency limits and manages system resources

This approach addresses the **complexity of modern vulnerability exploitation** - different vulnerability types require different strategies, so the Orchestrator ensures all viable approaches are explored simultaneously while avoiding resource waste.

## Design Principles

- **Multi-Agent Coordination**: Manages three distinct exploitation strategies concurrently
- **Resource Management**: Configurable concurrency limits with fault isolation between agents
- **Intelligent Filtering**: Eliminates redundant work through coverage-based and transition deduplication
- **Priority Processing**: Higher-priority BITs are processed multiple times for increased coverage
- **Auto-Sanitizer Selection**: Chooses appropriate sanitizers based on programming language

## Architecture

```
┌─────────────────┐
│ Preprocess      │ (Create contexts, determine sanitizers, priority handling)
└────────┬────────┘
         │
         ├───────────────────────┬───────────────────────┐
         ▼                       ▼                       ▼
┌──────────────────┐    ┌─────────────────┐    ┌──────────────────┐
│ BlobGenAgent     │    │ GeneratorAgent  │    │ MutatorAgent     │
│ (Concurrent)     │    │ (Concurrent)    │    │ (Concurrent)     │
└──────────────────┘    └─────────────────┘    └──────────────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 ▼
                        ┌─────────────────┐
                        │ Finalize        │ (Aggregate results, status reporting)
                        └─────────────────┘
```

**Workflow Overview**:
- **Input**: Call Graphs (CGs), Bug Inducing Things (BITs), sanitizer preferences
- **Process**: Context creation → Concurrent agent execution → Result aggregation
- **Output**: Consolidated results from all agents with comprehensive status information

## Key Components

### Context Creation & Management
- Creates structured contexts from CGs and BITs for agent consumption
- Duplicates higher-priority BITs to increase exploration probability
- Auto-selects sanitizers based on language (JVM → jazzer, others → address)
- Integrates crash history to avoid redundant exploration

### Concurrent Agent Execution
- Uses Python asyncio for true concurrent execution of all enabled agents
- Implements configurable concurrency limits to prevent resource exhaustion
- Provides fault isolation - agent failures don't cascade to other agents
- Coordinates result collection from all concurrent agents

### Advanced Filtering & Optimization
- Eliminates duplicate function transitions across different call graphs
- Skips transitions already covered by existing fuzzer seeds
- Focuses on transitions with meaningful conditions or specific vulnerability targets
- Validates file paths for proper analysis

### Result Aggregation & Status Management
- Aggregates results from all agents with comprehensive status tracking
- Consolidates errors in structured format with performance metrics
- Ensures result consistency and completeness before finalization

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATOR_MODEL` | `gpt-4o` | LLM model used for orchestrator operations |
| `ORCHESTRATOR_TEMPERATURE` | `0.4` | LLM generation temperature |
| `ORCHESTRATOR_MAX_TOKENS` | `4096` | Maximum tokens for LLM responses |
| `ORCHESTRATOR_EVAL_NUM_TEST` | `1` | Number of test evaluations per context |
| `ORCHESTRATOR_MAX_CONCURRENT_CG` | `5` | Maximum concurrent call graph processing |
| `ORCHESTRATOR_HANDLE_UNVISITED_CGS` | `False` | Process call graphs without matching BITs |
| `ORCHESTRATOR_BGA_USE` | `False` | Enable BlobGenAgent execution |
| `ORCHESTRATOR_GENERATOR_USE` | `False` | Enable GeneratorAgent execution |
| `ORCHESTRATOR_MUTATOR_USE` | `False` | Enable MutatorAgent execution |
| `ORCHESTRATOR_MUTATOR_DEDUP_AMONG_CGS` | `True` | Deduplicate transitions across call graphs |
| `ORCHESTRATOR_MUTATOR_FILTER_ALREADY_IN_COVERAGE` | `False` | Filter transitions already in coverage |
| `ORCHESTRATOR_MUTATOR_FILTER_NO_BIT_CGS` | `False` | Filter call graphs without BIT nodes |
| `ORCHESTRATOR_MUTATOR_FILTER_NO_CONDITIONS` | `False` | Filter transitions without key conditions |

### Example Configuration
```bash
# Orchestrator Core Settings
ORCHESTRATOR_MODEL=claude-sonnet-4-20250514
ORCHESTRATOR_TEMPERATURE=0.4
ORCHESTRATOR_MAX_TOKENS=4096
ORCHESTRATOR_MAX_CONCURRENT_CG=10

# Agent Enablement
ORCHESTRATOR_BGA_USE=true
ORCHESTRATOR_GENERATOR_USE=true
ORCHESTRATOR_MUTATOR_USE=true

# Filtering & Optimization
ORCHESTRATOR_MUTATOR_DEDUP_AMONG_CGS=true
ORCHESTRATOR_MUTATOR_FILTER_ALREADY_IN_COVERAGE=true
ORCHESTRATOR_MUTATOR_FILTER_NO_CONDITIONS=true

# Evaluation Settings
ORCHESTRATOR_EVAL_NUM_TEST=3
ORCHESTRATOR_HANDLE_UNVISITED_CGS=true
```

## State Structures

```python
# Input: What the orchestrator needs
{
    "cp": sCP,                              # Source code project information
    "CGs": Dict[str, List],                 # Call graphs by harness name
    "BITs": List[BugInducingThing],         # Bug inducing things for targeting
    "sanitizer": str                        # Base sanitizer type
}

# Output: What the orchestrator produces
{
    "blobgen_results": Dict[str, BlobGenPayload],    # Results from BlobGenAgent
    "generator_results": Dict[str, GeneratorPayload], # Results from GeneratorAgent
    "mutator_results": Dict[str, MutatorPayload],     # Results from MutatorAgent
    "status": Literal["success", "partial_success", "failed"], # Overall status
    "error": Dict[str, Any]                          # Error information
}

# BlobGenContext: Context for agent coordination
{
    "harness_name": str,                    # Target harness name
    "sanitizer": str,                       # Sanitizer type for this context
    "cg_name": str,                         # Call graph name
    "attr_cg": Optional[AttributeCG],       # Attributed call graph with vulnerability context
    "bit": Optional[BugInducingThing],      # Associated bug inducing thing
    "selected_sanitizers": List[str]        # Sanitizers selected for this context
}

# Overall State: Complete orchestrator state
{
    "gc": GlobalContext,                    # Global context for system state
    "llm": LLM,                            # Language model instance
    "blobgen_contexts": List[BlobGenContext], # Contexts for agent processing
    "transitions": List[tuple]              # Collected transitions for MutatorAgent
}
```

## Agent Coordination

- **BlobGenAgent**: Converts contexts to agent input state, processes multiple contexts with semaphore control
- **GeneratorAgent**: Reuses BlobGenContexts, validates AttributeCG nodes, integrates sanitizers, detects crashes
- **MutatorAgent**: Extracts unique transitions, applies filtering stages, integrates coverage data, runs independently

## Filtering & Optimization

### Priority-Based BIT Processing
Higher priority BITs are duplicated for increased exploration probability.

### Transition Filtering Pipeline
1. Gather all transitions from AttributeCGs
2. Remove duplicate transitions across call graphs
3. Skip transitions already covered by existing seeds
4. Focus on transitions with specific vulnerability targets
5. Prioritize transitions with meaningful execution conditions

### Crash History Integration
Loads crash history to avoid re-exploring known crashes and filters contexts based on BIT-specific crash patterns.

## Comparison with Individual Agents

| Aspect | BlobGenAgent | GeneratorAgent | MutatorAgent | Orchestrator Agent |
|--------|--------------|----------------|--------------|-------------------|
| **Role** | Single blob generation | Probabilistic generation | Function transitions | Master coordinator |
| **Scope** | Individual payloads | Multiple variations | Targeted mutations | System-wide orchestration |
| **Execution** | Sequential processing | Sequential processing | Sequential processing | Concurrent multi-agent |
| **Context** | Single context focus | Single context focus | Single transition focus | Multi-context management |
| **Optimization** | Internal iteration | Internal iteration | Internal iteration | Cross-agent optimization |
| **Resource Usage** | Single agent resources | Single agent resources | Single agent resources | Multi-agent resource management |

## Limitations & Extension Opportunities

### Current Capabilities
- Concurrent execution of three specialized agents
- Advanced filtering and deduplication logic
- Priority-based BIT processing with duplication
- Comprehensive error handling and status tracking
- Configurable concurrency and filtering controls

### Extension Possibilities
- **Dynamic Agent Selection**: Choose agents based on vulnerability characteristics
- **Load Balancing**: Distribute work based on agent performance and resource usage
- **Result Fusion**: Combine results from multiple agents for enhanced effectiveness
- **Adaptive Filtering**: Machine learning-based filtering optimization
- **Real-time Monitoring**: Live performance monitoring and adjustment
- **Plugin Architecture**: Support for additional specialized agents
- **Distributed Execution**: Scale orchestration across multiple machines

## Common Issues

| Issue | Solution |
|-------|----------|
| No agents enabled | Set at least one `ORCHESTRATOR_*_USE` variable to `true` |
| High memory usage | Reduce `ORCHESTRATOR_MAX_CONCURRENT_CG` value |
| Slow execution | Increase concurrency limits or enable fewer agents |
| No results from agents | Check individual agent configurations and error logs |
| Context creation failures | Verify CG and BIT data integrity |
| Filtering too aggressive | Disable optional filtering flags |
| Semaphore deadlocks | Ensure concurrency limits are reasonable for system resources |

## Development

### Key Files
```
mlla/agents/orchestrator_agent/
├── agent.py              # Main orchestrator implementation
├── state.py              # State type definitions
├── modules/              # Agent coordination modules
│   ├── __init__.py
│   ├── run_all_agents.py         # Main coordination logic
│   ├── call_blobgen_agent.py     # BlobGenAgent coordination
│   ├── call_generator_agent.py   # GeneratorAgent coordination
│   └── call_mutator_agent.py     # MutatorAgent coordination
└── README.md             # This documentation
```

### Extending the Orchestrator

1. **Add New Agents**: Create new coordination modules in `modules/` and update `run_all_agents.py`
2. **Enhance Filtering**: Extend filtering logic in individual agent coordination modules
3. **Improve Context Creation**: Update context creation logic for new agent requirements
4. **Add Monitoring**: Implement performance monitoring and metrics collection
5. **Optimize Concurrency**: Fine-tune semaphore usage and task management

### Testing

```bash
# Test with all agents enabled
export ORCHESTRATOR_BGA_USE=true
export ORCHESTRATOR_GENERATOR_USE=true
export ORCHESTRATOR_MUTATOR_USE=true
export ORCHESTRATOR_MAX_CONCURRENT_CG=3
python -m mlla.main --cp <test-project> --harness <test-harness>

# Test with selective agent execution
export ORCHESTRATOR_BGA_USE=false
export ORCHESTRATOR_GENERATOR_USE=true
export ORCHESTRATOR_MUTATOR_USE=false
python -m mlla.main --cp <test-project> --harness <test-harness>

# Test with aggressive filtering
export ORCHESTRATOR_MUTATOR_FILTER_ALREADY_IN_COVERAGE=true
export ORCHESTRATOR_MUTATOR_FILTER_NO_CONDITIONS=true
python -m mlla.main --cp <test-project> --harness <test-harness>
```

---

**Note**: The Orchestrator Agent represents the central coordination layer of the MLLA system, designed to maximize vulnerability exploitation effectiveness through intelligent multi-agent coordination and resource optimization.
