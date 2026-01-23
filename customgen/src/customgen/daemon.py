import resource
import sys
from argparse import ArgumentParser
from concurrent import futures
from pathlib import Path

import grpc

from . import (
    fetch_antlr4_generators,
    generate_antlr4,
    generate_bmp,
    generate_gif,
    generate_jpeg,
    generate_png,
)
from .rpc.customgen_pb2 import (
    CustomGenRequest,
    CustomGenResponse,
    GenerationError,
    GenerationResult,
)
from .rpc.customgen_pb2_grpc import (
    CustomGenServiceServicer,
    add_CustomGenServiceServicer_to_server,
)


class CustomGenServicer(CustomGenServiceServicer):
    def generate(self, request: CustomGenRequest, context) -> CustomGenResponse:
        gen_id = request.generator_id
        count = request.count
        output = []

        try:
            if gen_id == "bmp":
                output = generate_bmp(count)
            elif gen_id == "gif":
                output = generate_gif(count)
            elif gen_id == "jpeg":
                output = generate_jpeg(count)
            elif gen_id == "png":
                output = generate_png(count)
            elif gen_id in available_antlr4_generators:
                output = generate_antlr4(gen_id, count)
            else:
                raise Exception("No such generator id")
        except Exception as e:
            return CustomGenResponse(failed=GenerationError(message=str(e)))

        return CustomGenResponse(
            generated=GenerationResult(count=len(output), output=output)
        )


if __name__ == "__main__":
    global available_antlr4_generators

    sys.setrecursionlimit(10**6)
    (_, cur_limit_hard) = resource.getrlimit(resource.RLIMIT_STACK)
    resource.setrlimit(resource.RLIMIT_STACK, (cur_limit_hard, cur_limit_hard))

    parser = ArgumentParser(
        prog="customgen.daemon",
        description="Generates random bytes according to custom rules",
    )

    parser.add_argument("socket_file", type=Path)

    args = parser.parse_args()
    available_antlr4_generators = fetch_antlr4_generators()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    add_CustomGenServiceServicer_to_server(CustomGenServicer(), server)
    server.add_insecure_port(f"unix://{args.socket_file.resolve()}")
    server.start()
    server.wait_for_termination()
