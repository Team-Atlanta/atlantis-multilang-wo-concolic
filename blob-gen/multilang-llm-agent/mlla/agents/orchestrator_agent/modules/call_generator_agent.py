"""Module for calling GeneratorAgent from OrchestratorAgent."""

import asyncio
import traceback
from typing import Dict, List

from loguru import logger

from ....utils.attribute_cg import AttributeCG
from ....utils.cg import CG, FuncInfo
from ....utils.context import GlobalContext
from ...generator_agent.agent import GeneratorAgent
from ...generator_agent.state import GeneratorAgentInputState
from ...orchestrator_agent.state import BlobGenContext

global_generator_semaphore = asyncio.Semaphore(5)


async def process_generator_from_contexts(
    gc: GlobalContext,
    llm,
    blobgen_contexts: List[BlobGenContext],
    sanitizer: str,
    max_concurrent: int = 5,
) -> Dict:
    """Process GeneratorAgent directly from BlobGenAgent contexts."""
    # Set up semaphore for concurrency control

    # Get number of contexts
    num_contexts = len(blobgen_contexts)
    logger.info(f"Processing {num_contexts} contexts with GeneratorAgent")

    # Create GeneratorAgent instance
    generator_agent = GeneratorAgent(gc).compile()

    # Create tasks for each context to run asynchronously
    async def process_context(context, idx):
        # Use semaphore for concurrency control
        async with global_generator_semaphore:
            logger.info(
                f"Processing context {idx+1}/{num_contexts} with"
                f" GeneratorAgent: {context['cg_name']}"
            )

            # Get attr_cg from context
            attr_cg = context.get("attr_cg")
            if not attr_cg or not attr_cg.root_node or not attr_cg.bit_node:
                logger.warning(f"No valid attr_cg for context {context['cg_name']}")
                return {}

            # Get selected sanitizers (optional)
            selected_sanitizers = context.get("selected_sanitizers", [])
            if not selected_sanitizers:
                logger.warning(
                    f"No selected_sanitizers for context {context['cg_name']}"
                )
                return {}

            # Run GeneratorAgent directly on this context
            try:
                # Prepare generator agent input state
                generator_input = GeneratorAgentInputState(
                    harness_name=context["harness_name"],
                    sanitizer=sanitizer,
                    selected_sanitizers=selected_sanitizers,
                    attr_cg=attr_cg,
                    src_func=attr_cg.root_node,
                    dst_func=attr_cg.bit_node,
                    source_path="",
                    bit=context["bit"],
                )

                # Run generator agent with recursion limit
                result = await generator_agent.ainvoke(
                    generator_input, {"recursion_limit": 100}
                )

                # Process result
                crashed_blobs = result.get("crashed_blobs", {})
                if not crashed_blobs:
                    return {}

                logger.info(f"Generator found {len(crashed_blobs)} crashes.")

                return crashed_blobs

            except Exception as e:
                error_msg = f"{e}\n{traceback.format_exc()}"
                logger.error(
                    f"Failed to run generator agent for {context['cg_name']}:"
                    f" {error_msg}"
                )
                return {}

    # Create tasks for all contexts
    tasks = [
        process_context(context, idx) for idx, context in enumerate(blobgen_contexts)
    ]

    # Run all tasks concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    all_generator_results = {}

    for result in results:
        if isinstance(result, Exception):
            logger.warning(f"Generator task failed: {result}")

            tb_lines = traceback.format_exception(
                type(result), result, result.__traceback__
            )
            logger.warning("".join(tb_lines))
            continue

        all_generator_results.update(result)

    logger.info(
        f"Completed GeneratorAgent processing with {len(all_generator_results)} results"
    )
    return all_generator_results


async def process_generator_from_cg(
    gc: GlobalContext,
    cg: CG,
    dst_func: FuncInfo,
    sanitizer: str = "",
) -> Dict:
    """Process GeneratorAgent directly from a CG and destination function.

    Args:
        gc: GlobalContext
        cg: Call Graph containing the source function as root_node
        dst_func: Target function to reach

    Returns:
        Dict of crashed blobs if any found, empty dict otherwise
    """
    logger.info(f"Processing CG {cg.name} with GeneratorAgent")

    # Get harness name from global context
    harness_name = gc.target_harness

    # Create AttributeCG from CG without BIT
    attr_cg = AttributeCG.from_cg(
        cg,
        gc.code_indexer,
        coverage_info=None,
        bit=None,  # No BIT provided
        language=gc.cp.language,
        focus_on_bit=False,  # Since no BIT
    )

    # Set source and destination functions
    src_func = cg.root_node

    # Derive sanitizer based on language
    if not sanitizer:
        if gc.cp.language == "jvm":
            sanitizer = "jazzer"
        else:
            sanitizer = gc.cp.sanitizers[0]

    try:
        # Create GeneratorAgent instance
        generator_agent = GeneratorAgent(gc).compile()

        # Prepare generator agent input state
        generator_input = GeneratorAgentInputState(
            harness_name=harness_name,
            sanitizer=sanitizer,
            attr_cg=attr_cg,
            src_func=src_func,
            dst_func=dst_func,
            source_path="",
            bit=None,  # No BIT
        )

        # Run generator agent
        result = await generator_agent.ainvoke(
            generator_input, {"recursion_limit": 100}
        )

        # Process result
        crashed_blobs = result.get("crashed_blobs", {})
        if crashed_blobs:
            logger.info(
                f"Generator found {len(crashed_blobs)} crashes for CG {cg.name}"
            )
        else:
            logger.info(f"No crashes found for CG {cg.name}")

        return crashed_blobs

    except Exception as e:
        error_msg = f"{e}\n{traceback.format_exc()}"
        logger.error(f"Failed to run generator agent for CG {cg.name}: {error_msg}")
        return {}
