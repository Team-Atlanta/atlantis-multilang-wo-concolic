import traceback
from typing import Dict

from loguru import logger

from mlla.agents.blobgen_agent.state import BlobGenAgentOverallState
from mlla.modules.sanitizer import is_known_crash
from mlla.utils.artifact_storage import store_artifact
from mlla.utils.run_pov import RunPovResult, process_pov_in_docker_async

from ..prompts.coverage_prompt import build_coverage_prompt


async def collect_coverage_node(state: BlobGenAgentOverallState) -> Dict:
    """Collect coverage information by running the payload through test harness."""
    new_state: Dict = {}
    harness_name = state["harness_name"]
    current_payload = state["current_payload"]

    logger.info(f"Collecting coverage for {harness_name}")

    try:
        # Run the payload to collect coverage
        blob_hash = current_payload["blob_hash"]
        blob = current_payload["blob"]

        # logger.info(
        #     f"Running POV for harness {harness_name}\n"
        #     f"blob content: {str(blob)[:1024]}"
        # )

        # Process a single POV in docker environment
        result: RunPovResult = await process_pov_in_docker_async(
            state["gc"],
            harness_name,
            blob_hash,
            blob,
            1,  # idx
            1,  # total_povs
        )

        # Extract values from RunPovResult
        crashed = result["triggered"]
        sanitizer_info = result["triggered_sanitizer"]
        coverage_info = result["coverage"]

        # Update payload with results
        updated_payload = current_payload.copy()
        updated_payload["crashed"] = crashed
        updated_payload["sanitizer_info"] = sanitizer_info
        updated_payload["run_pov_result"] = result
        updated_payload["coverage_info"] = coverage_info

        # Get base AttributeCG from state and update with coverage
        base_attr_cg = state.get("attr_cg")
        if base_attr_cg and coverage_info:
            # Update with this payload's coverage
            base_attr_cg.update_coverage(coverage_info)
            updated_payload["attr_cg"] = base_attr_cg

            coverage_prompts = build_coverage_prompt(base_attr_cg, blob, result)
            new_state["messages"] = state["messages"].copy()
            new_state["messages"].extend(coverage_prompts)

        # Update state based on result
        new_state["current_payload"] = updated_payload

        if crashed:
            logger.info(f"Crash detected: {sanitizer_info}")
            new_state["status"] = "crashed"

            # Add to crashed_blobs if it's not already there
            crashed_blobs = state.get("crashed_blobs", {}).copy()
            crashed_blobs[blob_hash] = updated_payload
            new_state["crashed_blobs"] = crashed_blobs

            artifact_type = "crashed_blob"
            store_in_output = True

            # check sanitizer is in actual target scope
            # if not, we need to do feedback loop again
            if not is_known_crash(sanitizer_info):
                new_state["current_payload"]["crashed"] = False
                new_state["status"] = "success"
                logger.info(f"Keep running for {sanitizer_info}")

        else:
            logger.debug("No crash detected")
            new_state["status"] = "success"

            artifact_type = "coverage"
            store_in_output = False

        # Store the blobs using the new unified artifact storage system
        store_artifact(
            gc=state["gc"],
            agent_name="blobgen",
            artifact_type=artifact_type,
            artifact_hash=blob_hash,
            artifact_code=current_payload.get("code", ""),
            artifact_desc=current_payload.get("desc", ""),
            artifact_blob=blob,
            iter_cnt=state.get("iter_cnt", 0),
            bit_info=state.get("bit"),
            coverage_info=coverage_info,
            run_pov_result=result,
            prompts=state["messages"],
            store_in_output=store_in_output,
        )

    except Exception as e:
        error_msg = f"{e}\n{traceback.format_exc()}"
        logger.error(f"Coverage collection failed: {error_msg}")
        new_state["status"] = "failed"
        new_state["error"] = {
            "phase": "collect_coverage",
            "status": "failed",
            "details": error_msg,
        }

    return new_state
