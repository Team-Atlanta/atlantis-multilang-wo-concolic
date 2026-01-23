import argparse
import asyncio
from pathlib import Path

from mlla.agents.cgpa import CGParserAgent, CGParserInputState
from mlla.utils.context import GlobalContext


def parse_args():
    parser = argparse.ArgumentParser(description="Run CGParser Agent")
    parser.add_argument(
        "--cp", type=str, required=True, help="Path to the CP directory"
    )
    parser.add_argument(
        "--fn-name",
        type=str,
        default="format.unmarshal(dataIn)",
        help="Function name to parse",
    )
    parser.add_argument(
        "--fn-file-path", type=str, default=None, help="File path of the function"
    )
    parser.add_argument(
        "--caller-file-path", type=str, default=None, help="File path of the caller"
    )
    parser.add_argument(
        "--caller-fn-body", type=str, default=None, help="Function body of the caller"
    )
    parser.add_argument(
        "--caller-location",
        type=str,
        default=None,
        help="Location of the caller (format: line,column)",
    )
    parser.add_argument(
        "--callee-range",
        type=str,
        default=None,
        help="Range of the callee (format: start_line,end_line)",
    )
    parser.add_argument("--no-llm", action="store_true", help="Run without LLM")
    parser.add_argument(
        "--workdir", type=str, default="results", help="Directory to store results"
    )
    parser.add_argument(
        "--harness", type=str, required=True, help="Target harness name"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable LangGraph debug mode"
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    try:
        # Initialize GlobalContext
        gc = GlobalContext(
            no_llm=args.no_llm,
            cp_path=Path(args.cp).resolve(),
            target_harness=args.harness,
            workdir=args.workdir,
        )

        # Use the async context manager to properly initialize
        async with gc.init():
            fn_name = args.fn_name

            # Create and run the CGParserAgent
            graph = CGParserAgent(gc, no_llm=args.no_llm).compile()
            # Parse caller_location and callee_range if provided
            caller_location = None
            if args.caller_location:
                parts = args.caller_location.split(",")
                if len(parts) == 2:
                    try:
                        caller_location = (int(parts[0]), int(parts[1]))
                    except ValueError:
                        print(
                            "Warning: Invalid caller_location format. Expected"
                            " 'line,column'"
                        )

            callee_range = None
            if args.callee_range:
                parts = args.callee_range.split(",")
                if len(parts) == 2:
                    try:
                        callee_range = (int(parts[0]), int(parts[1]))
                    except ValueError:
                        print(
                            "Warning: Invalid callee_range format. Expected"
                            " 'start_line,end_line'"
                        )

            result = await graph.ainvoke(
                CGParserInputState(
                    messages=[],
                    fn_name=fn_name,
                    fn_file_path=args.fn_file_path,
                    caller_file_path=args.caller_file_path,
                    caller_fn_body=args.caller_fn_body,
                    caller_location=caller_location,
                    callee_range=callee_range,
                ),
                gc.graph_config,
                debug=args.debug,
            )

            print(result)
            import pdb

            pdb.set_trace()

    except Exception as e:
        import traceback

        print(f"Error: {e}")
        print(traceback.format_exc())
        raise e
    finally:
        if "gc" in locals():
            # Print execution time and other metrics if available
            print(f"Total execution time: {gc.get_execution_time()}")


if __name__ == "__main__":
    asyncio.run(main())
