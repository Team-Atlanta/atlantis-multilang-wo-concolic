"""Unified storage system for artifacts (blobs, generators, mutators)."""

import hashlib
import json
import os
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .attribute_cg import AttributeFuncInfo, get_transition_key
from .bit import BugInducingThing
from .context import GlobalContext
from .run_pov import RunPovResult


def create_metadata(
    cg_name: str,
    harness_name: str,
    src_func: Optional[AttributeFuncInfo] = None,
    dst_func: Optional[AttributeFuncInfo] = None,
    bit_info: Optional[BugInducingThing] = None,
    run_pov_result: Optional[RunPovResult] = None,
) -> Dict[str, Any]:
    """Create metadata dictionary for artifact storage."""
    metadata: Dict = {
        "cg_name": cg_name,
        "harness_name": harness_name,
    }

    if src_func and dst_func:
        metadata["transition_key"] = get_transition_key(src_func, dst_func)

        metadata["src_func"] = {
            "func_name": src_func.func_location.func_name,
            "file_path": str(src_func.func_location.file_path),
            "start_line": src_func.func_location.start_line,
            "end_line": src_func.func_location.end_line,
        }

        metadata["dst_func"] = {
            "func_name": dst_func.func_location.func_name,
            "file_path": str(dst_func.func_location.file_path),
            "start_line": dst_func.func_location.start_line,
            "end_line": dst_func.func_location.end_line,
        }

    if bit_info:
        metadata["BIT"] = bit_info.to_dict()

    if run_pov_result:
        metadata["run_pov_result"] = run_pov_result

    return metadata


def artifact_exists(directory: Path, artifact_hash: str) -> Optional[Path]:
    """Check if an artifact with the given hash already exists in the directory."""
    if not directory.exists():
        return None

    for file_path in directory.iterdir():
        if file_path.is_file() and artifact_hash in file_path.name:
            return file_path

    return None


def load_artifact_metadata(artifact_path: Path) -> Optional[Dict[str, Any]]:
    """Load metadata for an artifact."""
    try:
        # Check for metadata file (.json)
        meta_path = artifact_path.with_suffix(".json")

        if meta_path.exists():
            with open(meta_path, "r") as f:
                metadata = json.load(f)
            return metadata
        else:
            logger.debug(f"No metadata file found for {artifact_path}")
            return None
    except Exception as e:
        logger.error(f"Failed to load metadata for {artifact_path}: {e}")
        return None


def serialize_prompts(prompts: List) -> List[Dict[str, Any]]:
    """Serialize a list of BaseMessage objects to a list of dictionaries."""
    serialized = []
    for message in prompts:
        # Extract role and content from BaseMessage
        role = getattr(message, "type", "unknown")
        content = getattr(message, "content", "")

        # Create a dictionary representation
        serialized.append({"type": role, "content": content})

    return serialized


def store_artifact_files(
    base_path: Path,
    code: Optional[str] = None,
    desc: Optional[str] = None,
    blob: Optional[bytes] = None,
    metadata: Optional[Dict[str, Any]] = None,
    coverage_info: Optional[Dict[str, Any]] = None,
    prompts: Optional[List] = None,
) -> bool:
    """Store artifact files."""
    try:
        # Store code if provided
        if code:
            code_path = base_path.with_suffix(".py")
            with open(code_path, "w") as f:
                f.write(code)

        # Store description if provided
        if desc:
            desc_path = base_path.with_suffix(".txt")
            with open(desc_path, "w") as f:
                f.write(desc)

        # Store binary blob if provided
        if blob:
            blob_path = base_path.with_suffix(".blob")
            with open(blob_path, "wb") as f:
                f.write(blob)

        # Store metadata if provided
        if metadata:
            meta_path = base_path.with_suffix(".json")
            with open(meta_path, "w") as f:
                json.dump(metadata, f, indent=2)

        # Store coverage information if provided
        if coverage_info:
            cov_path = base_path.with_suffix(".cov")
            with open(cov_path, "w") as f:
                json.dump(coverage_info, f, indent=2)

        # Store prompts if provided
        if prompts:
            prompts_path = base_path.with_suffix(".prompts")
            serialized_prompts = serialize_prompts(prompts)
            with open(prompts_path, "w") as f:
                json.dump(serialized_prompts, f, indent=2)

        done_path = base_path.with_suffix(".done")
        done_path.touch()
        assert done_path.is_file()

        return True

    except Exception as e:
        error_msg = f"{e}\n{traceback.format_exc()}"
        logger.error(f"Failed to save artifact {base_path}: {error_msg}")
        return False


def store_artifact(
    gc: GlobalContext,
    agent_name: str,
    artifact_type: str,
    artifact_hash: str,
    artifact_code: Optional[str] = None,
    artifact_desc: Optional[str] = None,
    artifact_blob: Optional[bytes] = None,
    iter_cnt: int = 0,
    src_func: Optional[AttributeFuncInfo] = None,
    dst_func: Optional[AttributeFuncInfo] = None,
    bit_info: Optional[BugInducingThing] = None,
    coverage_info: Optional[Dict[str, Any]] = None,
    run_pov_result: Optional[RunPovResult] = None,
    prompts: Optional[List[Any]] = None,
    store_in_output: bool = False,
) -> bool:
    """Store an artifact (blob, generator, or mutator)."""
    # Validate artifact type
    if artifact_type not in [
        "blob",
        "generator",
        "mutator",
        "crashed_blob",
        "coverage",
    ]:
        logger.error(f"Invalid artifact type: {artifact_type}")
        return False

    # Generate hash if not provided
    if not artifact_hash and artifact_blob:
        artifact_hash = hashlib.md5(artifact_blob).hexdigest()
    elif not artifact_hash and artifact_code:
        artifact_hash = hashlib.md5(artifact_code.encode()).hexdigest()
    elif not artifact_hash:
        logger.error("Cannot generate hash: no code or blob provided")
        return False

    # Special handling for generator agent crashed blobs
    if agent_name == "generator" and artifact_type == "crashed_blob" and artifact_blob:
        generator_hash = artifact_hash[:10]
        blob_hash = hashlib.md5(artifact_blob).hexdigest()[:10]
        artifact_hash = f"{generator_hash}_{blob_hash}"
    else:
        artifact_hash = artifact_hash[:10]

    # Get timestamp directory based on artifact type
    if artifact_type == "crashed_blob":
        timestamp_dir = gc.CRASH_TIMESTAMP_DIR
        output_dir = gc.BLOBS_OUTPUT_DIR if store_in_output else None
    else:
        if agent_name == "blobgen":
            timestamp_dir = gc.BLOBS_TIMESTAMP_DIR
            output_dir = gc.BLOBS_OUTPUT_DIR if store_in_output else None
        elif agent_name == "generator":
            timestamp_dir = gc.GENERATORS_TIMESTAMP_DIR
            output_dir = gc.GENERATORS_OUTPUT_DIR if store_in_output else None
        elif agent_name == "mutator":
            timestamp_dir = gc.MUTATORS_TIMESTAMP_DIR
            output_dir = gc.MUTATORS_OUTPUT_DIR if store_in_output else None

    # Create timestamp directory if it doesn't exist
    timestamp_dir.mkdir(parents=True, exist_ok=True)

    # # Create base path for timestamp directory
    # base_path = timestamp_dir / f"{agent_name}_iter{iter_cnt}_{artifact_hash}"

    no_dedup = os.getenv("ORCHESTRATOR_EVAL_NO_DEDUP", False)
    if not no_dedup:
        # Create base path for timestamp directory
        base_path = timestamp_dir / f"{agent_name}_iter{iter_cnt}_{artifact_hash}"
    else:
        import uuid

        uuid_str = uuid.uuid4()
        base_path = (
            timestamp_dir / f"{agent_name}_iter{iter_cnt}_{artifact_hash}_{uuid_str}"
        )

    # Create metadata
    metadata = create_metadata(
        gc.cp.name, gc.target_harness, src_func, dst_func, bit_info, run_pov_result
    )

    # Coverage should be stored separately, so we do not need the exists check
    if artifact_type == "coverage":
        # Store in timestamp directory
        success = store_artifact_files(
            base_path,
            # code=artifact_code,
            # desc=artifact_desc,
            # blob=artifact_blob,
            metadata=metadata,
            coverage_info=coverage_info,
            prompts=prompts,
        )
        return True

    # Check if artifact already exists
    if not no_dedup:
        existing_file = artifact_exists(timestamp_dir, artifact_hash)
        if existing_file:
            logger.info(
                f"{artifact_type.capitalize()} {artifact_hash} already exists at"
                f" {existing_file}"
            )
            return True

    # Store in timestamp directory
    success = store_artifact_files(
        base_path,
        code=artifact_code,
        desc=artifact_desc,
        blob=artifact_blob,
        metadata=metadata,
        coverage_info=coverage_info,
        prompts=prompts,
    )

    if success:
        logger.info(f"Saved {artifact_type} to {base_path}")

    # Store in output directory if requested and available
    if success and store_in_output and output_dir:
        # Create output directory if it doesn't exist
        output_dir.mkdir(parents=True, exist_ok=True)

        # Create base path for output directory
        output_path = output_dir / f"{agent_name}_iter{iter_cnt}_{artifact_hash}"

        if artifact_type in ["blob", "crashed_blob"]:
            artifact_code = ""
            metadata = {}

        # We already have blob_hash
        if (
            agent_name == "generator"
            and artifact_type == "crashed_blob"
            and artifact_blob
        ):
            # Check if blob_hash already exists
            existing_file = artifact_exists(output_dir, blob_hash)
            if existing_file:
                logger.info(
                    f"{artifact_type.capitalize()} {blob_hash} already exists at"
                    f" {existing_file}"
                )
                return True

        # For output directory, store code, blob, and metadata.
        output_success = store_artifact_files(
            output_path,
            code=artifact_code,
            desc=None,  # Don't store description in output
            blob=artifact_blob,
            metadata=metadata,
            coverage_info=None,  # Don't store coverage in output
            prompts=None,  # Don't store prompt in output
        )

        if output_success:
            logger.info(f"Saved {artifact_type} to output directory at {output_path}")

    return success
