"""Test module for artifact_storage.py."""

import hashlib
import json
from pathlib import Path

import pytest

from mlla.utils.artifact_storage import (
    artifact_exists,
    create_metadata,
    serialize_prompts,
    store_artifact,
    store_artifact_files,
)
from mlla.utils.bit import BugInducingThing
from tests.dummy_context import DummyContext


class MockFuncLocation:
    def __init__(self, func_name: str, file_path: str, start_line: int, end_line: int):
        self.func_name = func_name
        self.file_path = Path(file_path)
        self.start_line = start_line
        self.end_line = end_line

    def model_dump(self):
        return {
            "func_name": self.func_name,
            "file_path": str(self.file_path),
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


class MockAttributeFuncInfo:
    def __init__(self, func_name: str, file_path: str, start_line: int, end_line: int):
        self.func_location = MockFuncLocation(
            func_name, file_path, start_line, end_line
        )

    def model_dump(self):
        return {
            "func_location": self.func_location.model_dump(),
        }


class MockMessage:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


@pytest.fixture
def dummy_context():
    """Create a dummy context for testing."""
    with DummyContext() as context:
        # Set up additional directories needed for testing
        context.GENERATORS_DIR = context.RESULT_DIR / "generators"
        context.GENERATORS_DIR.mkdir(parents=True, exist_ok=True)
        context.GENERATORS_TIMESTAMP_DIR = context.GENERATORS_DIR / context.timestamp
        context.GENERATORS_TIMESTAMP_DIR.mkdir(parents=True, exist_ok=True)
        context.GENERATORS_OUTPUT_DIR = context.RESULT_DIR / "output" / "generators"
        context.GENERATORS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        context.MUTATORS_DIR = context.RESULT_DIR / "mutators"
        context.MUTATORS_DIR.mkdir(parents=True, exist_ok=True)
        context.MUTATORS_TIMESTAMP_DIR = context.MUTATORS_DIR / context.timestamp
        context.MUTATORS_TIMESTAMP_DIR.mkdir(parents=True, exist_ok=True)
        context.MUTATORS_OUTPUT_DIR = context.RESULT_DIR / "output" / "mutators"
        context.MUTATORS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        context.CRASH_DIR = context.RESULT_DIR / "crashes"
        context.CRASH_DIR.mkdir(parents=True, exist_ok=True)
        context.CRASH_TIMESTAMP_DIR = context.CRASH_DIR / context.timestamp
        context.CRASH_TIMESTAMP_DIR.mkdir(parents=True, exist_ok=True)

        # Set up output directories
        context.BLOBS_OUTPUT_DIR = context.RESULT_DIR / "output" / "blobs"
        context.BLOBS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        yield context


@pytest.fixture
def sample_src_func():
    """Create a sample source function for testing."""
    return MockAttributeFuncInfo(
        func_name="source_function",
        file_path="/path/to/source.py",
        start_line=10,
        end_line=20,
    )


@pytest.fixture
def sample_dst_func():
    """Create a sample destination function for testing."""
    return MockAttributeFuncInfo(
        func_name="destination_function",
        file_path="/path/to/destination.py",
        start_line=30,
        end_line=40,
    )


@pytest.fixture
def sample_bit_info(sample_dst_func):
    """Create a sample BIT info for testing."""
    bit = BugInducingThing(
        harness_name="test_harness",
        func_location=sample_dst_func,
        key_conditions=[],
        should_be_taken_lines=[],
        analysis_message=[],
        analyzed_functions=[],
    )
    return bit


@pytest.fixture
def sample_run_pov_result():
    """Create a sample RunPovResult for testing."""
    return {
        "triggered": True,
        "triggered_sanitizer": "address",
        "coverage": {"file1.py": [1, 2, 3], "file2.py": [10, 20, 30]},
        "stdout": "Test stdout",
        "stderr": "Test stderr",
        "returncode": 1,
    }


@pytest.fixture
def sample_coverage_info():
    """Create a sample coverage info for testing."""
    return {
        "file1.py": [1, 2, 3],
        "file2.py": [10, 20, 30],
        "file3.py": [100, 200, 300],
    }


@pytest.fixture
def sample_prompts():
    """Create sample prompts for testing."""
    return [
        MockMessage("system", "System message"),
        MockMessage("user", "User message"),
        MockMessage("assistant", "Assistant message"),
    ]


def test_create_metadata(
    sample_src_func, sample_dst_func, sample_bit_info, sample_run_pov_result
):
    """Test create_metadata function."""
    # Test with all parameters
    metadata = create_metadata(
        cg_name="test_cg",
        harness_name="test_harness",
        src_func=sample_src_func,
        dst_func=sample_dst_func,
        bit_info=sample_bit_info,
        run_pov_result=sample_run_pov_result,
    )

    assert metadata["cg_name"] == "test_cg"
    assert metadata["harness_name"] == "test_harness"
    assert "transition_key" in metadata
    assert metadata["src_func"]["func_name"] == "source_function"
    assert metadata["dst_func"]["func_name"] == "destination_function"
    assert metadata["run_pov_result"] == sample_run_pov_result

    # Test with minimal parameters
    minimal_metadata = create_metadata(
        cg_name="test_cg",
        harness_name="test_harness",
    )

    assert minimal_metadata["cg_name"] == "test_cg"
    assert minimal_metadata["harness_name"] == "test_harness"
    assert "transition_key" not in minimal_metadata
    assert "src_func" not in minimal_metadata
    assert "dst_func" not in minimal_metadata
    assert "BIT" not in minimal_metadata
    assert "run_pov_result" not in minimal_metadata


def test_artifact_exists(dummy_context):
    """Test artifact_exists function."""
    # Create a test file
    test_hash = "test_hash123"
    test_file = dummy_context.BLOBS_TIMESTAMP_DIR / f"test_file_{test_hash}.py"
    test_file.touch()

    # Test finding an existing artifact
    found_file = artifact_exists(dummy_context.BLOBS_TIMESTAMP_DIR, test_hash)
    assert found_file == test_file

    # Test with non-existent artifact
    not_found = artifact_exists(dummy_context.BLOBS_TIMESTAMP_DIR, "nonexistent_hash")
    assert not_found is None

    # Test with non-existent directory
    non_existent_dir = dummy_context.RESULT_DIR / "nonexistent_dir"
    not_found_dir = artifact_exists(non_existent_dir, test_hash)
    assert not_found_dir is None


@pytest.mark.skip(reason="prompt format has been changed.")
def test_serialize_prompts(sample_prompts):
    """Test serialize_prompts function."""
    serialized = serialize_prompts(sample_prompts)

    assert len(serialized) == 3
    assert serialized[0]["role"] == "system"
    assert serialized[0]["content"] == "System message"
    assert serialized[1]["role"] == "user"
    assert serialized[1]["content"] == "User message"
    assert serialized[2]["role"] == "assistant"
    assert serialized[2]["content"] == "Assistant message"


def test_store_artifact_files(dummy_context, sample_prompts):
    """Test store_artifact_files function."""
    base_path = dummy_context.BLOBS_TIMESTAMP_DIR / "test_artifact"

    # Test with all parameters
    result = store_artifact_files(
        base_path=base_path,
        code="def test(): pass",
        desc="Test description",
        blob=b"Test blob data",
        metadata={"test": "metadata"},
        coverage_info={"file.py": [1, 2, 3]},
        prompts=sample_prompts,
    )

    assert result is True
    assert (base_path.with_suffix(".py")).exists()
    assert (base_path.with_suffix(".txt")).exists()
    assert (base_path.with_suffix(".blob")).exists()
    assert (base_path.with_suffix(".json")).exists()
    assert (base_path.with_suffix(".cov")).exists()
    assert (base_path.with_suffix(".prompts")).exists()

    # Check file contents
    with open(base_path.with_suffix(".py"), "r") as f:
        assert f.read() == "def test(): pass"

    with open(base_path.with_suffix(".txt"), "r") as f:
        assert f.read() == "Test description"

    with open(base_path.with_suffix(".blob"), "rb") as f:
        assert f.read() == b"Test blob data"

    with open(base_path.with_suffix(".json"), "r") as f:
        assert json.load(f) == {"test": "metadata"}

    with open(base_path.with_suffix(".cov"), "r") as f:
        assert json.load(f) == {"file.py": [1, 2, 3]}

    with open(base_path.with_suffix(".prompts"), "r") as f:
        prompts_data = f.read()
        assert len(prompts_data) > 0


def test_store_generator_artifact(
    dummy_context,
    sample_src_func,
    sample_dst_func,
    sample_bit_info,
    sample_coverage_info,
):
    """Test store_artifact function for generator agent."""
    generator_code = "def generate(rnd):\n    return b'test'"
    generator_hash = hashlib.md5(generator_code.encode()).hexdigest()[:10]

    # Store generator artifact
    result = store_artifact(
        gc=dummy_context,
        agent_name="generator",
        artifact_type="generator",
        artifact_hash=generator_hash,
        artifact_code=generator_code,
        artifact_desc="Test generator",
        iter_cnt=1,
        src_func=sample_src_func,
        dst_func=sample_dst_func,
        bit_info=sample_bit_info,
        store_in_output=True,
    )

    assert result is True

    # Check files in timestamp directory
    timestamp_base_path = (
        dummy_context.GENERATORS_TIMESTAMP_DIR / f"generator_iter1_{generator_hash}"
    )
    assert timestamp_base_path.with_suffix(".py").exists()
    assert timestamp_base_path.with_suffix(".txt").exists()
    assert timestamp_base_path.with_suffix(".json").exists()

    # Check files in output directory
    output_base_path = (
        dummy_context.GENERATORS_OUTPUT_DIR / f"generator_iter1_{generator_hash}"
    )
    assert output_base_path.with_suffix(".py").exists()
    assert output_base_path.with_suffix(".json").exists()

    # Check content of files
    with open(timestamp_base_path.with_suffix(".py"), "r") as f:
        assert f.read() == generator_code

    with open(timestamp_base_path.with_suffix(".json"), "r") as f:
        metadata = json.load(f)
        assert metadata["cg_name"] == dummy_context.cp.name
        assert metadata["src_func"]["func_name"] == "source_function"
        assert metadata["dst_func"]["func_name"] == "destination_function"

    # Save file modification times before storing coverage
    py_mtime_before = timestamp_base_path.with_suffix(".py").stat().st_mtime
    txt_mtime_before = timestamp_base_path.with_suffix(".txt").stat().st_mtime
    # json_mtime_before = timestamp_base_path.with_suffix(".json").stat().st_mtime

    # Save file contents before storing coverage
    with open(timestamp_base_path.with_suffix(".py"), "r") as f:
        py_content_before = f.read()

    with open(timestamp_base_path.with_suffix(".txt"), "r") as f:
        txt_content_before = f.read()

    # with open(timestamp_base_path.with_suffix(".json"), "r") as f:
    #     json_content_before = json.load(f)

    # Test storing coverage for generator
    coverage_result = store_artifact(
        gc=dummy_context,
        agent_name="generator",
        artifact_type="coverage",
        artifact_hash=generator_hash,
        coverage_info=sample_coverage_info,
        iter_cnt=1,
    )

    assert coverage_result is True

    # Check coverage file exists with the same hash as the generator
    coverage_path = (
        dummy_context.GENERATORS_TIMESTAMP_DIR / f"generator_iter1_{generator_hash}.cov"
    )
    assert coverage_path.exists()

    # Check content of coverage file
    with open(coverage_path, "r") as f:
        coverage_data = json.load(f)
        assert coverage_data == sample_coverage_info

    # Verify that the original files were not modified
    py_mtime_after = timestamp_base_path.with_suffix(".py").stat().st_mtime
    txt_mtime_after = timestamp_base_path.with_suffix(".txt").stat().st_mtime
    # json_mtime_after = timestamp_base_path.with_suffix(".json").stat().st_mtime

    # Check that file modification times haven't changed
    assert (
        py_mtime_before == py_mtime_after
    ), "Python file was modified after storing coverage"
    assert (
        txt_mtime_before == txt_mtime_after
    ), "Text file was modified after storing coverage"
    # JSON file store metadata and it can be modified when storing coverage
    # assert (
    #     json_mtime_before == json_mtime_after
    # ), "JSON file was modified after storing coverage"

    # Double-check file contents haven't changed
    with open(timestamp_base_path.with_suffix(".py"), "r") as f:
        assert (
            f.read() == py_content_before
        ), "Python file content changed after storing coverage"

    with open(timestamp_base_path.with_suffix(".txt"), "r") as f:
        assert (
            f.read() == txt_content_before
        ), "Text file content changed after storing coverage"

    # JSON file store metadata and it can be modified when storing coverage
    # with open(timestamp_base_path.with_suffix(".json"), "r") as f:
    #     assert (
    #         json.load(f) == json_content_before
    #     ), "JSON file content changed after storing coverage"


def test_store_blobgen_artifact(
    dummy_context, sample_bit_info, sample_coverage_info, sample_run_pov_result
):
    """Test store_artifact function for blobgen agent."""
    # Test storing a regular blob
    blob_code = "def create_payload():\n    return b'test'"
    blob_data = b"test blob data"
    blob_hash = hashlib.md5(blob_data).hexdigest()[:10]

    result = store_artifact(
        gc=dummy_context,
        agent_name="blobgen",
        artifact_type="blob",
        artifact_hash=blob_hash,
        artifact_code=blob_code,
        artifact_desc="Test blob",
        artifact_blob=blob_data,
        iter_cnt=1,
        bit_info=sample_bit_info,
        store_in_output=True,
    )

    assert result is True

    # Check files in timestamp directory
    timestamp_base_path = (
        dummy_context.BLOBS_TIMESTAMP_DIR / f"blobgen_iter1_{blob_hash}"
    )
    assert timestamp_base_path.with_suffix(".py").exists()
    assert timestamp_base_path.with_suffix(".txt").exists()
    assert timestamp_base_path.with_suffix(".blob").exists()
    assert timestamp_base_path.with_suffix(".json").exists()

    # Check files in output directory
    output_base_path = dummy_context.BLOBS_OUTPUT_DIR / f"blobgen_iter1_{blob_hash}"
    assert not output_base_path.with_suffix(".py").exists()
    assert output_base_path.with_suffix(".blob").exists()
    assert not output_base_path.with_suffix(".json").exists()

    # Save file modification times before storing coverage
    py_mtime_before = timestamp_base_path.with_suffix(".py").stat().st_mtime
    txt_mtime_before = timestamp_base_path.with_suffix(".txt").stat().st_mtime
    blob_mtime_before = timestamp_base_path.with_suffix(".blob").stat().st_mtime
    # json_mtime_before = timestamp_base_path.with_suffix(".json").stat().st_mtime

    # Save file contents before storing coverage
    with open(timestamp_base_path.with_suffix(".py"), "r") as f:
        py_content_before = f.read()

    with open(timestamp_base_path.with_suffix(".txt"), "r") as f:
        txt_content_before = f.read()

    with open(timestamp_base_path.with_suffix(".blob"), "rb") as f:
        blob_content_before = f.read()

    # with open(timestamp_base_path.with_suffix(".json"), "r") as f:
    #     json_content_before = json.load(f)

    # Test storing coverage for blobgen
    coverage_result = store_artifact(
        gc=dummy_context,
        agent_name="blobgen",
        artifact_type="coverage",
        artifact_hash=blob_hash,
        coverage_info=sample_coverage_info,
        iter_cnt=1,
    )

    assert coverage_result is True

    # Check coverage file exists with the same hash as the blob
    coverage_path = dummy_context.BLOBS_TIMESTAMP_DIR / f"blobgen_iter1_{blob_hash}.cov"
    assert coverage_path.exists()

    # Check content of coverage file
    with open(coverage_path, "r") as f:
        coverage_data = json.load(f)
        assert coverage_data == sample_coverage_info

    # Verify that the original files were not modified
    py_mtime_after = timestamp_base_path.with_suffix(".py").stat().st_mtime
    txt_mtime_after = timestamp_base_path.with_suffix(".txt").stat().st_mtime
    blob_mtime_after = timestamp_base_path.with_suffix(".blob").stat().st_mtime
    # json_mtime_after = timestamp_base_path.with_suffix(".json").stat().st_mtime

    # Check that file modification times haven't changed
    assert (
        py_mtime_before == py_mtime_after
    ), "Python file was modified after storing coverage"
    assert (
        txt_mtime_before == txt_mtime_after
    ), "Text file was modified after storing coverage"
    assert (
        blob_mtime_before == blob_mtime_after
    ), "Blob file was modified after storing coverage"

    # JSON file store metadata and it can be modified when storing coverage
    # assert (
    #     json_mtime_before == json_mtime_after
    # ), "JSON file was modified after storing coverage"

    # Double-check file contents haven't changed
    with open(timestamp_base_path.with_suffix(".py"), "r") as f:
        assert (
            f.read() == py_content_before
        ), "Python file content changed after storing coverage"

    with open(timestamp_base_path.with_suffix(".txt"), "r") as f:
        assert (
            f.read() == txt_content_before
        ), "Text file content changed after storing coverage"

    with open(timestamp_base_path.with_suffix(".blob"), "rb") as f:
        assert (
            f.read() == blob_content_before
        ), "Blob file content changed after storing coverage"

    # JSON file store metadata and it can be modified when storing coverage
    # with open(timestamp_base_path.with_suffix(".json"), "r") as f:
    #     assert (
    #         json.load(f) == json_content_before
    #     ), "JSON file content changed after storing coverage"


def test_store_crashed_blob(
    dummy_context, sample_bit_info, sample_coverage_info, sample_run_pov_result
):
    """Test store_artifact function for crashed blobs."""
    # Test storing a crashed blob from blobgen agent
    blob_data = b"crashed blob data"
    blob_hash = hashlib.md5(blob_data).hexdigest()[:10]

    result = store_artifact(
        gc=dummy_context,
        agent_name="blobgen",
        artifact_type="crashed_blob",
        artifact_hash=blob_hash,
        artifact_code="def create_payload():\n    return b'crash'",
        artifact_desc="Test crashed blob",
        artifact_blob=blob_data,
        iter_cnt=1,
        bit_info=sample_bit_info,
        coverage_info=sample_coverage_info,
        run_pov_result=sample_run_pov_result,
        store_in_output=True,
    )

    assert result is True

    # Check files in crash timestamp directory
    crash_base_path = dummy_context.CRASH_TIMESTAMP_DIR / f"blobgen_iter1_{blob_hash}"
    assert crash_base_path.with_suffix(".py").exists()
    assert crash_base_path.with_suffix(".txt").exists()
    assert crash_base_path.with_suffix(".blob").exists()
    assert crash_base_path.with_suffix(".json").exists()
    assert crash_base_path.with_suffix(".cov").exists()

    # Check files in output directory
    output_base_path = dummy_context.BLOBS_OUTPUT_DIR / f"blobgen_iter1_{blob_hash}"
    assert not output_base_path.with_suffix(".py").exists()
    assert output_base_path.with_suffix(".blob").exists()
    assert not output_base_path.with_suffix(".json").exists()

    # Test storing a crashed blob from generator agent
    # For generator agent, we pass the generator hash and the blob data
    # The function will calculate the blob hash and combine them
    generator_hash = "generator1"  # Use the same hash as in the logs
    generator_blob_data = b"generator crashed blob data"
    blob_hash = hashlib.md5(generator_blob_data).hexdigest()[:10]
    combined_hash = f"{generator_hash}_{blob_hash}"

    generator_result = store_artifact(
        gc=dummy_context,
        agent_name="generator",
        artifact_type="crashed_blob",
        artifact_hash=generator_hash,  # Just the generator hash
        artifact_desc="Test generator crashed blob",
        artifact_blob=generator_blob_data,  # Blob data
        iter_cnt=1,
        bit_info=sample_bit_info,
        coverage_info=sample_coverage_info,
        run_pov_result=sample_run_pov_result,
        store_in_output=True,
    )

    assert generator_result is True

    # Check files in crash timestamp directory with combined hash
    generator_crash_base_path = (
        dummy_context.CRASH_TIMESTAMP_DIR / f"generator_iter1_{combined_hash}"
    )
    # Description should be stored in timestamp directory
    assert generator_crash_base_path.with_suffix(".txt").exists()
    assert generator_crash_base_path.with_suffix(".blob").exists()
    assert generator_crash_base_path.with_suffix(".json").exists()
    assert generator_crash_base_path.with_suffix(".cov").exists()

    # Check files in output directory with combined hash
    generator_output_base_path = (
        dummy_context.BLOBS_OUTPUT_DIR / f"generator_iter1_{combined_hash}"
    )
    # Description should NOT be stored in output directory
    assert not generator_output_base_path.with_suffix(".txt").exists()
    assert generator_output_base_path.with_suffix(".blob").exists()
    assert not generator_output_base_path.with_suffix(".json").exists()


def test_store_mutator_artifact(
    dummy_context, sample_src_func, sample_dst_func, sample_bit_info
):
    """Test store_artifact function for mutator agent."""
    mutator_code = "def mutate(src_code, dst_code):\n    return dst_code"
    mutator_hash = hashlib.md5(mutator_code.encode()).hexdigest()[:10]

    # Store mutator artifact
    result = store_artifact(
        gc=dummy_context,
        agent_name="mutator",
        artifact_type="mutator",
        artifact_hash=mutator_hash,
        artifact_code=mutator_code,
        artifact_desc="Test mutator",
        iter_cnt=1,
        src_func=sample_src_func,
        dst_func=sample_dst_func,
        bit_info=sample_bit_info,
        store_in_output=True,
    )

    assert result is True

    # Check files in timestamp directory
    timestamp_base_path = (
        dummy_context.MUTATORS_TIMESTAMP_DIR / f"mutator_iter1_{mutator_hash}"
    )
    assert timestamp_base_path.with_suffix(".py").exists()
    assert timestamp_base_path.with_suffix(".txt").exists()
    assert timestamp_base_path.with_suffix(".json").exists()

    # Check files in output directory
    output_base_path = (
        dummy_context.MUTATORS_OUTPUT_DIR / f"mutator_iter1_{mutator_hash}"
    )
    assert output_base_path.with_suffix(".py").exists()
    assert output_base_path.with_suffix(".json").exists()

    # Check content of files
    with open(timestamp_base_path.with_suffix(".py"), "r") as f:
        assert f.read() == mutator_code

    with open(timestamp_base_path.with_suffix(".json"), "r") as f:
        metadata = json.load(f)
        assert metadata["cg_name"] == dummy_context.cp.name
        assert metadata["src_func"]["func_name"] == "source_function"
        assert metadata["dst_func"]["func_name"] == "destination_function"
        assert "transition_key" in metadata


def test_invalid_artifact_type(dummy_context):
    """Test store_artifact function with invalid artifact type."""
    result = store_artifact(
        gc=dummy_context,
        agent_name="generator",
        artifact_type="invalid_type",
        artifact_hash="test_hash",
        artifact_code="def test(): pass",
        iter_cnt=1,
    )

    assert result is False
