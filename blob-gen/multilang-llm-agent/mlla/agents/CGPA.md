# CGPA Agent

A specialized agent for retrieving precise function information within
large codebases. The CGPA (Call Graph Parser Agent) is designed
to resolve ambiguities in function definitions, especially in complex,
C/C++/Java projects, by leveraging multiple code analysis tools and
strategies. It is a core component of the MLLA system,
supporting other agents by providing accurate
function metadata and code context.

## What is the CGPA Agent?

The CGPA Agent is responsible for:

- Locating Function Definitions: Finds the exact location and metadata for a
given function name, even in the presence of overloads, multiple candidates,
or ambiguous references.

- Aggregating Code Intelligence: Integrates results from LSP servers, static
analysis tools (Joern), code indexers, and AST-based search to maximize
accuracy.

- Providing Structured Function Info:
Returns results as structured FuncInfo objects, including file path, signature,
and code body.

- Supporting Downstream Agents: Supplies function information to MCGA, and BCDA
for call graph construction, and vulnerability analysis.

## Core Concept

The CGPA Agent's primary goal is to resolve function identity
and location in large, multi-language codebases. Given a function
name (and optionally, file and callsite context), it:

1. Searches Multiple Sources: Queries LSP, Joern, code indexers, and AST tools
for candidate definitions.

2. Deduplicates and Filters: Removes duplicates and filters candidates based
on context (file, callsite, etc.).

3. Selects the Best Match: Uses heuristics or LLM-based selection to choose
the most relevant function definition.

4. Caches Results: Stores results in Redis for fast future retrieval.

5. Returns Structured Output: Provides a FuncInfo object with
all relevant details.

This approach ensures robust and accurate function resolution, even in the face
of codebase complexity.

## Design Principles

### Multi-Source Aggregation

- LSP Integration: Uses Language Server Protocol for symbol and
definition lookup.

- Joern Static Analysis: Leverages Joern for deep code graph queries.

- Code Indexer: Fast indexed search for function definitions.

- AST Grep Tool: Fallback for syntax-based search.

### Context-Aware Selection

- Callsite Awareness: Uses callsite location/range to disambiguate overloaded
functions.

- File Path Filtering: Prefers candidates in the expected file when possible.

- LLM-Assisted Selection: Optionally uses LLMs to select among multiple
candidates.

### Caching & Efficiency

- Redis Caching: Stores results for repeated queries to minimize redundant
computation.

- Deduplication: Ensures only unique, relevant function definitions
are returned.

## Architecture


```

┌─────────────┐
│ Query Input │  (function name, file, callsite, etc.)
└──────┬──────┘
       │
       ▼
┌────────────────────┐
│ Multi-Source Query │
│ (LSP, Joern, CI,   │
│  AST Grep)         │
└──────┬─────────────┘
       │
       ▼
┌────────────────────┐
│ Deduplication &    │
│ Filtering          │
└──────┬─────────────┘
       │
       ▼
┌────────────────────┐
│ Candidate Selection│
│ (Heuristic/LLM)    │
└──────┬─────────────┘
       │
       ▼
┌────────────────────┐
│ Redis Caching      │
└──────┬─────────────┘
       │
       ▼
┌────────────────────┐
│ Structured Output  │
│ (FuncInfo)         │
└────────────────────┘
```

Workflow Overview:

- Input: Function name, optional file path, callsite location/range, caller info

- Process: Query all sources → Deduplicate/filter → Select best match → Cache → Return

- Output: FuncInfo object with file, signature, code, etc.

## Key Components

### Multi-Source Search

- LSP: Symbol and definition lookup via language server

- Joern: Static code graph queries for method definitions

- Code Indexer: Fast indexed search for function signatures

- AST Grep Tool: Syntax-based fallback search

### Deduplication & Filtering

- Removes duplicate candidates

- Filters by file path, callsite, and function signature

### Candidate Selection

- If one candidate: return directly

- If multiple: use LLM or heuristics to select the best match

### Caching

- Uses Redis to cache results for repeated queries

## Configuration

|Variable|Default|Description|
|---|---|---|
|CGPA_MODEL|o4-mini|LLM model for candidate selection (if enabled)|

### Example Configuration
```bash
# CGPA Settings
CGPA_MODEL=o4-mini
```

## State Structures
```python

# Input: What the agent needs
{
    "fn_name": str,                        # Target function name
    "fn_file_path": Optional[str],         # (Optional) File path for disambiguation
    "caller_file_path": Optional[str],     # (Optional) Caller file path
    "caller_fn_body": Optional[str],       # (Optional) Caller function code
    "callsite_location": Optional[Tuple[int, int]],  # (Optional) Callsite line/col
    "callsite_range": Optional[Tuple[int, int]],     # (Optional) Callsite line range
}

# Output: What the agent produces
{
    "code_dict": FuncInfo,                 # Selected function information
}
```

## Development

### Key Files


```text
mlla/agents/cgpa.py         # Main agent implementation
mlla/utils/context.py       # Global context and configuration
mlla/utils/llm_tools/       # LLM and AST tools
mlla/codeindexer/           # Code indexer and parser
mlla/prompts/cgparser.py    # Prompt templates for candidate selection
```
### Extending the Agent

1. Add New Search Backends: Integrate additional code analysis tools in cgpa.py
2. Improve Deduplication: Enhance filtering logic for more robust candidate selection
3. Enhance LLM Prompts: Update prompts/cgparser.py for better selection strategies
