import subprocess

import pytest

from mlla.codeindexer.codeindexer import CodeIndexer
from mlla.utils.cp import sCP


@pytest.mark.asyncio
async def test_codeindexer_cli_mock_c(redis_client, redis_host, crs_multilang_path):
    """Test code indexing with mock-c project"""
    # Get db index from redis client
    db_index = redis_client.connection_pool.connection_kwargs.get("db", 0)
    # Use existing mock-c project
    test_cp_path = crs_multilang_path / "benchmarks/projects/aixcc/c/mock-c"
    redis = redis_client
    cp, _ = sCP.from_cp_path(test_cp_path)

    # Clean any existing test data
    keys = redis.keys("*mock-c-code-index*")
    if keys:
        redis.delete(*keys)

    try:
        # Run the CLI command
        subprocess.run(
            [
                "python",
                "-m",
                "mlla.codeindexer.main",
                "--cp",
                str(test_cp_path),
                "--redis",
                redis_host,
                "--db-index",
                str(db_index),
            ],
            check=True,
        )

        # Use CodeIndexer's search_function to verify indexing
        indexer = CodeIndexer(redis)
        indexer.setup_project(cp.name)

        # Search and verify target_1 function
        query_1 = "process_input_header"
        results = await indexer.search_function(query_1)
        assert len(results) == 1, f"Expected exactly one result for {query_1} function"
        target1_func = results[0]
        assert (
            target1_func.func_name
            == "void process_input_header(const uint8_t *data, size_t size)"
        )
        assert "if (size > 0 && data[0] == 'A')" in target1_func.func_body
        assert target1_func.file_path.endswith("mock.c")

        # Search and verify target_2 function
        query_2 = "parse_buffer_section"
        results = await indexer.search_function(query_2)
        assert len(results) == 1, f"Expected exactly one result for {query_2} function"
        target2_func = results[0]
        assert (
            target2_func.func_name
            == "void parse_buffer_section(const uint8_t *data, size_t size)"
        )
        assert "uint32_t buf_size = ((uint32_t *)data)[0];" in target2_func.func_body
        assert target2_func.file_path.endswith("mock.c")

    finally:
        # Clean up Redis data
        keys = redis.keys("*mock-c-code-index*")
        # if keys:
        #     redis.delete(*keys)


@pytest.mark.asyncio
async def test_codeindexer_cli_mock_java(redis_client, redis_host, crs_multilang_path):
    """Test code indexing with mock-java project"""
    # Get db index from redis client
    db_index = redis_client.connection_pool.connection_kwargs.get("db", 0)
    # Use existing mock-java project
    test_cp_path = crs_multilang_path / "benchmarks/projects/aixcc/jvm/mock-java"
    redis = redis_client
    cp, _ = sCP.from_cp_path(test_cp_path)

    # Clean any existing test data
    keys = redis.keys("*mock-java-code-index*")
    if keys:
        redis.delete(*keys)

    try:
        # Run the CLI command
        subprocess.run(
            [
                "python",
                "-m",
                "mlla.codeindexer.main",
                "--cp",
                str(test_cp_path),
                "--redis",
                redis_host,
                "--db-index",
                str(db_index),
            ],
            check=True,
        )

        # Use CodeIndexer's search_function to verify indexing
        indexer = CodeIndexer(redis)
        indexer.setup_project(cp.name)

        # Search and verify executeCommand method
        results = await indexer.search_function("executeCommand")
        assert (
            len(results) == 1
        ), "Expected exactly one result for executeCommand method"
        execute_cmd_func = results[0]
        assert (
            execute_cmd_func.func_name
            == "void com.aixcc.mock_java.App.executeCommand(String data)"
        )
        assert (
            "public static void executeCommand(String data)"
            in execute_cmd_func.func_body
        )
        assert execute_cmd_func.file_path.endswith("App.java")

    finally:
        # Clean up Redis data
        keys = redis.keys("*mock-java-code-index*")
        if keys:
            redis.delete(*keys)
