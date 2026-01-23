# Custom input generator for UniAFL

## Installation

```sh
poetry install
./build.sh
```

## Generating inputs

```text
usage: python -m customgen [-h] [-c COUNT] generator_id output_dir

Generates random bytes according to custom rules

positional arguments:
  generator_id
  output_dir

options:
  -h, --help            show this help message and exit
  -c COUNT, --count COUNT
```
