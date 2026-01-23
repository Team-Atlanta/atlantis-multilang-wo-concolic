#!/bin/bash

CRS_SARIF_TRACER_CORPUS_DIRECTORY=./corpus
CRS_SARIF_TRACER_TRACE_OUTPUTDIR=./output
MULTILANG_BUILD_DIR=/CRS-multilang/directory/libs/oss-fuzz/build/artifacts/aixcc/c/mock-c/tarballs
docker run -v $CRS_SARIF_TRACER_CORPUS_DIRECTORY:/corpus \
    -v $CRS_SARIF_TRACER_TRACE_OUTPUTDIR:/output \
    -v $MULTILANG_BUILD_DIR:/resources \
    -it \
    -e CRS_SARIF_TRACER_CORPUS_DIRECTORY=/corpus \
    -e CRS_SARIF_TRACER_TRACE_OUTPUTDIR=/output \
    -e MULTILANG_BUILD_DIR=/resources \
    sarif-tracer-c