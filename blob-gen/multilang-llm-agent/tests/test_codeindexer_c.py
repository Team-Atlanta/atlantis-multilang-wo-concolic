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
        random_project_name, [tmp_path], "c", overwrite=True
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
        random_project_name, [tmp_path], "c", overwrite=True
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
        random_project_name, [tmp_path], "c", overwrite=True
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
        random_project_name, [tmp_path], "c", overwrite=True
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
        random_project_name, [tmp_path], "c", overwrite=True
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
        random_project_name, [tmp_path], "c", overwrite=True
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
        random_project_name, [tmp_path], "c", overwrite=True
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
        random_project_name, [tmp_path], "c", overwrite=True
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
        random_project_name, [tmp_path], "c", overwrite=True
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
        random_project_name, [tmp_path], "c", overwrite=True
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
        random_project_name, [tmp_path], "c", overwrite=True
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
        random_project_name, [tmp_path], "c", overwrite=True
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
        random_project_name, [tmp_path], "c", overwrite=True
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
        random_project_name, [tmp_path], "c", overwrite=True
    )
    results = await code_indexer.search_function("test_int")

    assert len(results) == 1
    result = results[0]
    assert result.func_name == "test_int"
    assert result.file_path == str(test_file)
    assert result.start_line == 1
    assert result.end_line == 1


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
        random_project_name, [tmp_path], "c", overwrite=True
    )
    results = await code_indexer.search_function("TestEnum")

    assert len(results) == 1
    result = results[0]
    assert result.func_name == "TestEnum"
    assert result.file_path == str(test_file)
    assert result.start_line == 1
    assert result.end_line == 4


@pytest.mark.asyncio
async def test_c_rpn_calculator(tmp_path, random_project_name, redis_client) -> None:
    test_file = tmp_path / "rpn_calculator.c"
    test_file.write_text(
        r"""#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#include <stddef.h>
#include <stdbool.h>
#include <wchar.h>
#include <locale.h>
#include <uchar.h>
#include <err.h>

// total array size
#define STACK_SIZE 0x20
// size of child arrays
#define CHILD_SIZE (STACK_SIZE / 2)
// start index of stack
#define START CHILD_SIZE
// end index of stack
#define END STACK_SIZE

// Not best practice, but convenient for main_loop
#define two_arg_operand(expr, a) do {                       \
        check_value( r2 = pop(a.values, &a.idx),            \
                     "r2 pop: no more elements on stack" ); \
        check_value( r1 = pop(a.values, &a.idx),            \
                     "r1 pop: no more elements on stack" ); \
        expr;                                               \
        check_value( push(a.values, &a.idx, r3),            \
                     "Push: ran out of stack space" );      \
        dump(a.values, a.idx - 1);                          \
    } while (0);

#define one_arg_operand(expr, a) do {                       \
        check_value( r1 = pop(a.values, &a.idx),            \
                     "r1 pop: no more elements on stack" ); \
        expr;                                               \
        check_value( push(a.values, &a.idx, r3),            \
                     "Push: ran out of stack space" );      \
        dump(a.values, a.idx - 1);                          \
    } while (0);

// NOTE: don't abort on failure, due to fuzzer harness requirement
int myerrno = 0;
"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "c", overwrite=True
    )
    results = await code_indexer.search_function("two_arg_operand")

    assert len(results) == 1
    result = results[0]
    print(result.func_name)
    print(result.func_body)
    assert result.func_name == "two_arg_operand"
    assert result.file_path == str(test_file)
    assert result.start_line == 22
    assert result.end_line == 31

    results = await code_indexer.search_function("one_arg_operand")

    assert len(results) == 1
    result = results[0]
    assert result.func_name == "one_arg_operand"
    assert result.file_path == str(test_file)
    assert result.start_line == 33
    assert result.end_line == 40
