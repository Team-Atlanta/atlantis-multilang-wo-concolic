import getpass
import json
import os
import sys
from pathlib import Path

from git import Repo
from libCRS import CP
from openai import OpenAI

from ..utils.execute_llm_code import collect_code_block

KEY = (
    getpass.getpass("Enter your LiteLLM API key: ").strip()
    if os.getenv("LITELLM_KEY") is None
    else os.getenv("LITELLM_KEY")
)

URL = (
    input("Enter your LiteLLM URL: ").strip()
    if os.getenv("LITELLM_URL") is None
    else os.getenv("LITELLM_URL")
)


def call_openai(msg: str) -> str:
    client = OpenAI(
        api_key=KEY,
        base_url=URL,
    )
    completion = client.chat.completions.create(
        model="o1-preview", messages=[{"role": "user", "content": msg}]
    )

    return completion.choices[0].message.content


def gen_sanitizer_result(cp: CP):
    ret_file = Path(f"results/{cp.name}/sanitizer_result.json")
    repo_url = "git@github.com:Team-Atlanta/benchmark-list.git"

    if ret_file.exists():
        with ret_file.open("r") as f:
            return json.load(f)

    msg = (
        "I am providing a markdown containing a vulnerability table. The CPV "
        "column in the table has cpv formatted as [harness name] + 'CPV' "
        "[cpv_number]. The Vuln column contains the vulnerability type. The markdown"
        "will be provided between <MARKDOWN> and </MARKDOWN> tags.\n\n"
    )

    msg += "<MARKDOWN>\n"
    if len(sys.argv) == 3:
        markdown_file = sys.argv[1]
    else:
        benchmark_list = Path("benchmark-list")
        if not benchmark_list.exists():
            Repo.clone_from(repo_url, benchmark_list)

        markdown_file = benchmark_list / cp.language / cp.name / "README.md"

    with open(markdown_file, "r") as f:
        msg += f.read()

    msg += "<MARKDOWN>\n"

    msg += (
        "And then I am providing a dictionary containing sanitizer information. "
        "The value of 'sanitizers' is "
        "where the key is the sanitizer id and the value is a message from the"
        "sanitizer. "
        "the dict will be provided between <SAN> and </SAN> tags.\n\n"
    )

    msg += (
        "Deserialization bug is considered as Remote Code execution and Path"
        "Traversal is considered as File Read/Write hook path. "
    )

    msg += "<SAN>\n"
    msg += f"{cp.sanitizers}\n"
    msg += "</SAN>\n"

    msg += (
        "You should return two outputs. "
        "The first output's format will be a dictionary where the key is the "
        "harness name and the value is the list of sanitizer id. "
        "The second output's format is a dictionary where the key is the CPV name "
        "and the "
        "value is the list of sanitizer id. "
        "Return them as two python dictionaries wrapped by code block. "
    )

    output = call_openai(msg)

    scripts = collect_code_block(output, lang="python")

    ret_dicts = []

    for script in scripts:
        d = eval(script)
        ret_dicts.append(d)

    if len(ret_dicts) != 2:
        print("Returned dictionaries are not two")
        sys.exit(1)

    harness_sanitizer_dict = ret_dicts[0]
    cpv_sanitizer_dict = ret_dicts[1]

    ret_dict = {
        "harness_sanitizer_dict": harness_sanitizer_dict,
        "cpv_sanitizer_dict": cpv_sanitizer_dict,
    }

    with open(ret_file, "w") as f:
        json.dump(ret_dict, f, indent=2)

    print(f"Result is saved at {ret_file}")

    return ret_dict


def main():
    if len(sys.argv) != 3:
        print(
            "Usage: python test_bga_checker_result.py <benchmark_markdown> "
            "<project_path>"
        )
        sys.exit(1)

    project_path = Path(sys.argv[2])

    if not os.path.exists(project_path):
        print(f"{project_path} does not exist")

    cp = CP(project_path)

    ret_dict = gen_sanitizer_result(cp)

    print(f"Result: {ret_dict}")


if __name__ == "__main__":
    main()
