# Mutator Agent

The Mutator Agent is a specialized vulnerability exploitation agent within the MLLA system. Its primary function is to **generate targeted code mutations** to create variations of existing payloads. This allows for the exploration of different execution paths and increases the chances of triggering a vulnerability.

## Purpose & Role

The Mutator Agent is one of three specialized payload generation agents coordinated by the Orchestrator Agent:
- **BlobGen Agent**: Single binary blob generation
- **Generator Agent**: Multi-payload generation strategies
- **Mutator Agent**: Payload mutation and variation (this agent)

**Core Mission**: To systematically generate variations of a payload by creating and applying targeted mutations. **This focused approach helps to address challenges where the overall source code or analysis context is too large, by concentrating on a single, critical transition.**

## How It Works

### Input → Process → Output Flow

**Input**: Vulnerability context (AttributeCG, source function, destination function)
**Process**: Plan mutation strategy → Create mutator code → Analyze and refine the mutator iteratively
**Output**: A dictionary of mutators, each with a plan, code, and description.

### Core Workflow

```
┌─────────────────┐
│ Plan            │
│ Mutation        │
└────────┬────────┘
         │
         ▼
┌──────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Create/Improve   │───▶│ Analyze         │───▶│ Finalize        │
│ Mutator          │    │ Mutator         │    │                 │
└──────────────────┘    └────────┬────────┘    └─────────────────┘
         ▲                       │
         │                       │
         └───────────────────────┘
           Improvement Loop
 (up to BGA_MUTATOR_MAX_ITERATION times)
```

The agent operates through three key phases in a loop:

1.  **Mutation Planning**: Devises a strategy on how to mutate the code based on the vulnerability context.
2.  **Mutator Creation**: Generates the actual mutator code based on the plan from the previous step.
3.  **Mutator Analysis and Refinement**: Analyzes the generated mutator and provides feedback for improvement. The agent can then loop back to the creation step to refine the mutator.

## Key Capabilities

### Focused Transition Analysis
- **Addresses Large Contexts**: By concentrating on a single transition between a source and destination function, the agent can operate effectively even when the overall program context is very large or complex.
- **Precise Mutations**: This focus allows for more precise and relevant mutations, as they are tailored to the specific data flow and logic of the transition being analyzed.

### Iterative Refinement
- The agent uses a loop to iteratively improve the generated mutators.
- It analyzes its own work and uses that feedback to create better mutations in the next iteration.
- This process continues until a satisfactory mutator is created or the maximum number of iterations is reached.

### Guided Mutation
- The mutation process is not random. It's guided by an initial plan that is based on the provided vulnerability context (AttributeCG).
- This ensures that the mutations are targeted and have a higher chance of being effective.

## Architecture & Implementation

### State Management

The agent operates with three main state structures:

```python
# Input: What the agent needs
MutatorAgentInputState = {
    "harness_name": str,
    "attr_cg": AttributeCG,
    "src_func": AttributeFuncInfo,
    "dst_func": AttributeFuncInfo,
}

# Output: What the agent produces
MutatorAgentOutputState = {
    "mutator_dict": Dict[str, MutatorPayload],
    "error": Dict[str, str],
}

# Payload: The core artifact
MutatorPayload = {
    "mutator_plan": str,
    "mutator_code": str,
    "mutator_desc": str,
    "mutator_hash": str,
    "mutator_feedback": str,
}
```

### Node Architecture

```
mlla/agents/mutator_agent/
├── agent.py              # Main agent implementation
├── graph.py              # LangGraph workflow definition
├── state.py              # State type definitions
├── nodes/                # Individual processing nodes
│   ├── plan_mutation.py
│   ├── create_mutator.py
│   └── analyze_mutator.py
└── prompts/              # LLM prompt templates
    └── build_prompts.py
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BGA_MUTATOR_MODEL` | `gpt-4o` | LLM model used for mutator generation |
| `BGA_MUTATOR_TEMPERATURE` | `0.4` | LLM generation temperature |
| `BGA_MUTATOR_MAX_TOKENS` | `4096` | Maximum tokens for LLM responses |
| `BGA_MUTATOR_MAX_ITERATION` | `1` | Maximum improvement attempts |

### Example Configuration
```bash
# Mutator Settings
BGA_MUTATOR_MODEL=gpt-4o
BGA_MUTATOR_TEMPERATURE=0.4
BGA_MUTATOR_MAX_TOKENS=4096
BGA_MUTATOR_MAX_ITERATION=1
```

## Development & Usage

### Basic Testing
```bash
# Quick test with minimal iterations
export BGA_MUTATOR_MODEL="gpt-4o-mini"
export BGA_MUTATOR_MAX_ITERATION="1"
# (Further testing requires integration with the Orchestrator Agent)
```

### Extending the Agent

**Add New Node Types**: Create functions in `nodes/` and update `graph.py` with new conditional logic.
**Modify Prompts**: Edit templates in `prompts/` for different mutation strategies.
**Enhance State**: Update `state.py` for additional data tracking.

## Limitations & Extension Opportunities

### Current Capabilities
- Targeted mutation generation based on AttributeCG.
- Iterative refinement of mutators.
- Focused analysis of single transitions to handle large contexts.

### Extension Possibilities
- **More Sophisticated Analysis**: The analysis step could be enhanced to provide more detailed feedback for improvement.
- **Integration with Coverage Analysis**: The agent could use coverage information to guide the mutation process more effectively.
- **Dynamic Adaptation**: The agent could adapt its mutation strategy based on the results of previous mutations.

---

**Note**: This agent is designed for targeted payload mutation. Its effectiveness comes from its ability to iteratively refine its mutations based on a structured plan and analysis, especially in scenarios with large and complex codebases.
