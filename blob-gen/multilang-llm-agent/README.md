# Multilang-LLM-Agent (MLLA)

MLLA (Multi-Language LLM Agent) is an advanced, automated vulnerability exploitation system that leverages Large Language Models (LLMs) to generate targeted payloads for fuzzing and vulnerability discovery. It is designed to analyze complex codebases, identify potential weaknesses, and craft payloads to test and confirm them.

## System Architecture

MLLA operates through a sophisticated pipeline of specialized agents that work in concert to analyze, identify, and exploit vulnerabilities. The system can run in two primary modes: a streamlined **Standalone Mode** for general fuzzing and a comprehensive **Full Pipeline Mode** for deep, targeted analysis.

### Workflow Overview

#### **Standalone Mode (Generator Agent Only)**
The goal of standalone mode is to boost up the initial fuzzing process by quickly generating various seeds based on the hardness code.

```
┌─────────────────┐    ┌─────────────────┐    ┌──────────────────┐
│ Harness Code    │───▶│ Generator Agent │───▶│ Generator Script │
│ + Diff (option) │    │ (Standalone)    │    │ + Blobs/Coverage │
└─────────────────┘    └─────────────────┘    └──────────────────┘
```

#### **Full Pipeline Mode**
A multi-stage process for in-depth vulnerability analysis, from initial code understanding to targeted payload generation.

```
                                                         ┌─────────────────┐
                                                         │ CGPA            │
                                       (self-loop)┌─────▶│ (Function Info) │
                                          ┌────┐  │ ┌─── └─────────────────┘
                                          │    │  │ │           ▲│
                                          │    ▼  │ ▼           │▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ CP Project  │───▶│ CPUA        │───▶│ MCGA        │───▶│ BCDA        │
│ (Source)    │    │ (Understand)│    │ (CallGraph) │    │ (BugDetect) │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
                                                                 │
                                                                 │
                                   ┌─────────────────┐           │
                                   │ Orchestrator    │◀──────────┘
                                   │ (Coordinate)    │
                                   └─────────┬───────┘
                   ┌─────────────────────────┼─────────────────────────┐
                   │                         │                         │
                   ▼                         ▼                         ▼
         ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
         │ BlobGen Agent   │       │ Generator Agent │       │ Mutator Agent   │
         │ (Single Blob)   │       │ (Multi-Variant) │       │ (Transitions)   │
         └─────────────────┘       └─────────────────┘       └─────────────────┘
                   │                         │                         │
                   └─────────────────────────┼─────────────────────────┘
                                             ▼
                                   ┌─────────────────┐
                                   │ Final Results   │
                                   │ (Aggregated)    │
                                   └─────────────────┘
```

## Core Components

For detailed information about individual agents, see their respective README files:
- [Orchestrator Agent](mlla/agents/orchestrator_agent/README.md) - Coordinates payload generation agents
- [BlobGen Agent](mlla/agents/blobgen_agent/README.md) - Single binary payload generation
- [Generator Agent](mlla/agents/generator_agent/README.md) - Probabilistic fuzzing with multiple variations
- [Mutator Agent](mlla/agents/mutator_agent/README.md) - Function transition targeting

### Agent Responsibilities

#### **CPUA (CP Understanding Agent)**
- **Purpose**: Analyzes harness files and identifies interesting functions for vulnerability analysis.
- **Process**: Uses LLM to understand code structure, extract entry points, and determine tainted arguments.
- **Output**: List of target functions with metadata (priority, call sites, tainted parameters).
- **Key Features**: Multi-language support, reflection-based analysis, function validation.

#### **[MCGA (Make Call Graph Agent)](mlla/agents/MCGA.md)**
- **Purpose**: Builds detailed call graphs from CPUA's identified target functions.
- **Process**: Traces function call relationships, handles complex call chains and recursion.
- **Output**: Complete call graphs (CGs) representing function call hierarchies.
- **Integration**: Works with Joern and LSP for comprehensive code analysis.

#### **BCDA (Bug Candidate Detection Agent)**
- **Purpose**: Analyzes call graphs to identify potential vulnerability points.
- **Process**: Extracts paths that could lead to bugs, prioritizes based on vulnerability patterns.
- **Output**: Bug Inducing Things (BITs) - prioritized vulnerability candidates with context.
- **Features**: Path extraction, vulnerability pattern recognition, priority assignment.

#### **[Orchestrator Agent](mlla/agents/orchestrator_agent/README.md)**
- **Purpose**: Coordinates execution of three specialized payload generation agents.
- **Process**: Manages concurrent execution, context creation, and result aggregation.
- **Output**: Consolidated results from BlobGen, Generator, and Mutator agents.
- **Features**: Intelligent filtering, resource management, deduplication.

#### **Payload Generation Agents**

**[BlobGen Agent](mlla/agents/blobgen_agent/README.md) (Single Binary Payload Generation)**
- **Purpose**: Generates single binary payloads designed to reach specific vulnerability points.
- **Approach**: Uses LLM-powered Python code generation with iterative improvement based on execution feedback.
- **Self-Evolving**: Continuously learns from coverage data and execution results across iterations.
- **Output**: Creates `create_payload() -> bytes` functions using structured LLM prompts.
- **Use Case**: Direct vulnerability exploitation when you need a single, highly targeted payload.

**[Generator Agent](mlla/agents/generator_agent/README.md) (Probabilistic Fuzzing)**
- **Purpose**: Creates intelligent payload generators that produce multiple payload variations.
- **Approach**: Addresses non-deterministic nature of LLMs through probabilistic success strategies.
- **Probabilistic Fuzzing**: Assumes that if enough variations are generated, at least one will reach the target.
- **Output**: Creates `generate(rnd: random.Random) -> bytes` functions that produce multiple variations.
- **Use Case**: Broad vulnerability exploration and scenarios requiring multiple payload attempts.

**[Mutator Agent](mlla/agents/mutator_agent/README.md) (Function Transition Targeting)**
- **Purpose**: Creates targeted mutation functions for specific function-to-function transitions.
- **Approach**: Addresses LLM context length limitations by focusing on precise transitions.
- **Surgical Precision**: Targets specific source → destination function pairs rather than entire call graphs.
- **Output**: Creates `mutate(rnd: random.Random, seed: bytes) -> bytes` functions for transitions.
- **Use Case**: Complex call path scenarios where BlobGen and Generator agents struggle with context limits.

#### **Tool Agents**
- **[CGPA (Call Graph Parser Agent)](mlla/agents/CGPA.md)**: A specialized tool agent for parsing and analyzing individual functions. It is used by other agents for tasks like function body extraction, parameter analysis, and precise location tracking. It internally uses a suite of tools, including **LSP**, **Joern**, **CodeIndexer**, and **AST-grep**, to find the most accurate function definition.

### Tools and Technologies

#### **Core Information Retriever Tools**

**Code Indexer**
- **Purpose**: Creates searchable indexes of codebases for efficient lookup
- **Usage**: Fast function lookup, symbol resolution, cross-references
- **Integration**: Supports CPUA and MCGA for rapid code navigation

#### **Development and Infrastructure Tools**

**Redis (State Management)**
- **Purpose**: Caching and state management across agent executions
- **Usage**: Stores intermediate results, enables checkpointing and resume functionality
- **Integration**: Used by all agents for state persistence and sharing
- **Setup**: `docker compose up redis`

### Data Flow and Communication

#### **Key Data Structures**
- **Call Graphs (CGs)**: Tree structures representing function call relationships
- **Bug Inducing Things (BITs)**: Vulnerability candidates with priority and context
- **AttributeCGs**: Enhanced call graphs with vulnerability annotations
- **FuncInfo Objects**: Detailed function metadata including location, body, and parameters

#### **Agent Communication Flow**
1. **CPUA → MCGA**: Target functions with metadata and tainted arguments
2. **MCGA → BCDA**: Complete call graphs with function relationships and vulnerable sinks
3. **BCDA → Orchestrator**: Prioritized vulnerability candidates (BITs)
4. **Orchestrator → Payload Agents**: Structured contexts for concurrent payload generation
5. **Payload Agents → Results**: Aggregated and deduplicated final payloads

## Getting Started

### Quick Setup (Recommended)

The easiest way to get started is with the bootstrap script, which automates the setup process.

```bash
# Run the bootstrap script
./scripts/bootstrap.sh
```

This will install all necessary dependencies, set up the Python environment, and clone required repositories. After the script completes, you will need to:

1.  **Set Environment Variables**:
    ```bash
    export LITELLM_KEY="your_key"
    export LITELLM_URL="your_url"
    ```
2.  **Start Services**:
    ```bash
    docker compose up
    ```
    This command starts essential services like Redis.

### Manual Setup

If you prefer a manual installation:

1.  **Clone CRS-multilang**:
    ```bash
    git clone git@github.com:Team-Atlanta/CRS-multilang.git
    cd CRS-multilang
    git submodule update --init --recursive
    pip install pyyaml coloredlogs
    ```
2.  **Set Environment Variables** and **Start Services** as described in the Quick Setup section.
3.  **Install Dependencies**: MLLA uses Poetry for dependency management.
    ```bash
    # Install core dependencies
    poetry install

    # To include optional dependencies for testing and telemetry
    poetry install --with test --with telemetry
    ```

## Usage

### Running in a Docker Environment (Recommended)

This method ensures a consistent and isolated environment, especially for testing.

1.  **Run the Docker Container**:
    The `run.py` script simplifies running MLLA with a target project.
    ```bash
    # This command builds the container and drops you into a shell
    ./run.py run --target aixcc/jvm/mock-java --config ./crs.config --shell
    ```
2.  **Execute MLLA inside the Container**:
    ```bash
    cd ./blob-gen/multilang-llm-agent

    # Run against all harnesses in the project
    python -m mlla.main --cp $SRC

    # Run against a specific harness
    python -m mlla.main --cp $SRC --harness OssFuzz1
    ```
You can also run MLLA with a single command from your host machine:
```bash
# Run with your local MLLA path
./run.py run --target aixcc/jvm/mock-java --config ./crs.config --mlla [your_mlla_path]

# To pass additional arguments to MLLA, add them after --
./run.py run --target aixcc/jvm/mock-java --config ./crs.config --mlla [your_mlla_path] -- --enable-telemetry --project-name "mock-java"
```

### Running in a Local Environment

1.  **Setup Python**: Ensure you have Python 3.10.14 and the necessary build dependencies installed.
2.  **Run MLLA**:
    ```bash
    # Using Poetry
    poetry run mlla --cp <path-to-cp>

    # Or as a Python module
    python -m mlla.main --cp <path-to-cp>
    ```

### Key Command-Line Arguments

-   `--workdir`: Directory for intermediate results (default: `results`).
-   `--output`: Directory for final generated blobs (default: `{workdir}/blobs`).
-   `--redis`: Address of the Redis server (e.g., `localhost:6379`).
-   `--cmd`: Command to run a specific agent or workflow (e.g., `load,cpua,mcga`).

## Tools and Technologies

MLLA integrates several powerful open-source tools for its analysis capabilities:

-   **Joern**: For deep static code analysis and generating Code Property Graphs (CPGs).
-   **Language Server Protocol (LSP)**: For code intelligence features like go-to-definition and find-references.
-   **Ripgrep / AST-grep**: For fast, syntax-aware code searching.
-   **Redis**: For caching, state management, and inter-agent communication.

## Development

### Running Tests

To run the test suite, first install the testing dependencies:
```bash
poetry install --with test
poetry run pytest
```

### Pre-commit Hooks

To ensure code quality and consistency, install the pre-commit hooks:
```bash
pre-commit install --hook-type pre-commit --hook-type pre-push
```

### Telemetry

MLLA supports OpenTelemetry for monitoring and tracing, with support for **Phoenix** and **Traceloop**.

1.  **Install Telemetry Dependencies**:
    ```bash
    poetry install --with telemetry
    ```
2.  **Enable Telemetry at Runtime**:
    ```bash
    # Run with default provider (Phoenix)
    poetry run mlla --cp <path-to-cp> --enable-telemetry
    ```
You can view traces in the Phoenix UI, typically available at `http://localhost:6006`.
