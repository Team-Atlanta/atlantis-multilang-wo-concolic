import json
from argparse import ArgumentParser
from enum import Enum
from glob import glob
from pathlib import Path

from grammarinator.tool import ProcessorTool

from . import fetch_antlr4_generators


def check_antlr4_grammars_json(json_path: Path) -> bool:
    ok = True
    grammars = None
    grammars_dir = json_path.resolve().parent
    expected_grammar_dir_list = set(
        map(
            lambda x: str(Path(x).parent),
            filter(
                lambda x: "/examples/" not in x,
                glob("**/*.g4", root_dir=grammars_dir, recursive=True),
            ),
        )
    )
    grammar_dir_list = set()
    with open(json_path) as json_file:
        grammars = json.load(json_file)

    if grammars is None:
        print("Grammar json file not holding array")
        return False

    for grammar in grammars:
        name = grammar["name"]
        lexer = grammar["lexer"]
        parser = grammar["parser"]
        examples = grammar["example"]

        example_base_dir = None
        if lexer:
            lexer_path = grammars_dir / lexer
            example_base_dir = lexer_path.parent
            if not lexer_path.exists():
                print(f"No lexer for {name}: {lexer_path}")
                ok = False

        if parser:
            parser_path = grammars_dir / parser
            example_base_dir = parser_path.parent
            if not parser_path.exists():
                print(f"No parser for {name}: {parser_path}")
                ok = False

        if example_base_dir is None:
            print(f"No base example directory for {name}. Check if the grammar exists.")
        else:
            grammar_dir_str = str(example_base_dir.relative_to(grammars_dir))
            grammar_dir_list.add(grammar_dir_str)
            for example in examples:
                example_path = example_base_dir / "examples" / example
                if not example_path.exists():
                    print(f"No example for {name}: {example_path}")
                    ok = False

    if grammar_dir_list != expected_grammar_dir_list:
        diff = sorted(list(expected_grammar_dir_list.difference(grammar_dir_list)))
        print("\nUncovered grammar:")
        print(f"{diff}")
        return False

    return ok


def fix_antlr4_grammars_json(json_path: Path, output: Path) -> bool:
    grammars = None
    new_grammars = []
    grammars_dir = json_path.resolve().parent
    with open(json_path) as json_file:
        grammars = json.load(json_file)

    if grammars is None:
        print("Grammar json file not holding array")
        return False

    for grammar in grammars:
        name = grammar["name"]
        lexer = grammar["lexer"]
        parser = grammar["parser"]
        examples = grammar["example"]

        example_base_dir = None
        if lexer:
            lexer_path = grammars_dir / lexer
            if lexer_path.exists():
                example_base_dir = lexer_path.parent

        if parser:
            parser_path = grammars_dir / parser
            if parser_path.exists():
                example_base_dir = parser_path.parent

        if example_base_dir:
            example_dir = example_base_dir / "examples"
            new_examples = glob("**/*", root_dir=example_dir, recursive=True)
            new_examples = list(
                filter(lambda x: (example_dir / x).is_file(), new_examples)
            )
            new_examples.sort()
            examples.sort()

            if examples != new_examples:
                print(f"Change examples for {name}:")
                print(f"{examples}\n\n================>\n\n{new_examples}\n\n")

            grammar["example"] = new_examples
            new_grammars.append(grammar)
        else:
            print(f"Remove {name}: No parser/lexer exists.")

    with open(output, "w") as fout:
        json.dump(new_grammars, fout)

    return True


def antlr4_grammars_codegen(json_path: Path, out_dir: Path) -> bool:
    grammars = None
    grammars_dir = json_path.resolve().parent
    with open(json_path) as json_file:
        grammars = json.load(json_file)

    if grammars is None:
        print("Grammar json file not holding array")
        return False

    for grammar in grammars:
        name = grammar["name"]
        lexer = grammar["lexer"]
        parser = grammar["parser"]
        entrypoint = grammar["start"]

        rules = []
        if lexer:
            lexer_path = grammars_dir / lexer
            if lexer_path.exists():
                rules.append(str(lexer_path))

        if parser:
            parser_path = grammars_dir / parser
            if parser_path.exists():
                rules.append(str(parser_path))

        if len(rules) == 0:
            print(f"Skip {name}: No parser/lexer exists.")
            continue
        print(f"CodeGen: {name}")
        ProcessorTool("py", str(out_dir)).process(rules, default_rule=entrypoint)

    return True


class ProgramMode(Enum):
    CHECK_JSON = 0
    FIX_JSON = 1
    CODE_GEN = 2
    LIST_GEN = 3


program_modes = {
    "check": ProgramMode.CHECK_JSON,
    "fix": ProgramMode.FIX_JSON,
    "codegen": ProgramMode.CODE_GEN,
    "list": ProgramMode.LIST_GEN,
}

if __name__ == "__main__":
    parser = ArgumentParser(
        prog="customgen.antlr4",
        description="ANTLR4 grammar helper",
    )

    subparser = parser.add_subparsers(dest="progmode")

    check_parser = subparser.add_parser("check")
    check_parser.add_argument("input_json", type=Path)

    fix_parser = subparser.add_parser("fix")
    fix_parser.add_argument("input_json", type=Path)
    fix_parser.add_argument("output_json", type=Path)

    codegen_parser = subparser.add_parser("codegen")
    codegen_parser.add_argument("input_json", type=Path)
    codegen_parser.add_argument("output_dir", type=Path)

    _listgen_parser = subparser.add_parser("list")

    args = parser.parse_args()
    mode = program_modes.get(args.progmode)

    if mode == ProgramMode.CHECK_JSON:
        if not check_antlr4_grammars_json(args.input_json):
            exit(-1)
    elif mode == ProgramMode.FIX_JSON:
        if not fix_antlr4_grammars_json(args.input_json, args.output_json):
            exit(-1)
    elif mode == ProgramMode.CODE_GEN:
        if not antlr4_grammars_codegen(args.input_json, args.output_dir):
            exit(-1)
    elif mode == ProgramMode.LIST_GEN:
        print("\n".join(fetch_antlr4_generators()))
