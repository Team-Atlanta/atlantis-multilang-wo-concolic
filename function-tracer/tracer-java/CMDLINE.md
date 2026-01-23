## How to trace data with cmdline


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
    tracer-java-inline /bin/bash
```

### Run tracer

cmd version of java tracer is `cmd_java.py`

```
usage: cmd_java.py [-h] {prepare,trace} ...

JAVA tracer

positional arguments:
  {prepare,trace}  command: [prepare | trace]
    prepare        prepare work directory
    trace          trace inputs

options:
  -h, --help       show this help message and exit

usage: cmd_java.py prepare [-h] --fuzzerdir FUZZERDIR

options:
  -h, --help            show this help message and exit
  --fuzzerdir FUZZERDIR
                        fuzzer directory

usage: cmd_java.py trace [-h] --harness HARNESS --seed SEED --output OUTPUT

options:
  -h, --help         show this help message and exit
  --harness HARNESS  harness name
  --seed SEED        seed data path
  --output OUTPUT    trace output path

```

1. **prepare trace directory**

Since Java Tracer uses a custom jazzer engine, tracer copy the original fuzzer directory to the work directory and then overwrite the custom jazzer engine.

```bash
python3 cmd_java.py prepare --fuzzerdir=[fuzzer directory]
```

fuzzer directory means untared CRS-multilang's `fuzzer.tar.gz`

NOTE: Tracer gets the work directory path as an environment variable named `TRACER_WORKDIR`.

(please see the [Dockerfile.inline](Dockerfile.inline))


2. **trace with harness, input**

```
python3 cmd_java.py --harness=[harness_name] --seed=[seed input path] --output=[output file path]
```

After tracing, tracer drop the trace results to the `--output` specified path.