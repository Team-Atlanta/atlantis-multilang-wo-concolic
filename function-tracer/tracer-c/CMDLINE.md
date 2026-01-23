## How to trace with cmdline


### Build environment

first, build docker for environment

```bash
./docker-build-inline.sh
```

run docker with fuzzer directory


```bash
#!/bin/bash

FUZZER_DIR=/path/to/fuzzers/directory

docker run \
    -v $FUZZER_DIR:/fuzzer_dir\
    -it \
    tracer-c-inline /bin/bash
```

### Run tracer

cmd version of C tracer is `cmd_c.py`

```
usage: cmd_c.py [-h] {trace} ...

C tracer

positional arguments:
  {trace}     command: trace
    trace     trace inputs

options:
  -h, --help  show this help message and exit

usage: cmd_c.py trace [-h] --harness HARNESS --seed SEED --output OUTPUT --fuzzerdir FUZZERDIR

options:
  -h, --help            show this help message and exit
  --harness HARNESS     harness name
  --seed SEED           seed data path
  --output OUTPUT       trace output path
  --fuzzerdir FUZZERDIR
                        fuzzer directory

```

Then, run tracer
```
python3 cmd_java.py --fuzzerdir=[fuzzer directory] --harness=[harness_name] --seed=[seed input path] --output=[output file path]
```

fuzzer directory means untared CRS-multilang's `fuzzer.tar.gz`

After tracing, tracer drop the trace results to the `--output` specified path.