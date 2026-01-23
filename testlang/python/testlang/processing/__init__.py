import sys
import resource
import importlib
from pathlib import Path
from typing import Optional
import traceback
import re

from .encoding import Encoder, Generator

def import_dynamic(name: str):
    qualifier = name.rsplit(".", 1)
    if len(qualifier) != 2:
        return None

    return getattr(importlib.import_module(qualifier[0]), qualifier[1])

def add_sys_path(path: Path):
    path_str = str(path.resolve())

    if path_str not in sys.path:
        sys.path.append(path_str)

def run(module_name: str, input: Path, output: Path, path: Optional[Path] = None, trace: bool = False) -> None:
    sys.setrecursionlimit(10**6)
    (_, cur_limit_hard) = resource.getrlimit(resource.RLIMIT_STACK)
    resource.setrlimit(resource.RLIMIT_STACK, (cur_limit_hard, cur_limit_hard))

    generated_code_path = path
    if generated_code_path:
        add_sys_path(generated_code_path)

    module_name = module_name
    processor = import_dynamic(module_name)
    if not processor:
        raise Exception(
            f"given module name doesn't exist in generated codes: {module_name}"
        )

    def trace_exception(_, event, arg):
        if event == "exception":
            exc_type, exc_value, exc_traceback = arg
            def exit_with_traceback():
                traceback.print_exception(exc_type, exc_value, exc_traceback, file=sys.stderr)
                sys.exit(1)

            frames = traceback.extract_tb(exc_traceback)
            if generated_code_path:
                for frame in frames:
                    if str(generated_code_path) in frame.filename:
                        exit_with_traceback()

            m = re.match(r"^(\w+).(\w+)$", str(module_name))
            if m:
                module, _ = m.groups()
                for frame in frames:
                    if re.match(rf"^{module}\.py$", Path(frame.filename).name):
                        exit_with_traceback()

        return trace_exception

    if trace:
        sys.settrace(trace_exception)

    processor_instance = processor()
    if isinstance(processor_instance, Encoder):
        encoder = processor_instance
        with open(input, "rb") as fin, open(output, "wb") as fout:
            input_data = fin.read()
            output_data = encoder.encode(input_data)
            encoder.validate(output_data)
            fout.write(output_data)
    elif isinstance(processor_instance, Generator):
        generator = processor_instance
        with open(output, "wb") as fout:
            output_data = generator.generate()
            generator.validate(output_data)
            fout.write(output_data)
    else:
        raise Exception(
            f"given module {module_name} should have a method either:"
            "1. def encode(self, input: bytes) -> bytes:"
            "2. def generate(self) -> bytes:"
            "and also:"
            "1. def validate(self, input: bytes):"
        )