#!/usr/bin/env python3
import argparse
import asyncio
import os
import random
import signal
import sys
from pathlib import Path

import psutil
import yaml
from libCRS.challenge import CP, CP_Harness
from typing_extensions import Callable, Dict, List

from mlla.modules.sanitizer import Sanitizer

# Global lock for all async_run_input calls
run_input_lock = asyncio.Lock()


def kill_process_tree(pid: int) -> None:
    """Kill a process and all its descendants by process group."""
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)

        # Include the parent in the group list too
        all_procs = children + [parent]

        groups: Dict[int, List[psutil.Process]] = {}
        for proc in all_procs:
            try:
                pgid = os.getpgid(proc.pid)
                groups.setdefault(pgid, []).append(proc)
            except Exception:
                continue

        for pgid, procs in groups.items():
            print(f"Sending SIGKILL to process group {pgid}")
            try:
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                for p in procs:
                    try:
                        print(f"Killing PID {p.pid} ({p.name()})")
                        p.kill()
                    except Exception:
                        continue

    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


def signal_handler(harness, signum, frame) -> None:
    """Handle termination signals by cleaning up harness processes."""
    signal_names = {
        signal.SIGTERM: "SIGTERM",
        signal.SIGINT: "SIGINT",
        signal.SIGQUIT: "SIGQUIT",
        signal.SIGHUP: "SIGHUP",
    }
    signal_name = signal_names.get(signum, str(signum))
    print(f"Received {signal_name}, terminating all processes...")

    # Terminate harness runner if it exists
    if harness and hasattr(harness, "runner") and harness.runner:
        try:
            print(f"Terminating harness runner for {harness.name}")
            kill_process_tree(harness.runner.pid)
        except Exception as e:
            print(f"Error terminating harness runner: {e}")

    # Finally terminate all processes in the group
    try:
        pgid = os.getpgid(0)
        os.killpg(pgid, signal.SIGKILL)
    except Exception:
        pass  # Process group already dead or we don't have permission

    sys.exit(0)


def import_generate_function(file_path: Path) -> Callable | None:
    """Import the generate() function from a Python file."""
    try:
        import importlib.util
        import sys

        # Get the module name from the file name
        module_name = file_path.stem

        # Load the module
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if not spec or not spec.loader:
            print(f"Failed to load spec for {file_path}")
            return None

        module = importlib.util.module_from_spec(spec)
        # Add random to the script
        module.random = random  # type: ignore[attr-defined]
        module.random.Random = random.Random  # type: ignore[attr-defined]
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Get the generate function
        if not hasattr(module, "generate"):
            print(f"No generate() function found in {file_path}")
            return None

        return module.generate

    except Exception as e:
        print(f"Error importing generate() function: {e}")
        return None


def execute_generate_function(generate_func: Callable, seed_num: int) -> bytes | None:
    """Execute the generate() function and return the blob."""
    try:
        # Call the generate function
        rnd = random.Random(seed_num)
        result = generate_func(rnd)

        # Convert result to bytes if it isn't already
        if isinstance(result, bytes):
            return result
        elif isinstance(result, str):
            return result.encode("utf-8")
        else:
            print(f"generate() returned unexpected type: {type(result)}")
            return None

    except Exception as e:
        print(f"Error executing generate() function: {e}")
        return None


async def process_file(
    file_path: Path,
    output_dir: Path,
    harness: CP_Harness | None,
    seed_num: int,
    num_blobs: int,
) -> None:
    """Process a single Python file to generate and run multiple blobs."""
    try:
        # Read the file content
        # with open(file_path, "r") as f:
        #     code = f.read()

        # Import generate function
        generate_func = import_generate_function(file_path)
        if not generate_func:
            return

        # Generate multiple blobs
        for i in range(num_blobs):
            try:
                # Use seed + i to get different blobs
                blob = execute_generate_function(generate_func, seed_num + i)

                if blob:
                    # Create output filename with incremental index
                    output_file = output_dir / f"{file_path.stem}_{i:03d}.bin"

                    # Write blob to file
                    with open(output_file, "wb") as f:
                        f.write(blob)
                    print(
                        f"Generated blob {i+1}/{num_blobs} saved to {output_file}."
                        f" Seed: {seed_num+i}"
                    )

                    # Run the blob only if --run is specified
                    if harness is not None:
                        print(f"\nRunning blob {i+1}/{num_blobs} from {file_path}...")

                        # Execute reproduce command
                        cmd = (
                            f"TESTCASE={output_file.absolute()} reproduce"
                            f" {harness.name}"
                        )

                        process = await asyncio.create_subprocess_shell(
                            cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            cwd="/src",
                        )

                        stdout, stderr = await process.communicate()

                        # stdout_str = stdout.decode("utf-8") if stdout else ""
                        stderr_str = stderr.decode("utf-8") if stderr else ""
                        output = stderr_str

                        # Crash detection
                        triggered, triggered_sanitizer = Sanitizer.detect_crash_type(
                            output
                        )

                        if triggered:
                            print(f"Crash detected! Sanitizer: {triggered_sanitizer}")
                            if stderr_str:
                                print("Last few lines of stderr:")
                                last_few = "\n".join(
                                    stderr_str.strip().split("\n")[-5:]
                                )
                                print(last_few)
                        else:
                            print("No crash detected")
                else:
                    print(f"Failed to generate blob {i+1}/{num_blobs} from {file_path}")

            except ValueError as e:
                print(f"Error generating blob {i+1}/{num_blobs} from {file_path}:")
                print(str(e))

    except Exception as e:
        print(f"Error processing file {file_path}:")
        print(str(e))


async def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate and run blobs from Python files containing generate() functions"
        )
    )
    parser.add_argument("input_dir", type=str, help="Directory containing Python files")
    parser.add_argument("--run", action="store_true", help="Run the generated blobs")
    parser.add_argument(
        "--cp", type=str, help="Name of the cp to use (required with --run)"
    )
    parser.add_argument(
        "--harness", type=str, help="Name of the harness to use (required with --run)"
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default="generated_blobs",
        help="Directory to store generated blobs (default: generated_blobs)",
    )
    parser.add_argument(
        "--seed", type=int, default=31337, help="Seed number for random.Random(seed)."
    )
    parser.add_argument(
        "--num-blobs",
        "-n",
        type=int,
        default=1,
        help="Number of blobs to generate per file (default: 1)",
    )

    args = parser.parse_args()

    # Validate arguments
    if args.run:
        if not args.cp:
            print("Error: --cp is required when using --run")
            sys.exit(1)
        if not args.harness:
            print("Error: --harness is required when using --run")
            sys.exit(1)

    cp_name = args.cp
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    harness_name = args.harness

    # Validate input directory
    if not input_dir.is_dir():
        print(f"Error: {input_dir} is not a directory")
        sys.exit(1)

    # Only set up harness if --run is specified
    harness = None
    if args.run:
        # Read harness path from config
        config_path = Path("/src/.aixcc/config.yaml")
        if not config_path.exists():
            print(f"Error: Config file not found at {config_path}")
            sys.exit(1)

        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Get harness info from config
        harnesses = config.get("harness_files", {})
        target_harness = None
        for harness_info in harnesses:
            if harness_info["name"] != harness_name:
                continue

            target_harness = harness_info

        if not target_harness:
            print(f"Error: Harness '{harness_name}' not found in config")
            print("Available harnesses:", list(map(lambda x: x["name"], harnesses)))
            sys.exit(1)

        harness_path_str = target_harness["path"]
        harness_path_str = harness_path_str.replace("$PROJECT", "/src")
        harness_path_str = harness_path_str.replace("$REPO", "/src/repo")

        harness_path = Path(harness_path_str)
        if not harness_path.exists():
            print(f"Error: Harness file {harness_path} not found")
            sys.exit(1)

    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find all Python files
    python_files = list(input_dir.glob("*.py"))
    if not python_files:
        print(f"No Python files found in {input_dir}")
        sys.exit(1)

    print(f"Found {len(python_files)} Python files")

    if args.run:
        cp = CP(cp_name, "/src/", "/src/repo", "/out")

        # Create test harness
        harness = CP_Harness(
            cp=cp,
            name=harness_name,
            bin_path=None,  # Will be set by the harness itself
            src_path=harness_path,
        )

        # Set up signal handlers for proper cleanup
        signal.signal(signal.SIGTERM, lambda s, f: signal_handler(harness, s, f))
        signal.signal(signal.SIGINT, lambda s, f: signal_handler(harness, s, f))
        signal.signal(signal.SIGQUIT, lambda s, f: signal_handler(harness, s, f))
        signal.signal(signal.SIGHUP, lambda s, f: signal_handler(harness, s, f))

    # Process each file individually
    for file_path in python_files:
        print(f"\nProcessing {file_path}...")
        await process_file(file_path, output_dir, harness, args.seed, args.num_blobs)


if __name__ == "__main__":
    asyncio.run(main())
