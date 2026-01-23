import json
import resource
import sys
from argparse import ArgumentParser
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import (
    fetch_antlr4_generators,
    fetch_generators,
    generate_antlr4,
    generate_bmp,
    generate_gif,
    generate_jpeg,
    generate_png,
)

sys.setrecursionlimit(10**6)
(_, cur_limit_hard) = resource.getrlimit(resource.RLIMIT_STACK)
resource.setrlimit(resource.RLIMIT_STACK, (cur_limit_hard, cur_limit_hard))

parser = ArgumentParser(
    prog="customgen",
    description="Generates random bytes according to custom rules",
)

parser.add_argument("generator_id")
parser.add_argument("output_dir", type=Path)
parser.add_argument("-c", "--count", default=1, type=int)
parser.add_argument("-l", "--list", default=False, action="store_true")

args = parser.parse_args()
if args.list:
    print(json.dumps(fetch_generators()))

outputs = []

available_antlr4_generators = fetch_antlr4_generators()
if args.generator_id == "bmp":
    outputs = generate_bmp(args.count)
elif args.generator_id == "gif":
    outputs = generate_gif(args.count)
elif args.generator_id == "jpeg":
    outputs = generate_jpeg(args.count)
elif args.generator_id == "png":
    outputs = generate_png(args.count)
elif args.generator_id in available_antlr4_generators:
    outputs = generate_antlr4(args.generator_id, args.count)

for output in outputs:
    with NamedTemporaryFile(dir=args.output_dir, delete=False) as fout:
        fout.write(output)
