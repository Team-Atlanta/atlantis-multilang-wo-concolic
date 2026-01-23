"""Module for calling BlobGenAgent from OrchestratorAgent."""

import asyncio
import traceback
from typing import Dict, List

from loguru import logger

from ....utils.attribute_cg import AttributeCG
from ....utils.bit import AnalysisMessages, AnalyzedFunction, BugInducingThing
from ....utils.cg import CG, FuncInfo
from ....utils.context import GlobalContext
from ...blobgen_agent.agent import BlobGenAgent
from ...blobgen_agent.state import BlobGenAgentInputState, BlobGenPayload
from ...orchestrator_agent.state import BlobGenContext


async def process_blobgen_agent(
    gc: GlobalContext, llm, context: BlobGenContext
) -> Dict[str, BlobGenPayload]:
    """Process a single BlobGenAgent context."""
    local_blobgen_results: Dict[str, BlobGenPayload] = {}

    # Create BlobGenAgent instance
    blobgen_agent = BlobGenAgent(gc).compile()

    assert context["attr_cg"]

    # Create input state for BlobGenAgent
    blobgen_input = BlobGenAgentInputState(
        harness_name=context["harness_name"],
        sanitizer=context["sanitizer"],
        cg_name=context["cg_name"],
        attr_cg=context["attr_cg"],
        bit=context["bit"],
        selected_sanitizers=context.get("selected_sanitizers", []),
        run_sanitizer_selection=True,
    )

    try:
        # Run BlobGenAgent
        blobgen_result = await blobgen_agent.ainvoke(blobgen_input)

        # Get payload dict from result
        payload_dict = blobgen_result.get("payload_dict", {})
        if not payload_dict:
            return {}

        local_blobgen_results = payload_dict

        return local_blobgen_results

    except Exception as e:
        error_msg = f"{e}\n{traceback.format_exc()}"
        logger.error(
            f"Error processing BlobGenAgent for {context['cg_name']}: {error_msg}"
        )
        return {}


global_blobgen_semaphore = asyncio.Semaphore(5)


async def process_blobgen_from_contexts(
    gc: GlobalContext,
    llm,
    blobgen_contexts: List[BlobGenContext],
    max_concurrent: int = 5,
) -> Dict[str, BlobGenPayload]:
    """Process BlobGenAgent for multiple contexts concurrently."""
    # Set up semaphore for concurrency control

    # Get number of contexts
    num_contexts = len(blobgen_contexts)
    logger.info(f"Processing {num_contexts} contexts with BlobGenAgent")

    # Create tasks for each context to run asynchronously
    async def process_context(context, idx):
        # Use semaphore for concurrency control
        async with global_blobgen_semaphore:
            logger.info(
                f"Processing context {idx+1}/{num_contexts} with"
                f" BlobGenAgent: {context['cg_name']}"
            )

            # Run BlobGenAgent for this context
            blobgen_result = await process_blobgen_agent(gc, llm, context)

            # Skip if no results
            if not blobgen_result:
                logger.warning(f"No BlobGen results for context {context['cg_name']}")
                return {}

            return blobgen_result

    # Create tasks for all contexts
    tasks = [
        process_context(context, idx) for idx, context in enumerate(blobgen_contexts)
    ]

    # Run all tasks concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    all_blobgen_results = {}

    for result in results:
        if isinstance(result, Exception):
            logger.warning(f"BlobGen context processing task failed: {result}")
            import traceback

            tb_lines = traceback.format_exception(
                type(result), result, result.__traceback__
            )
            logger.warning("".join(tb_lines))
            continue

        all_blobgen_results.update(result)

    logger.info(
        f"Completed BlobGenAgent processing with {len(all_blobgen_results)} results"
    )
    return all_blobgen_results


def create_simple_bit_from_func(
    gc: GlobalContext, dst_func: FuncInfo, sanitizer: str
) -> BugInducingThing:
    """Create a simple BIT from FuncInfo for targeting."""
    harness_name = gc.target_harness

    analysis_message = AnalysisMessages(
        sink_detection=f"Target function: {dst_func.func_location.func_name}",
        vulnerability_classification=sanitizer,
        sanitizer_type=sanitizer,
        key_conditions_report="Function execution target",
    )

    analyzed_function = AnalyzedFunction(
        func_location=dst_func.func_location, func_body=dst_func.func_body or ""
    )

    start_line = dst_func.func_location.start_line
    end_line = dst_func.func_location.end_line

    if end_line > start_line:
        dst_func.func_location.start_line = start_line + 1
        dst_func.func_location.end_line = start_line + 1
    else:
        dst_func.func_location.start_line = start_line
        dst_func.func_location.end_line = start_line

    return BugInducingThing(
        harness_name=harness_name,
        func_location=dst_func.func_location,
        key_conditions=[dst_func.func_location],
        should_be_taken_lines=[dst_func.func_location],
        analysis_message=[analysis_message],
        analyzed_functions=[analyzed_function],
        priority=1,
    )


async def process_blobgen_from_cg(
    gc: GlobalContext,
    cg: CG,
    dst_func: FuncInfo,
    sanitizer: str = "",
) -> Dict[str, BlobGenPayload]:
    """Process BlobGenAgent with simple BIT targeting dst_func.

    Args:
        gc: GlobalContext
        cg: Call Graph containing the source function as root_node
        dst_func: Target function to reach with generated blobs

    Returns:
        Dict of BlobGenPayload results
    """
    logger.info(
        f"Processing CG {cg.name} with BlobGenAgent targeting"
        f" {dst_func.func_location.func_name}"
    )

    # Get harness name from global context
    harness_name = gc.target_harness

    # Derive sanitizer based on language
    if not sanitizer:
        if gc.cp.language == "jvm":
            sanitizer = "jazzer"
        else:
            sanitizer = gc.cp.sanitizers[0]

    # Create simple BIT from dst_func
    import copy

    dst_func = copy.deepcopy(dst_func)
    simple_bit = create_simple_bit_from_func(gc, dst_func, sanitizer)

    try:
        # Create AttributeCG with simple BIT, focus_on_bit=False
        attr_cg = AttributeCG.from_cg(
            cg,
            gc.code_indexer,
            coverage_info=None,
            bit=simple_bit,
            language=gc.cp.language,
            focus_on_bit=False,  # Don't focus specifically on BIT
        )
        # Create BlobGenAgent instance
        blobgen_agent = BlobGenAgent(gc).compile()

        # Create input state with run_sanitizer_selection=False
        blobgen_input = BlobGenAgentInputState(
            harness_name=harness_name,
            sanitizer=sanitizer,
            cg_name=cg.name,
            attr_cg=attr_cg,
            bit=simple_bit,
            selected_sanitizers=[],  # Empty list
            run_sanitizer_selection=False,  # Skip sanitizer selection
        )

        # Run BlobGenAgent
        result = await blobgen_agent.ainvoke(blobgen_input)

        # Process result
        payload_dict = result.get("payload_dict", {})
        if payload_dict:
            logger.info(
                f"BlobGen generated {len(payload_dict)} payloads for CG {cg.name}"
            )
        else:
            logger.info(f"No payloads generated for CG {cg.name}")

        return payload_dict

    except Exception as e:
        error_msg = f"{e}\n{traceback.format_exc()}"
        logger.error(f"Failed to run blobgen agent for CG {cg.name}: {error_msg}")
        return {}
