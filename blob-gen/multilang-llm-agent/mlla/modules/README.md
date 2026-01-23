# MLLA Modules

This directory contains core modules for the Multi-Language LLM Agent (MLLA) fuzzing system, focusing on vulnerability detection and data structure handling.

## Overview

The modules provide essential functionality for:
- **Sanitizer Detection**: Identifying and categorizing security vulnerabilities from fuzzing output
- **Known Structure Handling**: Managing common data structures that affect LLM-based fuzzing effectiveness

## Modules

### sanitizer.py

**Purpose**: Detects and categorizes security vulnerabilities from various sanitizer outputs, providing human-readable descriptions and exploit guidance.

**Key Features**:
- **Multi-sanitizer support**: Jazzer (Java), AddressSanitizer, MemorySanitizer, ThreadSanitizer, UndefinedBehaviorSanitizer, LeakSanitizer
- **Vulnerability categorization**: Groups findings by human-understandable security categories
- **Exploit guidance**: Loads structured information from YAML files to provide actionable exploit guidance
- **Crash type classification**: Determines if detected crashes represent known security vulnerabilities

**Vulnerability Categories**:
- **Memory Corruption**: heap-buffer-overflow, use-after-free, double-free, stack-buffer-overflow
- **Injection Attacks**: SQL injection, LDAP injection, OS command injection, XPath injection
- **Remote Code Execution**: deserialization, expression language injection, reflective calls
- **Information Disclosure**: uninitialized memory access, memory leaks
- **Denial of Service**: timeout, out-of-memory, stack overflow
  - **Note on Timeout Bugs**: AIxCC introduced unusual timeout bugs that required special handling. These are addressed through specific timeout detection patterns (see [JazzerSanitizer_with_exploit.yaml](sanitizer_info/JazzerSanitizer_with_exploit.yaml#L364) and [AddressSanitizer_with_exploit.yaml](sanitizer_info/AddressSanitizer_with_exploit.yaml#L171))
- **Concurrency Issues**: data races, deadlocks, thread safety violations

**Example: OS Command Injection** (see [JazzerSanitizer_with_exploit.yaml](sanitizer_info/JazzerSanitizer_with_exploit.yaml#L86))

*Description*:
```
OS commands executed with user-controlled input.

Find: Runtime.exec() or ProcessBuilder using user input, including command arrays.
```

```java
String filename = request.getParameter("file");
Runtime.getRuntime().exec("cat " + filename);  // BUG: command injection

// Command array
String[] cmd = {"/bin/sh", "-c", "ls " + filename};  // BUG: shell injection
new ProcessBuilder(cmd).start();

// Direct command
String command = request.getParameter("cmd");
Runtime.getRuntime().exec(command);  // BUG: direct command execution
```

*Exploit Guidance*:
```
1. Locate command execution with user input
2. Execute exact target command "jazze"
```

```java
Runtime.getRuntime().exec("jazze");  // Exact command name required

// OR with ProcessBuilder
new ProcessBuilder("jazze").start();  // Alternative method
```

**Usage**:
```python
from mlla.modules.sanitizer import get_sanitizer_prompt, is_known_crash

# Get structured prompt for sanitizers
prompt = get_sanitizer_prompt(['jazzer', 'address'])

# Check if crash represents known vulnerability
is_vuln = is_known_crash("heap-buffer-overflow")
```

### known_struct.py

**Purpose**: Handles known data structures in fuzzing targets that require special consideration for effective LLM-based fuzzing.

**Key Features**:
- **Structure detection**: Identifies FuzzedDataProvider, ByteBuffer, and custom structures in source code
- **Language-aware processing**: Distinguishes between Java (Jazzer) and C++ (LLVM) contexts
- **Method extraction**: Analyzes which specific methods are used from detected structures
- **Targeted guidance**: Generates structure-specific prompts for better fuzzing effectiveness

#### Supported Structures

**FuzzedDataProvider**
- **Challenge**: Good for traditional fuzzers, problematic for LLMs due to complex data consumption patterns
- **Data consumption behavior**:
  - Consumes primitive types from the back of the data
  - Consumes data types from the beginning
  - Custom behaviors like `consumeInt(min, max)` for bounded values
- **Solution**: We leverage [libFDP](https://github.com/Team-Atlanta/libFDP) to let LLMs encode payloads properly
- **Implementation**: Function mapping table provides only methods related to the source code (selective approach)

*Example libFDP Usage (LLM-generated [BlobGen](../agents/blobgen_agent/README.md) script)*:
```python
<python_payload>
  import libfdp

  def create_payload():
      # Target value we want to test
      target_value = 4
      internal_payload_str = "field1:value1 field2:value2"

      # Create encoder and add values in the same order as they're consumed
      jazzer_encoder = libfdp.JazzerFdpEncoder()
      jazzer_encoder.produce_jint_in_range(target_value, 1, 6)
      jazzer_encoder.produce_remaining_as_jstring(internal_payload_str)

      # Finalize to get the encoded payload
      final_payload = jazzer_encoder.finalize()
      return final_payload
</python_payload>
```

- **Reference**: [Java Fuzzing with Jazzer - Code Intelligence](https://www.code-intelligence.com/blog/java-fuzzing-with-jazzer)

**Java ByteBuffer**
- **Behavior**: Consumes integers in big-endian format
- **Example** (JenkinsTwo vulnerability):
  ```
  Original: b'\r\x00\x00\x00\x01\x00\x00\x00x-evil-backdoor\x00breakin the law\x00jazze'
  Expected: b'\x00\x00\x00\r\x00\x00\x00\x01x-evil-backdoor\x00breakin the law\x00jazze'
  ```

**Custom Structures**
- **ServletFileUpload**: Multipart-based file upload handling (see [servlet_file_upload.py](known_struct_info/servlet_file_upload.py))
  - **Approach**: Function summaries instead of entire source code appear sufficient for handling certain cases
  - **Note**: This approach has not been fully explored and may warrant further investigation
- **Domain-specific structures**: Application-specific data formats requiring special handling

**Usage**:
```python
from mlla.modules.known_struct import get_known_struct_prompts

# Generate prompts for detected structures
source_code = "data.consumeInt(); ByteBuffer.allocate(1024);"
prompts = get_known_struct_prompts(source_code)
```

## Architecture

The modules work together to provide comprehensive fuzzing support:

1. **known_struct.py** analyzes source code to identify challenging data structures
2. **sanitizer.py** processes fuzzing output to detect and categorize vulnerabilities
3. Both modules generate structured prompts to guide LLM behavior for better fuzzing results

## Configuration

- Sanitizer descriptions stored in `sanitizer_info/` directory as YAML files
- Known structure information in `known_struct_info/` subdirectory
- Environment variable `ALLOW_TIMEOUT_BUG` controls timeout bug reporting

## Integration

These modules are core components of the MLLA system, providing essential functionality for:
- Vulnerability detection and classification
- LLM prompt generation for effective fuzzing
- Multi-language fuzzing support (Java, C/C++)
- Structured exploit guidance generation
