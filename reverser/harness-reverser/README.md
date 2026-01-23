# Harness Reverser

A sophisticated AI-powered tool for automated reverse engineering of test harnesses and target project to generate structured input specifications using `TestLang`. The system analyzes C/C++ and Java programs to extract input formats and identify potential security vulnerabilities for effective fuzzing.

## Overview

The Harness Reverser is designed to analyze existing test harnesses and generate `TestLang` specifications that describe the expected input structure. It uses a multi-agent AI system powered by Claude models to understand program logic, trace data flow, and identify security-critical code paths.

## Architecture

### Core Components

#### ReverserAgent (`agents/reverser.py`)
The main agent orchestrating the reverse engineering process:
- **Multi-Model Support**: Currently based on Claude-3.7 and Claude-4-Sonnet, but designed for easy extension to support additional models in the future
- **LangGraph Integration**: Implements a state machine with nodes for prompt preparation, LLM invocation, `TestLang` validation, and context updates
- **Error Handling**: Comprehensive error recovery and retry mechanisms

#### Code Analysis Tools (`tools/`)
- **CodeTool**: Language server protocol integration for code navigation and analysis
- **Context Management**: Project structure understanding and file organization
- **FuzzDB Integration**: Coverage information and crash log analysis

### Key Features

#### Structured Output Generation
- **`TestLang` Specification**: Generates formal input structure descriptions
- **Python Code Generation**: Creates custom generators and encoders for complex data structures
- **Validation Pipeline**: Ensures generated `TestLang` is syntactically and semantically correct

#### Security-Focused Analysis
Targets specific vulnerability classes:
- **C/C++ (AddressSanitizer)**: Heap/stack buffer overflows, use-after-free, memory leaks, etc.
- **Java (Jazzer)**: Command injection, path traversal, RCE, memory exhaustion, etc.

#### Advanced Code Understanding
- **Multi-Language Support**: C/C++, Java (Easily extensible to additional languages by adding language parsers)
- **Diff Analysis**: Focused analysis on code changes when diffs are provided
- **Custom Type Resolution**: Handles complex data structures and file formats

## Technical Implementation

### Core Processing Pipeline

#### 1. Prepare Prompt - Intelligent Context Assembly
The preparation phase establishes the foundation for LLM analysis through sophisticated context management:

**Prompt Caching Strategy**:
```python
# Anthropic-specific caching for performance optimization
def cache_anthropic_msg(self, msg: BaseMessage):
    msg.content = [{
        "text": msg.content,
        "type": "text", 
        "cache_control": {"type": "ephemeral", "ttl": "1h"}
    }]
```

**Base Context Assembly**:
- **System Prompts**: Security-focused analysis instructions with sanitizer-specific guidance
- **Grammar Definitions**: TestLang schema with JSON validation rules embedded
- **Example Repository**: Curated examples of successful TestLang specifications
- **Target Code Integration**: Harness code with optional diff analysis for focused investigation

**Static Asset Caching**: Large, reusable components (examples, grammar, system prompts) are cached with 1-hour TTL to reduce API costs and improve response times.

#### 2. Context-Driven Analysis Loop
The core analysis operates through an iterative, LLM-driven investigation process:

**Dynamic Context Building** (`build_context_msgs`):
- **Current State Assessment**: Aggregates existing TestLang, Python generators, and analysis progress
- **Coverage Integration**: Real-time feedback from FuzzDB showing which code paths are being exercised
- **Crash Log Integration**: Incorporates discovered vulnerabilities to redirect focus
- **Code Block Assembly**: Presents relevant source code sections based on LLM requests

**LLM-Driven Code Discovery**:
The LLM actively directs its own investigation through structured requests:
```python
class CodeReference(BaseModel):
    name: str = Field(description="Function, struct, or variable name to analyze")
    file_path: Optional[str] = Field(description="Absolute path for precise location")
    line_num: Optional[int] = Field(description="Specific line for contextual search")
    class_name: Optional[str] = Field(description="Class context for method resolution")
```

**Intelligent Information Filtering**:
- **Relevance Scoring**: Automatically filters out non-security-critical code paths
- **Context Window Management**: Dynamically reduces content when approaching token limits
- **Suppression Mechanisms**: LLM can mark irrelevant warnings or code blocks for removal

**Autonomous Analysis Capabilities**:
- **Symbol Resolution**: Language server integration for precise code navigation
- **Cross-Reference Discovery**: Automatic detection of related functions and data structures
- **Data Flow Tracing**: Follows input processing from entry points to vulnerable operations
- **Vulnerability Pattern Recognition**: Identifies common security anti-patterns in code

#### 3. TestLang Validation and Warning-Guided Refinement
The validation phase employs a comprehensive warning system to guide LLM toward deeper, more accurate analysis:

**Multi-Level Validation**:
- **Syntax Validation**: Rust-based TestLang parser ensures structural correctness
- **Semantic Validation**: Cross-references and field consistency checking
- **Runtime Validation**: Generated Python code execution with random inputs

**Warning-Driven Analysis Enhancement**:
The system defines specific warning types to guide LLM behavior:

**Security-Focused Warnings**:
```python
# Example: Low-severity vulnerability detection
PyTestLangWarning(
    kind="NotSevereSecurityVulnerability",
    message="Python code targets low-severity vulnerabilities. "
            "Search deeper for AddressSanitizer-detectable issues: "
            "heap-use-after-free, buffer-overflow, use-after-scope"
)

# Example: Coverage gap identification  
PyTestLangWarning(
    kind="UncoveredTargetLines", 
    message="Critical lines {line_numbers} never covered by corpus. "
            "Check if TestLang generates inputs reaching these paths"
)
```

**Code Quality Warnings**:
```python
# Example: Validation method weakness
PyTestLangWarning(
    kind="WeakValidateMethod",
    message="Validation relies on manual implementation instead of libraries. "
            "Use established parsers and avoid try-catch for debugging visibility"
)

# Example: Static loop detection
PyTestLangWarning(
    kind="ConstantRangeInPythonCode", 
    message="Detected static range() calls that may hinder fuzzing effectiveness. "
            "Consider randomized ranges for vulnerability triggering"
)
```

**Output Quality Warnings**:
```python
# Example: Generated blob analysis
PyTestLangWarning(
    kind="GeneratedBlob",
    message="Generated blob example (length: {size}): {sample_output}. "
            "Does this structure effectively target the identified vulnerability?"
)
```

#### 4. Context Update and Iteration Control
**State Management**:
- **Progressive Context Enrichment**: Each iteration adds discovered code, coverage data, and analysis insights
- **Warning Suppression**: LLM can mark resolved warnings to focus on remaining issues
- **Trial Management**: Intelligent retry with different strategies (model switching, context reduction)

**Convergence Criteria**:
- **Maximum Iterations**: Cap at 40 attempts with escalating intervention strategies
- **Security Focus**: Prioritizes vulnerability discovery over TestLang completeness

## Tool Integration
- **`TestLang`**: Input specification language (Rust-based)
- **LibCRS**: Challenge management integration
- **Multilspy**: Language server protocol implementation
- **Language Server Protocol**: For precise code navigation and symbol resolution
- **CodeIndexer**: For additional code search capabilities
- **Custom Generators**: Support for complex file format generation
- **FuzzDB**: Coverage and crash analysis
