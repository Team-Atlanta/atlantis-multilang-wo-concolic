import argparse
import os
from pathlib import Path
from typing import List
from langchain_openai import ChatOpenAI
from langchain_core.runnables.base import Runnable
from langchain_community.chat_message_histories import FileChatMessageHistory
from pydantic import BaseModel, SecretStr

PROMPT = """
You will be given a chunk of source code. Each line begins with a line number and is followed by one or both of the markers L and S:

L means the line was covered by LibFuzzer instrumentation.

S means the line was covered by SymCC instrumentation.

SymCC only instruments the predicate expressions of control-flow statements: if, switch, while, do-while, and for.

Your task is to return a list of lines where:

The line contains a control-flow statement (if, switch, while, do-while, or for), and

The expression was executed according to LibFuzzer (either marked directly with L, or indirectly inferred through control flow), but not marked with S (i.e., SymCC did not record coverage).

You should infer execution contextually. For example, if a nested control-flow block is marked with L, assume the parent condition was evaluated as well, even if it's not explicitly marked. If the entire code chunk is not annotated with L(S), assume NONE of the lines were executed by LibFuzzer (SymCC).

Return the result as a strict JSON array of objects. Each object must contain:

"line": the line number of the relevant control-flow statement

"reason": a brief explanation of why you believe this line was executed by LibFuzzer but not by SymCC

Examples
Input:

```c
152 L         if (in->buf->rev) {
153           for (int i = 0, j = size - 1; i < j; i++, j--) {
154               u_char c = in->buf->start[i];
155               in->buf->start[i] = in->buf->start[j];
156               in->buf->start[j] = c;
157           }
158         }
Output:

```json
[
  {
    "line": 152,
    "reason": "The line is an if-statement marked with L by LibFuzzer, but not with S by SymCC."
  }
]
```
Input:

```c
164           if (prev == in->buf->pos) {
165               iov->iov_len += size;
166   
167           } else {
168 L           if (n == vec->nalloc) {
169               break;
170           }
```
Output:

```json
[
  {
    "line": 168,
    "reason": "This if-statement was directly marked with L (LibFuzzer) and not S (SymCC)."
  },
  {
    "line": 164,
    "reason": "The else block was entered, indicating this if-statement was evaluated, even though it's not marked with L or S."
  }
]
```
Only return the JSON array. Do not include any explanation or text outside the JSON.
"""


class OutputElement(BaseModel):
    line: int
    reason: str


class Output(BaseModel):
    inner: List[OutputElement]


def llm_callback(
    runnable: Runnable, chunk: List[str], prompt: str, src_contents: List[str]
) -> List[str]:
    text = "\n".join(chunk)
    full_prompt = prompt + "\n\n" + text
    out = runnable.invoke(full_prompt)
    ret = []
    for e in out.inner:
        src = ""
        src += f"// {e.reason}\n"
        src += (
            "\n".join(src_contents[e.line - 1 : min(len(src_contents), e.line + 50)])
            + "\n"
        )
        ret.append(src)
    return ret 


def annotated(line: str) -> bool:
    return line[5] == "*" or line[6] == "#"


def chunk_lines(lines, chunk_size):
    chunks = []
    current = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if annotated(line):
            block = []
            while i < len(lines) and annotated(lines[i]):
                block.append(lines[i])
                i += 1
            if len(current) + len(block) > chunk_size and current:
                chunks.append(current)
                current = block
            elif len(block) <= chunk_size:
                current.extend(block)
            else:
                raise Exception("Block too large")
        else:
            if len(current) + 1 > chunk_size and current:
                chunks.append(current)
                current = []
            current.append(line)
            i += 1
    if current:
        chunks.append(current)
    return chunks


def construct_runnable(api_key: SecretStr, endpoint: str, model: str) -> Runnable:
    llm = ChatOpenAI(
        api_key=api_key, base_url=endpoint, model=model
    ).with_structured_output(Output)
    return llm


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=str)
    parser.add_argument("--model", type=str, default="gpt-4o")
    parser.add_argument("--output", type=str, default="llm-output")
    args = parser.parse_args()

    output = Path(args.output)
    if not output.exists():
        output.mkdir(parents=True)
    endpoint = os.environ.get("LITELLM_URL")
    api_key = os.environ.get("LITELLM_KEY")
    if not endpoint or not api_key:
        raise Exception("LITELLM_URL or LITELLM_KEY not set")

    runnable = construct_runnable(SecretStr(api_key), endpoint, args.model)

    root = Path(args.directory)
    for input_id in root.iterdir():
        input_id_output_dir = output / input_id.name
        if not input_id_output_dir.exists():
            input_id_output_dir.mkdir()
        for file in input_id.glob("**/*"):
            if file.is_file():
                lines = file.read_text().splitlines()
                chunks = chunk_lines(lines, 100)
                counter = 0
                for chunk in chunks:
                    reports = llm_callback(runnable, chunk, PROMPT, lines)
                    for report in reports:
                        out_file = input_id_output_dir / f"{counter}.txt"
                        report = f"File: {file}\n\n{report}"
                        with open(out_file, "w") as f:
                            f.write(report)
                        counter += 1


if __name__ == "__main__":
    main()
