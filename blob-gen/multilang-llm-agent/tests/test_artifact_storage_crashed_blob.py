"""Test for crashed blob hash checking in artifact_storage.py."""

import hashlib

import pytest

from mlla.utils.artifact_storage import artifact_exists, store_artifact
from tests.dummy_context import DummyContext


@pytest.fixture
def dummy_context():
    with DummyContext() as context:
        context.CRASH_DIR = context.RESULT_DIR / "crashes"
        context.CRASH_DIR.mkdir(parents=True, exist_ok=True)
        context.CRASH_TIMESTAMP_DIR = context.CRASH_DIR / context.timestamp
        context.CRASH_TIMESTAMP_DIR.mkdir(parents=True, exist_ok=True)

        context.BLOBS_OUTPUT_DIR = context.RESULT_DIR / "output" / "blobs"
        context.BLOBS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        context.GENERATORS_DIR = context.RESULT_DIR / "generators"
        context.GENERATORS_DIR.mkdir(parents=True, exist_ok=True)
        context.GENERATORS_TIMESTAMP_DIR = context.GENERATORS_DIR / context.timestamp
        context.GENERATORS_TIMESTAMP_DIR.mkdir(parents=True, exist_ok=True)
        context.GENERATORS_OUTPUT_DIR = context.RESULT_DIR / "output" / "generators"
        context.GENERATORS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        yield context


def test_generator_crashed_blob_existing_hash(dummy_context):
    # Note: generator_hash is truncated to 10 chars in the store_artifact function
    generator_hash = "generator1"  # Already 10 chars
    blob_data = b"test crashed blob data"
    blob_hash = hashlib.md5(blob_data).hexdigest()[:10]
    combined_hash = f"{generator_hash}_{blob_hash}"

    existing_file = dummy_context.BLOBS_OUTPUT_DIR / f"some_prefix_{blob_hash}.blob"
    existing_file.touch()

    found_file = artifact_exists(dummy_context.BLOBS_OUTPUT_DIR, blob_hash)
    assert found_file == existing_file

    result = store_artifact(
        gc=dummy_context,
        agent_name="generator",
        artifact_type="crashed_blob",
        artifact_hash=generator_hash,
        artifact_blob=blob_data,
        iter_cnt=1,
        store_in_output=True,
    )

    assert result is True

    new_file_path = (
        dummy_context.BLOBS_OUTPUT_DIR / f"generator_iter1_{combined_hash}.blob"
    )
    assert not new_file_path.exists()

    timestamp_file_path = (
        dummy_context.CRASH_TIMESTAMP_DIR / f"generator_iter1_{combined_hash}.blob"
    )
    assert timestamp_file_path.exists()

    with open(timestamp_file_path, "rb") as f:
        stored_blob = f.read()
        assert stored_blob == blob_data


def test_generator_crashed_blob_new_hash(dummy_context):
    # Note: generator_hash is truncated to 10 chars in the store_artifact function
    generator_hash = "generator4"  # Already 10 chars
    blob_data = b"new crashed blob data"
    blob_hash = hashlib.md5(blob_data).hexdigest()[:10]
    combined_hash = f"{generator_hash}_{blob_hash}"

    found_file = artifact_exists(dummy_context.BLOBS_OUTPUT_DIR, blob_hash)
    assert found_file is None

    result = store_artifact(
        gc=dummy_context,
        agent_name="generator",
        artifact_type="crashed_blob",
        artifact_hash=generator_hash,
        artifact_blob=blob_data,
        iter_cnt=1,
        store_in_output=True,
    )

    assert result is True

    timestamp_file_path = (
        dummy_context.CRASH_TIMESTAMP_DIR / f"generator_iter1_{combined_hash}.blob"
    )
    output_file_path = (
        dummy_context.BLOBS_OUTPUT_DIR / f"generator_iter1_{combined_hash}.blob"
    )

    assert timestamp_file_path.exists()
    assert output_file_path.exists()

    with open(timestamp_file_path, "rb") as f:
        stored_blob = f.read()
        assert stored_blob == blob_data

    with open(output_file_path, "rb") as f:
        stored_blob = f.read()
        assert stored_blob == blob_data


def test_blobgen_crashed_blob(dummy_context):
    blob_data = b"blobgen crashed blob data"
    blob_hash = hashlib.md5(blob_data).hexdigest()[:10]

    result = store_artifact(
        gc=dummy_context,
        agent_name="blobgen",
        artifact_type="crashed_blob",
        artifact_hash=blob_hash,
        artifact_blob=blob_data,
        artifact_code="def create_payload():\n    return b'test'",
        iter_cnt=1,
        store_in_output=True,
    )

    assert result is True

    timestamp_file_path = (
        dummy_context.CRASH_TIMESTAMP_DIR / f"blobgen_iter1_{blob_hash}.blob"
    )
    output_file_path = (
        dummy_context.BLOBS_OUTPUT_DIR / f"blobgen_iter1_{blob_hash}.blob"
    )

    assert timestamp_file_path.exists()
    assert output_file_path.exists()

    with open(timestamp_file_path, "rb") as f:
        stored_blob = f.read()
        assert stored_blob == blob_data

    with open(output_file_path, "rb") as f:
        stored_blob = f.read()
        assert stored_blob == blob_data
