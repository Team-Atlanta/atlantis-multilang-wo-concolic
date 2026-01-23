# MCGA Agent

A agent that constructs precise call graphs for target functions within large,
multi-language codebases. The MCGA (Make Call Graph Agent) is designed to
systematically analyze function relationships, identify callsites, and build
structured call graphs that are essential for downstream vulnerability analysis,
payload generation.

## What is the MCGA Agent?

The MCGA Agent is responsible for:

- Call Graph Construction: Builds a detailed call graph for a given target function, capturing all direct and indirect callees.

- Vulnerable Sink Detection: Identifies and analyzes potential vulnerable sinks
(e.g., security-sensitive operations, taint sinks) within each function during
call graph traversal.

- Recursive Analysis: Traverses function calls recursively, handling complex call chains and preventing infinite loops.

- Context-Aware Resolution: Utilizes function context, callsite information, and parent-child relationships to resolve ambiguities.

- Caching and Efficiency: Uses Redis and in-memory caches to avoid redundant computation and speed up repeated analyses.

## Core Concept

The MCGA Agent's primary goal is to construct a complete and accurate call graph for a specified function while simultaneously detecting vulnerable sinks within each function in the graph. Given a function (with name, file, code, and location), MCGA performs the following steps:

1. Initializes Call Graph Node: Uses CGPA to resolve the function's definition and create the root node for analysis.

2. Identifies Callees: Analyzes the function body to extract all callsites and potential callees, mapping out the function's direct interactions.

3. Detects Vulnerable Sinks: Examines the function for the presence of vulnerable sinks (e.g., security-sensitive operations, taint sinks) using LLM-based reasoning.

4. Resolves Callee Information: For each callee, uses CGPA to obtain precise function information, ensuring accurate linkage in the call graph.

5. Recursively Expands Graph: For each callee, recursively builds subgraphs, handling cycles and depth limits to prevent infinite recursion.

6. Caches Results: Stores intermediate and final results in Redis and local caches for efficiency and scalability.

7. Returns Structured Call Graph with Vulnerability Annotations: Outputs a tree of FuncInfo nodes representing the call hierarchy, with each node annotated with sink detection results and vulnerability context.

This integrated approach enables robust, scalable call graph construction and vulnerability analysis, even in large and complex codebases, providing essential insights for downstream security tasks.


## Design Principles

### Recursive Call Graph Expansion

- Depth-Limited Traversal: Prevents infinite recursion with configurable depth limits.

- Cycle Detection: Uses cache tags to avoid revisiting the same function nodes.

- Parent-Child Context: Maintains parent function context for accurate callsite resolution.

### Multi-Source Callee Resolution

- CGPA Integration: Calls CGPA agent to resolve ambiguous or overloaded function names.

- Static and Dynamic Analysis: Optionally incorporates tracer results for dynamic callsite discovery.

### Caching & Efficiency

- Redis Caching: Stores call graph nodes for fast retrieval and to avoid redundant analysis.

- In-Memory Loop Cache: Prevents cycles and repeated work during recursive traversal.

## Architecture

```text
┌────────────────────┐
│ Target Function    │  (name, file, code, location)
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ CGPA Resolution    │  (resolve function info)
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ Callee Extraction  │  (find all callsites)
└─────────┬──────────┘
          │
          ▼
┌────────────────────────────┐
│ Vulnerable Sink Detection  │  (analyze for vulnerable sinks)
└─────────┬──────────────────┘
          │
          ▼
┌────────────────────────────┐
│ Callee Info Resolution     │  (CGPA for each callee)
└─────────┬──────────────────┘
          │
          ▼
┌────────────────────────────┐
│ Recursive Expansion        │  (build subgraphs for each callee)
└─────────┬──────────────────┘
          │
          ▼
┌────────────────────┐
│ Caching            │  (Redis, in-memory)
└─────────┬──────────┘
          │
          ▼
┌────────────────────────────────────────────┐
│ Structured Output with Vulnerability Info  │  (FuncInfo call graph + sink analysis)
└────────────────────────────────────────────┘

```
Workflow Overview:
- Input: Target function (name, file, code, location, tainted args)
- Process: Resolve → Extract callees → Detect vulnerable sinks → Resolve callees → Recursively expand → Cache
- Output: Call graph rooted at the target function (FuncInfo tree), with each node annotated with vulnerability (sink) analysis results

## Configuration

|Variable|Default|Description|
|---|---|---|
|MCGA_MODEL|o4-mini|LLM model for code understanding|
|MCGA_MAX_TIMEOUT|7200|Maximum analysis time (seconds)|
|MCGA_SANITIZER_VALIDATOR_MODEL|o4-mini|LLM model for sanitizer validation|

### Example Configuration

```bash
# MCGA Settings
MCGA_MODEL=o4-mini
MCGA_MAX_TIMEOUT=7200
MCGA_SANITIZER_VALIDATOR_MODEL=o4-mini
```

### State Structures
```python
# Input: What the agent needs
{
    "target_fn": (str, str, str, list[int], tuple[int, int]),  # (name, file, code, tainted_args, (start, end))
    "parent_fn": Optional[FuncInfo],                           # Parent function info (for context)
    "current_fn_info": Optional[FuncInfo],                     # Current function info
}

# Output: What the agent produces
{
    "cg_root_node": FuncInfo,                                  # Root node of the constructed call graph
}
```

#### Key Structures
```python
class FuncInfo(BaseModel):
    tainted_args: list[int] = Field(default_factory=list)
    func_location: LocationInfo  # Location and name information for the function (file, name, start/end line)
    func_signature: Optional[str] = Field(default=None)  # Function signature as a string (e.g., def foo(a, b): ...)
    func_body: Optional[str] = Field(default=None)  # Full source code body of the function
    children: list[FuncInfo] = Field(default_factory=list)  # List of child FuncInfo nodes (callees in the call graph)
    need_to_analyze: bool = Field(default=False)  # Whether this function still needs to be analyzed (for traversal)
    tainted_args: list[int] = Field(default_factory=list)  # Indices of arguments that are tainted (for vulnerability analysis)
    sink_detector_report: Optional[SinkDetectReport] = Field(
        default=None,
        compare=False,
    ) # Optional vulnerability/sink analysis report for this function
    interest_info: Optional[InterestInfo] = Field(default=None)
    # Optional info about whether this function is interesting (e.g., diff, special marker)
```
