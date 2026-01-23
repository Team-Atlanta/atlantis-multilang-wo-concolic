from argparse import ArgumentParser
from pathlib import Path
from testlang.processing import run

if __name__ == "__main__":
    parser = ArgumentParser(
        prog="testlang.processing.run",
          description="Processing task for testlang utilizing generated codes",
    )

    parser.add_argument("module_name", type=str)
    parser.add_argument("-i", "--input", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("-p", "--path", type=Path, required=False)
    parser.add_argument(
        "-t", "--trace", action="store_true", help="Intercept exceptions to give them to LLM"
    )
    args = parser.parse_args()

    run(args.module_name, args.input, args.output, args.path, args.trace)