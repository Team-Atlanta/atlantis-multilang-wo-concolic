from pathlib import Path

import pytest

from mlla.codeindexer.codeindexer import CodeIndexer

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.asyncio
async def test_index_c_project(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    """Test indexing C project files."""
    c_file = tmp_path / "test.c"
    c_file.write_text(
        """int add(int a, int b) {
    return a + b;
}
"""
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("add")
    assert len(results) == 1
    result = results[0]

    assert result.func_name == "int add(int a, int b)"
    assert result.file_path == str(c_file)
    assert result.start_line == 1  # Line number of function start (1-based)
    assert result.end_line == 3  # Line number of function end (1-based)
    assert "return a + b;" in result.func_body


@pytest.mark.asyncio
async def test_c_function_names(tmp_path, random_project_name, redis_client) -> None:
    """Test C function name handling."""
    c_file = tmp_path / "test.c"
    c_file.write_text(
        """int add(int a, int b) {
    return a + b;
}
"""
    )

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )

    # C functions use simple names
    results = await code_indexer.search_function("add")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "int add(int a, int b)"  # Simple name for C functions
    assert result.file_path == str(c_file)
    assert result.start_line == 1  # Line number of function start (1-based)
    assert result.end_line == 3  # Line number of function end (1-based)
    assert "return a + b;" in result.func_body


@pytest.mark.asyncio
async def test_trailing_whitespace_c(
    tmp_path, random_project_name, redis_client
) -> None:
    """Test handling of trailing whitespace in C files."""
    c_file = tmp_path / "test.c"
    content = """


int main() {
    printf("test");
    return 0;
}


"""
    c_file.write_text(content)

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("main")
    assert len(results) == 1
    result = results[0]

    # Verify function body includes original whitespace
    assert result.func_name == "int main()"
    assert result.file_path == str(c_file)
    assert result.start_line == 4  # Line number from start of file (1-based)
    assert result.end_line == 7  # Line number from start of file (1-based)
    assert result.func_body.count("\n") >= 2  # At least 2 newlines in body
    assert "printf" in result.func_body


@pytest.mark.asyncio
async def test_same_function_single_file(
    tmp_path, random_project_name, redis_client
) -> None:
    """Test handling of same function defined multiple times in single file
    (e.g., under different compiler flags)."""
    test_file = tmp_path / "test.c"
    test_file.write_text(
        """#ifdef DEBUG
// Debug version
int process(int x) {
    printf("Debug mode\\n");
    return x + 1;
}
#else
// Release version
int process(int x) {
    printf("Release mode\\n");
    return x + 1;
}
#endif
"""
    )

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("process")

    # Note: We do not care about same functions in a single file for now

    # Should find both versions
    assert len(results) == 2
    names = {r.func_name for r in results}
    assert "int process(int x) [#ifdef DEBUG]" in names
    assert "int process(int x) [#ifdef DEBUG -> #else]" in names

    # Both should reference same file
    assert all(r.file_path == str(test_file) for r in results)

    # Should have different implementations
    bodies = {r.func_body for r in results}
    assert any("Debug mode" in body for body in bodies)
    assert any("Release mode" in body for body in bodies)


@pytest.mark.asyncio
async def test_same_function_multiple_files(
    tmp_path, random_project_name, redis_client
) -> None:
    """Test handling of same function defined in multiple files."""
    # Create first file
    file1 = tmp_path / "impl1.c"
    file1.write_text(
        """int process(int x) {
    return x + 1;  // Add implementation
}
"""
    )

    # Create second file
    file2 = tmp_path / "impl2.c"
    file2.write_text(
        """int process(int x) {
    return x * 2;  // Multiply implementation
}
"""
    )

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("process")

    # Note: We do not care about same functions in multiple files for now

    # Should find both implementations?
    assert len(results) == 2

    # Verify both variants are found
    file_paths = {r.file_path for r in results}
    assert str(file1) in file_paths
    assert str(file2) in file_paths

    # Verify different implementations
    bodies = {r.func_body for r in results}
    assert any("x + 1" in body for body in bodies)
    assert any("x * 2" in body for body in bodies)


@pytest.mark.asyncio
async def test_c_macro_function(tmp_path, random_project_name, redis_client) -> None:
    """Test C macro (#272)."""
    test_file = tmp_path / "macro.c"
    test_file.write_text(
        """char * ngx_conf_deprecated(ngx_conf_t *cf, void *post, void *data);
char *ngx_conf_check_num_bounds(ngx_conf_t *cf, void *post, void *data);


#define ngx_get_conf(conf_ctx, module)  conf_ctx[module.index]
"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("ngx_get_conf")

    # Should find macro
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "ngx_get_conf"
    assert result.file_path == str(test_file)
    assert result.start_line == 5
    assert result.end_line == 5
    assert (
        result.func_body
        == "#define ngx_get_conf(conf_ctx, module)  conf_ctx[module.index]\n"
    )


@pytest.mark.asyncio
async def test_c_multiline_macro(tmp_path, random_project_name, redis_client) -> None:
    """Test C macro."""
    test_file = tmp_path / "macro.c"
    test_file.write_text(
        """#define ngx_atomic_cmp_set(lock, old, new)                            \\
    OSAtomicCompareAndSwap32Barrier(old, new, (int32_t *) lock)
"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("ngx_atomic_cmp_set")

    # Should find macro
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "ngx_atomic_cmp_set"
    assert result.file_path == str(test_file)
    assert result.start_line == 1
    assert result.end_line == 2
    assert (
        result.func_body
        == """#define ngx_atomic_cmp_set(lock, old, new)                            \\
    OSAtomicCompareAndSwap32Barrier(old, new, (int32_t *) lock)\n"""
    )


@pytest.mark.asyncio
async def test_c_multiple_macros(tmp_path, random_project_name, redis_client) -> None:
    """Test C macro."""
    test_file = tmp_path / "macro.c"
    test_file.write_text(
        """#define ngx_tm_sec            tm_sec
#define ngx_tm_min            tm_min
"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("ngx_tm_sec")

    # Should find macro
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "ngx_tm_sec"
    assert result.file_path == str(test_file)
    assert result.start_line == 1
    assert result.end_line == 1
    assert result.func_body == "#define ngx_tm_sec            tm_sec\n"

    results = await code_indexer.search_function("ngx_tm_min")
    result = results[0]
    assert result.func_name == "ngx_tm_min"
    assert result.file_path == str(test_file)
    assert result.start_line == 2
    assert result.end_line == 2
    assert result.func_body == "#define ngx_tm_min            tm_min\n"


@pytest.mark.asyncio
async def test_c_empty_macros(tmp_path, random_project_name, redis_client) -> None:
    """Test C macro."""
    test_file = tmp_path / "macro.c"
    test_file.write_text(
        """#define ngx_cpu_pause()
"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("ngx_cpu_pause")

    # Should find macro
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "ngx_cpu_pause"
    assert result.file_path == str(test_file)
    assert result.start_line == 1
    assert result.end_line == 1
    assert result.func_body == "#define ngx_cpu_pause()\n"


@pytest.mark.asyncio
async def test_c_ngx_init_cycle(tmp_path, random_project_name, redis_client) -> None:
    """
    Test ngx_init_cycle reported in #272.
    This is for check a function that returns a pointer
    """
    test_file = tmp_path / "macro.c"
    test_file.write_text(
        """
ngx_cycle_t *
ngx_init_cycle(ngx_cycle_t *old_cycle)
{
    FILE                *fp;

    return NULL;
}
"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("ngx_init_cycle")

    assert len(results) == 1
    result = results[0]
    assert result.func_name == "ngx_cycle_t* ngx_init_cycle(ngx_cycle_t *old_cycle)"
    assert result.file_path == str(test_file)
    assert result.start_line == 2
    assert result.end_line == 8
    assert "ngx_cycle_t *" in result.func_body
    assert "FILE                *fp;" in result.func_body


@pytest.mark.asyncio
async def test_c_return_pointer(tmp_path, random_project_name, redis_client) -> None:
    """Test C macro."""
    test_file = tmp_path / "macro.c"
    test_file.write_text(
        """ngx_cycle_t **
ngx_init_cycle(ngx_cycle_t *old_cycle)
{
    return NULL;
}
"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("ngx_init_cycle")

    # Should find macro
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "ngx_cycle_t** ngx_init_cycle(ngx_cycle_t *old_cycle)"
    assert result.file_path == str(test_file)
    assert result.start_line == 1
    assert result.end_line == 5


@pytest.mark.asyncio
async def test_struct(tmp_path, random_project_name, redis_client) -> None:
    """Test struct"""
    test_file = tmp_path / "struct.c"
    test_file.write_text(
        """struct TestStruct {
            int x;
            int y;
        };"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("TestStruct")

    assert len(results) == 1
    result = results[0]
    assert result.func_name == "TestStruct"
    assert result.file_path == str(test_file)
    assert result.start_line == 1
    assert result.end_line == 4


@pytest.mark.asyncio
async def test_union(tmp_path, random_project_name, redis_client) -> None:
    """Test union"""
    test_file = tmp_path / "union.c"
    test_file.write_text(
        """union TestUnion {
            int a;
            float b;
        };
        """
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("TestUnion")

    assert len(results) == 1
    result = results[0]
    assert result.func_name == "TestUnion"
    assert result.file_path == str(test_file)
    assert result.start_line == 1
    assert result.end_line == 4


@pytest.mark.asyncio
async def test_type_definition(tmp_path, random_project_name, redis_client) -> None:
    """Test type definition"""
    test_file = tmp_path / "typedef.c"
    test_file.write_text("typedef int test_int;")
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("test_int")

    assert len(results) == 1
    result = results[0]
    assert result.func_name == "test_int"
    assert result.file_path == str(test_file)
    assert result.start_line == 1
    assert result.end_line == 1


@pytest.mark.asyncio
async def test_typedef_function_ptr(
    tmp_path, random_project_name, redis_client
) -> None:
    """Test type definition - function pointer"""
    test_file = tmp_path / "typedef.c"
    test_file.write_text(
        """typedef void (*FuncPtr)();

FuncPtr getFuncPtr() {
    return someFunction;
}"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("FuncPtr")

    assert len(results) == 1
    result = results[0]
    assert result.func_name == "FuncPtr"
    assert result.file_path == str(test_file)
    assert result.start_line == 1
    assert result.end_line == 1

    results = await code_indexer.search_function("getFuncPtr")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "FuncPtr getFuncPtr()"
    assert result.file_path == str(test_file)
    assert result.start_line == 3
    assert result.end_line == 5


@pytest.mark.asyncio
async def test_enum(tmp_path, random_project_name, redis_client) -> None:
    """Test enum"""
    test_file = tmp_path / "enum.c"
    test_file.write_text(
        """enum TestEnum {
            VALUE1,
            VALUE2
        };"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("TestEnum")

    assert len(results) == 1
    result = results[0]
    assert result.func_name == "TestEnum"
    assert result.file_path == str(test_file)
    assert result.start_line == 1
    assert result.end_line == 4


@pytest.mark.asyncio
async def test_cpp_inside_member_function(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    """Test indexing C++ class member functions."""
    cpp_file = tmp_path / "calculator.cpp"
    cpp_file.write_text(
        """class Calculator {
public:
    int add(int a, int b) {
        return a + b;
    }
};
"""
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("add")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "int Calculator::add(int a, int b)"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 3
    assert result.end_line == 5
    assert "return a + b;" in result.func_body


@pytest.mark.asyncio
async def test_cpp_outside_member_function(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    """Test indexing C++ class member functions."""
    cpp_file = tmp_path / "calculator.cpp"
    cpp_file.write_text(
        """class Calculator {
public:
    int add(int a, int b);
};

int Calculator::add(int a, int b) {
    return a + b;
}
"""
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("add")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "int Calculator::add(int a, int b)"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 6
    assert result.end_line == 8
    assert "return a + b;" in result.func_body


@pytest.mark.asyncio
async def test_cpp_namespace_function(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    """Test indexing C++ namespaced functions."""
    cpp_file = tmp_path / "math.cpp"
    cpp_file.write_text(
        """namespace Math {
    int add(int a, int b) {
        return a + b;
    }
}
"""
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("add")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "int Math::add(int a, int b)"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 2
    assert result.end_line == 4
    assert "return a + b;" in result.func_body


@pytest.mark.asyncio
async def test_cpp_overridden_functions(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    """Test indexing C++ overridden functions."""
    cpp_file = tmp_path / "base_derived.cpp"
    cpp_file.write_text(
        """class Base {
public:
    virtual void func() {
        // Base implementation
    }
};

class Derived : public Base {
public:
    void func() override {
        // Derived implementation
    }
};
"""
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("func")
    assert len(results) == 2
    base_func = next(r for r in results if "Base implementation" in r.func_body)
    derived_func = next(r for r in results if "Derived implementation" in r.func_body)

    assert base_func is not None
    assert derived_func is not None
    assert base_func.func_name == "void Base::func()"
    assert derived_func.func_name == "void Derived::func()"
    assert base_func.file_path == str(cpp_file)
    assert derived_func.file_path == str(cpp_file)


@pytest.mark.asyncio
async def test_cpp_overloaded_functions(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    """Test indexing C++ overloaded functions."""
    cpp_file = tmp_path / "overloads.cpp"
    cpp_file.write_text(
        """class Base {
public:
int add(int a, int b) {
    return a + b;
}

double add(double a, double b) {
    return a + b;
}
};
"""
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("add")
    assert len(results) == 2
    int_add = next(r for r in results if "int a, int b" in r.func_body)
    double_add = next(r for r in results if "double a, double b" in r.func_body)
    assert int_add is not None
    assert double_add is not None
    assert int_add.func_name == "int Base::add(int a, int b)"
    assert double_add.func_name == "double Base::add(double a, double b)"
    assert int_add.file_path == str(cpp_file)
    assert double_add.file_path == str(cpp_file)
    assert "return a + b;" in int_add.func_body
    assert "return a + b;" in double_add.func_body


@pytest.mark.asyncio
async def test_cpp_namespace_class_function(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    cpp_file = tmp_path / "namespace_class.cpp"
    cpp_file.write_text(
        """namespace Math {
            class Calculator {
            public:
                int add(int a, int b) {
                    return a + b;
                }
            };
        }
        """
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("add")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "int Math::Calculator::add(int a, int b)"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 4
    assert result.end_line == 6
    assert "return a + b;" in result.func_body


@pytest.mark.asyncio
async def test_cpp_nested_namespace(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    cpp_file = tmp_path / "nested.cpp"
    cpp_file.write_text(
        """namespace Outer {
            namespace Inner {
                int add(int a, int b) {
                    return a + b;
                }
            }
        }
        """
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("add")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "int Outer::Inner::add(int a, int b)"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 3
    assert result.end_line == 5
    assert "return a + b;" in result.func_body


@pytest.mark.asyncio
@pytest.mark.xfail(reason="Anonymous namespace should be handled differently")
async def test_cpp_anonymous_namespace(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    cpp_file = tmp_path / "anonymous.cpp"
    cpp_file.write_text(
        """namespace {
            int add(int a, int b) {
                return a + b;
            }
        }
        """
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("add")
    assert len(results) == 1
    result = results[0]
    assert (
        result.func_name == "int Anonymous::add(int a, int b)"
    )  # TODO: require correct name
    assert result.file_path == str(cpp_file)
    assert result.start_line == 2
    assert result.end_line == 4
    assert "return a + b;" in result.func_body


@pytest.mark.asyncio
async def test_cpp_inner_class(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    cpp_file = tmp_path / "inner_class.cpp"
    cpp_file.write_text(
        """class Outer {
        public:
            class Inner {
            public:
                int add(int a, int b) {
                    return a + b;
                }
            };
        };
        """
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("add")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "int Outer::Inner::add(int a, int b)"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 5
    assert result.end_line == 7
    assert "return a + b;" in result.func_body


@pytest.mark.asyncio
async def test_cpp_multiple_scopes(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    cpp_file = tmp_path / "multiple_scopes.cpp"
    cpp_file.write_text(
        """int add(int a, int b) {
            return a + b;
        }
        namespace Math {
            int add(int a, int b) {
                return a + b;
            }
        }
        class Calculator {
        public:
            int add(int a, int b) {
                return a + b;
            }
        };
        """
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("add")
    assert len(results) == 3
    global_add = next(r for r in results if r.func_name == "int add(int a, int b)")
    math_add = next(r for r in results if r.func_name == "int Math::add(int a, int b)")
    calc_add = next(
        r for r in results if r.func_name == "int Calculator::add(int a, int b)"
    )

    assert global_add.start_line == 1
    assert global_add.end_line == 3
    assert math_add.start_line == 5
    assert math_add.end_line == 7
    assert calc_add.start_line == 11
    assert calc_add.end_line == 13


@pytest.mark.asyncio
async def test_cpp_static_member_function(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    cpp_file = tmp_path / "static_member.cpp"
    cpp_file.write_text(
        """class Calculator {
        public:
            static int add(int a, int b) {
                return a + b;
            }
        };
        """
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("add")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "int Calculator::add(int a, int b)"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 3
    assert result.end_line == 5
    assert "return a + b;" in result.func_body


@pytest.mark.asyncio
async def test_cpp_const_member_function(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    cpp_file = tmp_path / "const_member.cpp"
    cpp_file.write_text(
        """class Calculator {
        public:
            int add(int a, int b) const {
                return a + b;
            }
        };
        """
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("add")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "int Calculator::add(int a, int b)"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 3
    assert result.end_line == 5
    assert "return a + b;" in result.func_body


@pytest.mark.asyncio
@pytest.mark.xfail(reason="Not handled yet")
async def test_cpp_friend_function(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    cpp_file = tmp_path / "friend_function.cpp"
    cpp_file.write_text(
        """class MyClass {
        public:
            friend void add(int a, int b) {
                return a + b;
            }
        };
        """
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("add")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "void ::add(int a, int b)"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 3
    assert result.end_line == 5
    assert "return a + b;" in result.func_body


@pytest.mark.asyncio
@pytest.mark.xfail(reason="Macro middle of function not handled yet")
async def test_cpp_macro_in_definition_simple(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    """A test case for issue #329"""
    cpp_file = tmp_path / "issue329.cpp"
    cpp_file.write_text(
        """png_uint_32 (PNGAPI
png_get_uint_32)(png_const_bytep buf)
{
   png_uint_32 uval =
       ((png_uint_32)(*(buf    )) << 24) +
       ((png_uint_32)(*(buf + 1)) << 16) +
       ((png_uint_32)(*(buf + 2)) <<  8) +
       ((png_uint_32)(*(buf + 3))      ) ;

   return uval;
}
        """
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("png_get_uint_32")
    assert len(results) == 1
    result = results[0]
    # Not sure whether the groundtruth function name is appropriate or not
    assert result.func_name == """png_uint_32 png_get_uint_32(png_const_bytep buf)"""
    assert result.file_path == str(cpp_file)
    assert result.start_line == 1
    assert result.end_line == 11
    assert "png_uint_32 uval" in result.func_body


@pytest.mark.asyncio
async def test_cpp_struct_destructor(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    """A test case for issue #329"""
    cpp_file = tmp_path / "issue329.cpp"
    cpp_file.write_text(
        """struct PngObjectHandler {
  png_infop info_ptr = nullptr;
  png_structp png_ptr = nullptr;
  png_infop end_info_ptr = nullptr;
  png_voidp row_ptr = nullptr;
  BufState* buf_state = nullptr;

  ~PngObjectHandler() {
    if (row_ptr)
      png_free(png_ptr, row_ptr);
    if (end_info_ptr)
      png_destroy_read_struct(&png_ptr, &info_ptr, &end_info_ptr);
    else if (info_ptr)
      png_destroy_read_struct(&png_ptr, &info_ptr, nullptr);
    else
      png_destroy_read_struct(&png_ptr, nullptr, nullptr);
    delete buf_state;
  }
};
        """
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("PngObjectHandler")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "PngObjectHandler"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 1
    assert result.end_line == 19
    assert "png_destroy_read_struct" in result.func_body
    assert "png_voidp row_ptr = nullptr;" in result.func_body

    results = await code_indexer.search_function("~PngObjectHandler")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "PngObjectHandler::~PngObjectHandler()"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 8
    assert result.end_line == 18
    assert "png_destroy_read_struct" in result.func_body
    assert "png_voidp row_ptr = nullptr;" not in result.func_body


@pytest.mark.asyncio
async def test_cpp_class_constructor(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    """An additional test case for issue #329"""
    cpp_file = tmp_path / "issue329.cpp"
    cpp_file.write_text(
        """class DynamicString {
private:
    char* data;
    size_t length;

public:
    DynamicString(const char* str) {
        length = std::strlen(str);
        data = new char[length + 1];
        std::strcpy(data, str);
        std::cout << "Constructor: " << str << std::endl;
    }

    ~DynamicString() {
        delete[] data;
        std::cout << "Destructor: " << std::endl;
    }

    void print() const {
        std::cout << data << std::endl;
    }
};
        """
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("DynamicString")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "DynamicString::DynamicString(const char* str)"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 7
    assert result.end_line == 12
    assert "Constructor" in result.func_body
    assert "Destructor" not in result.func_body

    results = await code_indexer.search_function("~DynamicString")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "DynamicString::~DynamicString()"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 14
    assert result.end_line == 17
    assert "Destructor" in result.func_body
    assert "Constructor" not in result.func_body

    results = await code_indexer.search_function("print")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "void DynamicString::print()"
    assert result.file_path == str(cpp_file)
    assert result.start_line == 19
    assert result.end_line == 21
    assert "std::cout << data << std::endl;" in result.func_body


@pytest.mark.asyncio
async def test_function_ptr(tmp_path, random_project_name, redis_client) -> None:
    """#342 Test a function pointer returning function"""
    test_file = tmp_path / "typedef.c"
    test_file.write_text(
        """float** (*getFuncPtr())(int) {
    return someFunction;
}"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )

    results = await code_indexer.search_function("getFuncPtr")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "float** (*)(int) getFuncPtr()"
    assert result.file_path == str(test_file)
    assert result.start_line == 1
    assert result.end_line == 3


@pytest.mark.asyncio
async def test_cpp_complex_function_name(
    code_indexer: CodeIndexer, tmp_path, random_project_name
) -> None:
    """A test case for issue #329"""
    cpp_file = tmp_path / "issue329.cpp"
    cpp_file.write_text(
        """static void (*
get_pixel(png_uint_32 format))(Pixel *p, png_const_voidp pb)
{
   /* The color-map flag is irrelevant here - the caller of the function
    * returned must either pass the buffer or, for a color-mapped image, the
    * correct entry in the color-map.
    */
   if (format & PNG_FORMAT_FLAG_LINEAR)
   {
      if (format & PNG_FORMAT_FLAG_COLOR)
      {
#        ifdef PNG_FORMAT_BGR_SUPPORTED
            if (format & PNG_FORMAT_FLAG_BGR)
            {
               if (format & PNG_FORMAT_FLAG_ALPHA)
               {
#                 ifdef PNG_FORMAT_AFIRST_SUPPORTED
                     if (format & PNG_FORMAT_FLAG_AFIRST)
                        return gp_abgr16;

                     else
#                 endif
                     return gp_bgra16;
               }

               else
                  return gp_bgr16;
            }
#        endif
        }
    }
}
        """
    )

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )
    results = await code_indexer.search_function("get_pixel")
    assert len(results) == 1
    result = results[0]
    assert (
        result.func_name
        == "void (*)(Pixel *p, png_const_voidp pb) get_pixel(png_uint_32 format)"
    )
    assert result.file_path == str(cpp_file)
    assert result.start_line == 1
    assert result.end_line == 32
    assert "return gp_abgr16;" in result.func_body


@pytest.mark.asyncio
async def test_reference_return(tmp_path, random_project_name, redis_client) -> None:
    """Test refereunce return"""
    test_file = tmp_path / "proto.c"
    test_file.write_text(
        """inline const std::string& HttpProto::request() const
    ABSL_ATTRIBUTE_LIFETIME_BOUND {
  // @@protoc_insertion_point(field_get:HttpProto.request)
  return _internal_request();
}"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c++", overwrite=True
    )

    results = await code_indexer.search_function("request")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "std::string& HttpProto::request()"
    assert result.file_path == str(test_file)
    assert result.start_line == 1
    assert result.end_line == 5


@pytest.mark.asyncio
async def test_type_only_params_cpp(
    tmp_path: Path, random_project_name: str, redis_client
) -> None:

    cpp_decls_and_signatures = [
        (
            """void simpleFunction(int x, std::string y){}""",
            "void simpleFunction(int, std::string)",
            "void simpleFunction(int x, std::string y)",
            "simpleFunction",
        ),
        (
            """namespace MyNamespace { int namespacedFunction(double d){} }""",
            "int MyNamespace::namespacedFunction(double)",
            "int MyNamespace::namespacedFunction(double d)",
            "namespacedFunction",
        ),
        (
            """class MyClass { public: void method(char c){} };""",
            "void MyClass::method(char)",
            "void MyClass::method(char c)",
            "method",
        ),
        (
            """template <typename T> T genericFunction(T val){}""",
            "T genericFunction(T)",
            "T genericFunction(T val)",
            "genericFunction",
        ),
        (
            """int* pointerFunction(const std::vector<int>& vec){}""",
            "int* pointerFunction(std::vector<int>&)",
            "int* pointerFunction(const std::vector<int>& vec)",
            "pointerFunction",
        ),
        (
            """void functionWithNoParams(){}""",
            "void functionWithNoParams()",
            "void functionWithNoParams()",
            "functionWithNoParams",
        ),
        (
            """
            struct MyStruct {
                void methodInStruct(float val){}
            };
            """,
            "void MyStruct::methodInStruct(float)",
            "void MyStruct::methodInStruct(float val)",
            "methodInStruct",
        ),
        (
            """
            class OuterClass {
            public:
                class InnerClass {
                public:
                    static void nestedMethod(bool b){}
                };
            };
            """,
            "void OuterClass::InnerClass::nestedMethod(bool)",
            "void OuterClass::InnerClass::nestedMethod(bool b)",
            "nestedMethod",
        ),
        (
            """
            namespace N1 {
                namespace N2 {
                    void deepNamespaceFunc(long l){}
                }
            }
            """,
            "void N1::N2::deepNamespaceFunc(long)",
            "void N1::N2::deepNamespaceFunc(long l)",
            "deepNamespaceFunc",
        ),
        (
            """
            int main(int argc, char* argv[]){}
            """,
            "int main(int, char*[])",
            "int main(int argc, char* argv[])",
            "main",
        ),
        (
            """float** (*getFuncPtrTypeAlias())(int) {
    return func_return_floatpp;
}""",
            "float** (*)(int) getFuncPtrTypeAlias()",
            "float** (*)(int) getFuncPtrTypeAlias()",
            "getFuncPtrTypeAlias",
        ),
        # Cannot handle this case yet
        #         (
        #             """void (*(*complexFuncPtrReturn())(int))(char) {
        #     return int_to_char_func;
        # }""",
        #             "void (*(*)(int))(char) complexFuncPtrReturn()",
        #             "void (*(*)(int))(char) complexFuncPtrReturn()",
        #             "complexFuncPtrReturn",
        #         ),
        (
            """static void (*get_format_ptr(int format))(char *p, const void* pb) {
return pixel_format_handler;
}""",
            "void (*)(char *p, const void* pb) get_format_ptr(int)",
            "void (*)(char *p, const void* pb) get_format_ptr(int format)",
            "get_format_ptr",
        ),
    ]

    for cpp_decl, _, _, expected_func_name in cpp_decls_and_signatures:
        test_file = tmp_path / f"{expected_func_name}.cpp"
        test_file.write_text(cpp_decl)

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "cpp", overwrite=True
    )

    for (
        _,
        expected_signature_type_only,
        expected_signature_full,
        search_query,
    ) in cpp_decls_and_signatures:
        results = await code_indexer.search_function(
            search_query, type_only_params=False
        )
        assert len(results) == 1
        result = results[0]
        assert result.func_name == expected_signature_full

        results = await code_indexer.search_function(
            search_query, type_only_params=True
        )
        assert len(results) == 1
        result = results[0]
        assert result.func_name == expected_signature_type_only
