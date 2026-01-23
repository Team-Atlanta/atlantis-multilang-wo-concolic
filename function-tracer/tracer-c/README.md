## C Function-Level tracer

this tracer traces which function calls other functions

### output format

output format is jsonl

each line has:

```json
{
    "thread_id": "thread id",
    "call_info": {
        "caller": { // This is caller function's information field
            "file": "File location where caller function defined",
            "line": "dummy value",
            "function_name": "caller function name"
        },
        "callees": { // This is information field  callee functions caller called 
            "file": "file path where the caller called",
            "line": "line number where the caller called",
            "callee": { // callee information
                "file": "file path where callee function defined",
                "line": "line number where callee function defined",
                "function_name": "callee function name"
            }
        }
      }
}
```

### output example (for mock-c):

```
{"thread_id": "11", "call_info": {"caller": {"file": "/src/mock-c/mock.c", "line": -1, "function_name": "__libc_csu_init"}, "callees": [{"file": "/src/mock-c/mock.c", "line": 23, "callee": {"file": "/src/fuzz/ossfuzz-1.c", "line": 5, "function_name": "asan.module_ctor"}}]}}
{"thread_id": "11", "call_info": {"caller": {"file": "/src/mock-c/mock.c", "line": -1, "function_name": "__libc_csu_init"}, "callees": [{"file": "/src/mock-c/mock.c", "line": 23, "callee": {"file": "/src/fuzz/ossfuzz-1.c", "line": 5, "function_name": "sancov.module_ctor_8bit_counters"}}]}}
{"thread_id": "11", "call_info": {"caller": {"file": "/src/fuzz/ossfuzz-1.c", "line": -1, "function_name": "LLVMFuzzerTestOneInput"}, "callees": [{"file": "/src/fuzz/ossfuzz-1.c", "line": 4, "callee": {"file": "/src/mock-c/mock.c", "line": 8, "function_name": "target_1"}}]}}
```

### ISSUE

- Need increasing trace stability for crash input
- Need increasing accuracy of caller's call location