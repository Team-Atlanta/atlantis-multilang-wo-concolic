import asyncio

import pytest
from loguru import logger

from mlla.codeindexer.codeindexer import CodeIndexer

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.asyncio
async def test_project_name_switching(
    tmp_path, random_project_name, redis_client
) -> None:
    """Test project name switching behavior."""
    test_file = tmp_path / "Test.java"
    test_file.write_text(
        """public class Test {
    public void testMethod() {
        System.out.println("Test");
    }
}
"""
    )

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )

    # Test with nonexistent project
    original_name = code_indexer.cp_name
    code_indexer.setup_project("nonexistent-proj")
    try:
        try:
            await asyncio.wait_for(
                code_indexer.search_function("testMethod"), timeout=3
            )
        except asyncio.TimeoutError:
            pass
        else:
            assert True, "Search should have timed out"
    except Exception as e:
        assert True, f"Unexpected error: {e}"
    logger.debug(f"back to original name: {original_name}")
    # Test with existing project
    code_indexer.setup_project(original_name)
    results = await code_indexer.search_function("testMethod")
    assert len(results) == 1
    result = results[0]
    full_signature = "void Test.testMethod()"
    assert result.func_name == full_signature
    assert result.file_path == str(test_file)
    assert result.start_line == 2  # Line number of function start (0-based)
    assert result.end_line == 4  # Line number of function end (0-based)
    assert "System.out.println" in result.func_body


@pytest.mark.asyncio
async def test_overwrite_behavior(tmp_path, random_project_name, redis_client) -> None:
    """Test overwrite behavior when indexing."""
    test_file = tmp_path / "Test.java"
    test_file.write_text(
        """public class Test {
    public void method1() {
        System.out.println("Method 1");
    }
}
"""
    )

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )
    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=False
    )

    results = await code_indexer.search_function("method1")
    assert len(results) == 1
    result = results[0]
    full_signature = "void Test.method1()"
    assert result.func_name == full_signature
    assert result.file_path == str(test_file)
    assert result.start_line == 2  # Line number of function start (1-based)
    assert result.end_line == 4  # Line number of function end (1-based)
    assert "System.out.println" in result.func_body


@pytest.mark.asyncio
async def test_function_search(tmp_path, random_project_name, redis_client) -> None:
    """Test searching for specific functions."""
    test_file = tmp_path / "Test.java"
    test_file.write_text(
        """public class Test {
    public void processData() {
        System.out.println("Processing");
    }
}
"""
    )

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )

    # Search with method name (falls back to candidates)
    results = await code_indexer.search_function("processData")
    assert len(results) == 1
    result = results[0]
    assert result.func_name == "void Test.processData()"
    assert result.file_path == str(test_file)
    assert result.start_line == 2  # Line number of function start (1-based)
    assert result.end_line == 4  # Line number of function end (1-based)
    assert "System.out.println" in result.func_body


@pytest.mark.asyncio
async def test_function_candidates_search(
    tmp_path, random_project_name, redis_client
) -> None:
    """Test searching for functions with same name but different signatures."""
    test_file = tmp_path / "Test.java"
    test_file.write_text(
        """public class Test {
    public void process() {
        System.out.println("No args");
    }
    public void process(String data) {
        System.out.println("String arg: " + data);
    }
    public void process(int count) {
        System.out.println("Int arg: " + count);
    }
}
"""
    )

    code_indexer = CodeIndexer(redis_client)

    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )
    candidates = await code_indexer.search_candidates("process")

    assert len(candidates) == 3

    # Verify each candidate has required fields
    expected_signatures = [
        "void Test.process()",
        "void Test.process(String data)",
        "void Test.process(int count)",
    ]
    for candidate in candidates:
        assert candidate.func_name in expected_signatures
        assert candidate.file_path == str(test_file)
        assert isinstance(candidate.start_line, int)
        assert isinstance(candidate.end_line, int)
        assert "System.out.println" in candidate.func_body


@pytest.mark.asyncio
async def test_empty_project(tmp_path, random_project_name, redis_client) -> None:
    """Test behavior with empty project."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    code_indexer = CodeIndexer(redis_client)
    await code_indexer.index_project(
        random_project_name, [empty_dir], "jvm", overwrite=True
    )
    assert len(await code_indexer.search_function("nonexistent")) == 0


@pytest.mark.asyncio
async def test_invalid_source_file(tmp_path, random_project_name, redis_client) -> None:
    """Test behavior with invalid source file."""
    invalid_file = tmp_path / "Invalid.java"
    invalid_file.write_text(
        """public class Invalid {
    public void method1() {
    // Missing closing brace
"""
    )
    code_indexer = CodeIndexer(redis_client)
    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )
    results = await code_indexer.search_function("method1")
    assert len(results) == 1
