from pathlib import Path

import pytest

from mlla.codeindexer.codeindexer import CodeIndexer

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.asyncio
async def test_index_java_project(
    cp_jenkins_path: Path, redis_client, random_project_name: str
) -> None:
    """Test indexing Java project files."""
    code_indexer = CodeIndexer(redis_client)
    await code_indexer.index_project(
        random_project_name, [cp_jenkins_path], "jvm", overwrite=True
    )
    assert code_indexer.cp_name == random_project_name


@pytest.mark.asyncio
async def test_java_function_signatures(
    tmp_path, random_project_name, redis_client
) -> None:
    """Test Java function signature handling."""
    test_file = tmp_path / "Test.java"
    test_file.write_text(
        """public class Test {
    public void method1() {
        System.out.println("test");
    }
}
"""
    )

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )

    # Direct search with full signature
    full_signature = "void Test.method1()"
    results = await code_indexer.search_function(full_signature)
    assert len(results) == 1
    result = results[0]
    assert result.func_name == full_signature  # Key from hash_mapped_data

    # Search with method name (falls back to candidates)
    results = await code_indexer.search_function("method1")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == full_signature  # Full signature from set_mapped_data


@pytest.mark.asyncio
async def test_trailing_whitespace_java(
    tmp_path, random_project_name, redis_client
) -> None:
    """Test handling of trailing whitespace in Java files."""
    java_file = tmp_path / "Test.java"
    content = """


public class Test {
    public void method1() {
        System.out.println("test");
    }


}


"""
    java_file.write_text(content)

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )
    results = await code_indexer.search_function("method1")
    assert len(results) == 1
    result = results[0]

    # Verify function body includes original whitespace
    assert result.func_name == "void Test.method1()"
    assert result.file_path == str(java_file)
    assert result.start_line == 5  # Line number from start of file (1-based)
    assert result.end_line == 7  # Line number from start of file (1-based)
    assert result.func_body.count("\n") >= 2  # At least 2 newlines in body
    assert "System.out.println" in result.func_body


@pytest.mark.asyncio
async def test_nested_functions_inner_class(
    tmp_path, random_project_name, redis_client
) -> None:
    """Test indexing and searching inner (non-static nested) class methods."""
    test_file = tmp_path / "Inner.java"
    test_file.write_text(
        """public class Nested {
    class Inner {
        public void methodA(int x) {
            System.out.println("Inner methodA");
        }
    }
}
"""
    )

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )
    results = await code_indexer.search_function("methodA")
    assert len(results) == 1
    result = results[0]

    assert result.func_name == "void Nested$Inner.methodA(int x)"
    assert result.file_path == str(test_file)
    assert result.start_line == 3  # Line number of inner function start (0-based)
    assert result.end_line == 5  # Line number of inner function end (0-based)
    assert "System.out.println" in result.func_body


@pytest.mark.asyncio
async def test_nested_functions_static_class(
    tmp_path, random_project_name, redis_client
) -> None:
    """Test indexing and searching static nested class methods."""
    test_file = tmp_path / "StaticNested.java"
    test_file.write_text(
        """public class Nested {
    static class StaticInner {
        public void methodB(double y) {
            System.out.println("StaticInner methodB");
        }
    }
}
"""
    )

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )
    results = await code_indexer.search_function("methodB")
    assert len(results) == 1
    result = results[0]

    assert result.func_name == "void Nested$StaticInner.methodB(double y)"
    assert result.file_path == str(test_file)
    assert (
        result.start_line == 3
    )  # Line number of static nested function start (0-based)
    assert result.end_line == 5  # Line number of static nested function end (0-based)
    assert "System.out.println" in result.func_body


@pytest.mark.asyncio
async def test_nested_functions_local_class(
    tmp_path, random_project_name, redis_client
) -> None:
    """Test indexing and searching local class methods (defined inside method)."""
    test_file = tmp_path / "Local.java"
    test_file.write_text(
        """public class Nested {
    public void outer() {
        class LocalClass {
            public void localMethod(String s) {
                System.out.println("LocalClass: " + s);
            }
        }
        LocalClass lc = new LocalClass();
        lc.localMethod("test");
    }
}
"""
    )

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )

    # Test local class method
    local_results = await code_indexer.search_function("localMethod")
    assert len(local_results) == 1
    local = local_results[0]

    assert local.func_name == "void Nested$LocalClass.localMethod(String s)"
    assert local.file_path == str(test_file)
    assert local.start_line == 4  # Line number of local function start (0-based)
    assert local.end_line == 6  # Line number of local function end (0-based)
    assert "System.out.println" in local.func_body

    # Test outer method that contains local class
    outer_results = await code_indexer.search_function("outer")
    assert len(outer_results) == 1
    outer = outer_results[0]

    assert outer.func_name == "void Nested.outer()"
    assert outer.file_path == str(test_file)
    assert outer.start_line == 2  # Line number of outer function start (0-based)
    assert outer.end_line == 10  # Line number of outer function end (0-based)
    assert "class LocalClass" in outer.func_body


@pytest.mark.asyncio
async def test_nested_functions_anonymous_class(
    tmp_path, random_project_name, redis_client
) -> None:
    """Test indexing and searching anonymous class methods."""
    test_file = tmp_path / "Anonymous.java"
    test_file.write_text(
        """public class Nested {
    public void outer() {
        Runnable r = new Runnable() {
            @Override
            public void run() {
                System.out.println("Anonymous run method");
            }
        };
        r.run();
        Runnable r2 = new Runnable() {
            @Override
            public void run2() {
                System.out.println("Anonymous run method");
            }
        };
        r2.run2();
    }
}
"""
    )

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )

    # Test outer method that contains anonymous class
    outer_results = await code_indexer.search_function("outer")
    assert len(outer_results) == 1
    outer = outer_results[0]
    assert outer.func_name == "void Nested.outer()"
    assert outer.file_path == str(test_file)
    assert outer.start_line == 2  # Line number of outer function start (0-based)
    assert outer.end_line == 17  # Line number of outer function end (0-based)
    assert "new Runnable()" in outer.func_body

    # Test anonymous class method
    # Note: We do not care about anonymous classes for now

    anon_results = await code_indexer.search_function("run")
    assert len(anon_results) == 1
    anon_run = anon_results[0]
    assert anon_run.func_name == "void Nested$1.run()"
    assert anon_run.file_path == str(test_file)
    assert anon_run.start_line == 4  # Line number of anon function start (0-based)
    assert anon_run.end_line == 7  # Line number of anon function end (0-based)
    assert 'System.out.println("Anonymous run method"' in anon_run.func_body

    anon_results = await code_indexer.search_function("run2")
    assert len(anon_results) == 1
    anon_run = anon_results[0]
    assert anon_run.func_name == "void Nested$2.run2()"
    assert anon_run.file_path == str(test_file)
    assert anon_run.start_line == 11  # Line number of anon function start (0-based)
    assert anon_run.end_line == 14  # Line number of anon function end (0-based)
    assert 'System.out.println("Anonymous run method"' in anon_run.func_body


@pytest.mark.asyncio
async def test_constructor(tmp_path, random_project_name, redis_client) -> None:
    """Test indexing and searching nested functions."""
    test_file = tmp_path / "Nested.java"
    test_file.write_text(
        """public class MyClass {
    public MyClass() {
    }

    public void regularMethod() {
        System.out.println("This is a regular method.");
    }
}
"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )
    results = await code_indexer.search_function("regularMethod")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "void MyClass.regularMethod()"
    assert result.file_path == str(test_file)
    assert result.start_line == 5  # Line number of inner function start (0-based)
    assert result.end_line == 7  # Line number of inner function end (0-based)
    results = await code_indexer.search_function("MyClass")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "MyClass.MyClass()"
    assert result.file_path == str(test_file)
    assert result.start_line == 2  # Line number of inner function start (0-based)
    assert result.end_line == 3  # Line number of inner function end (0-based)


@pytest.mark.asyncio
async def test_enum_method(tmp_path, random_project_name, redis_client) -> None:
    """Test indexing and searching enum methods."""
    test_file = tmp_path / "Enum.java"
    test_file.write_text(
        """public enum MyEnum {
    ONE, TWO, THREE;

    public void enumMethod() {
        System.out.println("This method is defined inside an enum.");
    }
}
"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )
    results = await code_indexer.search_function("enumMethod")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "void MyEnum.enumMethod()"
    assert result.file_path == str(test_file)
    assert result.start_line == 4  # Line number of inner function start (0-based)
    assert result.end_line == 6  # Line number of inner function end (0-based)


@pytest.mark.asyncio
async def test_interface(tmp_path, random_project_name, redis_client) -> None:
    """Test indexing and searching interface methods."""
    test_file = tmp_path / "Interface.java"
    test_file.write_text(
        """public interface MyInterface {
    void interfaceMethod();

    default int defaultMethod() {
        return 42;
    }
}

public class MyImpl implements MyInterface {
    @Override
    public void interfaceMethod() {
        System.out.println("Implementation of service method");
    }
}
"""
    )
    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )
    results = await code_indexer.search_function("interfaceMethod")
    assert len(results) == 2
    for result in results:
        if result.func_name == "void MyImpl.interfaceMethod()":
            assert result.file_path == str(test_file)
            assert result.start_line == 10
            assert result.end_line == 13
        elif result.func_name == "void MyInterface.interfaceMethod()":
            assert result.file_path == str(test_file)
            assert result.start_line == 2
            assert result.end_line == 2
        else:
            assert False, f"{result.func_name} is an unexpected function name"

    results = await code_indexer.search_function("defaultMethod")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "int MyInterface.defaultMethod()"
    assert result.file_path == str(test_file)
    assert result.start_line == 4  # Line number of inner function start (0-based)
    assert result.end_line == 6  # Line number of inner function end (0-based)


@pytest.mark.asyncio
async def test_type_only_params_java(
    tmp_path, random_project_name, redis_client
) -> None:
    from mlla.utils.coverage import normalize_func_name

    method_decls_and_signatures = [
        (
            """public class SimpleMethod {
    public void simpleMethod(int x, String y);
}""",
            "void SimpleMethod.simpleMethod(int, String)",
        ),
        (
            """public class StaticMethod {
    public static int staticMethod();
}""",
            "int StaticMethod.staticMethod()",
        ),
        (
            """public class GenericMethod {
    public void genericMethod(List<T> list, T value);
}""",
            "void GenericMethod.genericMethod(List<T>, T)",
        ),
        (
            """public class NestedMap {
    private Map<String, List<Integer>> nestedMap();
}""",
            "Map<String, List<Integer>> NestedMap.nestedMap()",
        ),
        (
            """public class ArrayMethod {
    protected void arrayMethod(String[] names, int[] ids);
}""",
            "void ArrayMethod.arrayMethod(String[], int[])",
        ),
        (
            """public class ComplexMethod {
    void complexMethod(List<String> strings, Map<Integer, Map<String, MyClass>> map);
}""",
            (
                "void ComplexMethod.complexMethod(List<String>, Map<Integer,"
                " Map<String, MyClass>>)"
            ),
        ),
    ]

    for method_decl, expected_signature in method_decls_and_signatures:
        method_name = normalize_func_name(expected_signature)
        test_file = tmp_path / f"{method_name}.java"
        test_file.write_text(method_decl)

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )

    for method_decl, expected_signature in method_decls_and_signatures:
        results = await code_indexer.search_function(
            normalize_func_name(method_decl), type_only_params=True
        )
        assert len(results) == 1
        result = results[0]
        assert result.func_name == expected_signature

        results = await code_indexer.search_function(
            normalize_func_name(method_decl), type_only_params=True
        )
        assert len(results) == 1
        result = results[0]
        assert result.func_name == expected_signature
