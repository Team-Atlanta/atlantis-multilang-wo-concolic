# Testlang

Testlang is a hybrid Rust/Python library and schema language for describing, parsing, and manipulating complex input data structures. It is designed for use in fuzzing, reverse engineering, and program analysis workflows, enabling precise modeling of binary and structured data formats.

## Features

- **Schema Language**: Expresses hierarchical, typed data structures with support for custom records, fields, and attributes.
- **Rust Core**: High-performance parsing, validation, and manipulation of Testlang schemas and data.
- **Python Bindings**: Seamless integration with Python via PyO3 and Maturin, enabling scripting and advanced processing.
- **Extensible**: Supports custom encoding/generator logic via Python classes, referenced directly in schema definitions.
- **Postprocessing**: Built-in normalization, validation, and unrolling of schemas for advanced workflows.

## Technical Overview

- **Schema Definition**: Testlang schemas are defined in JSON5, supporting types like integers, floats, strings, bytes, and nested records. Key attributes include `is_partial`, `mode`, and `default_endian`.
- **Rust Modules**: Core logic is implemented in Rust (`src/`), including AST, schema, postprocessing, and utility modules.
- **Python API**: Exposed via `python/testlang/`, allowing schema manipulation and custom logic in Python.
- **Build System**: Uses Cargo for Rust and Maturin for Python packaging. See `pyproject.toml` and `Cargo.toml` for dependencies and build options.

## Example Workflow

Testlang is used in multi-step analysis workflows, such as those described in the `../reverser/harness-reverser/prompts/GRAMMAR` file:

1. **Analyze Harness and Code**: Extract input structure from program harness and diffs.
2. **Document Reasoning**: Write a `<chain_of_thought>` block to explain input processing logic.
3. **Generate/Update Schema**: Output a `<sub_testlang>` block in JSON5, describing new or updated records.
4. **Custom Logic**: Implement complex encodings/generators as Python classes, referenced in the schema.
5. **Iterate**: Refine schema and logic based on further code analysis.

See `../reverser/harness-reverser/prompts/EXAMPLES` for concrete examples of harness analysis and schema generation.

## Usage

### Rust

```rust
use testlang::get_testlang_schema;

fn main() {
    println!("{}", get_testlang_schema());
}
```

### Python

```python
import testlang
# Load and manipulate schemas, use custom processing
```

## References

- Prompt grammar: `../reverser/harness-reverser/prompts/GRAMMAR`
- Example workflows: `../reverser/harness-reverser/prompts/EXAMPLES`
- Example outputs: `../reverser/harness-reverser/answers/`

## License

See repository root for license information.
