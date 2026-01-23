#!/bin/bash

FUZZER_DIR=/path/to/fuzzer/directory

docker run \
    -v $FUZZER_DIR:/fuzzer_dir\
    -it \
    tracer-java-inline /bin/bash

# 