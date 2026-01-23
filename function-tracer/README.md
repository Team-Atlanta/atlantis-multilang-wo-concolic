## Function-level-tracer

### How to use:

STEP 1. build docker images

```bash
# tracer-c (Build image named sarif-tracer-c)
cd tracer-c && ./docker-build.sh

# tracer-java (Build image named sarif-tracer-java)
cd tracer-java && ./docker-build.sh

```

STEP 2. Run docker image with environment

You have to set `CRS_SARIF_TRACER_CORPUS_DIRECTORY`, `CRS_SARIF_TRACER_TRACE_OUTPUTDIR` and `MULTILANG_BUILD_DIR`

- `CRS_SARIF_TRACER_CORPUS_DIRECTORY`: corpus (seed) directory
    - directory structure is:
```
<corpus_directory>
└── <harness_name>
    └── <seedfile>

```

- `CRS_SARIF_TRACER_TRACE_OUTPUTDIR`: trace output directory
    - directory structure is same as corpus directory, and output file name is `<seedfile>-traceoutput`
```
<output_directory>
└── <harness_name>
    └── <seedfile>-traceoutput
```

- `MULTILANG_BUILD_DIR`: multilang's build output directory. for example, in case of `aixcc/c/mock-c`, build output directory's path is `<CRS-multilang_path>/libs/oss-fuzz/build/artifacts/aixcc/c/mock-c/tarballs`

run example: 

```
#!/bin/bash

CRS_SARIF_TRACER_CORPUS_DIRECTORY=./corpus
CRS_SARIF_TRACER_TRACE_OUTPUTDIR=./output
MULTILANG_BUILD_DIR=/CRS-multilang/project/path/libs/oss-fuzz/build/artifacts/aixcc/c/mock-c/tarballs
docker run -v $CRS_SARIF_TRACER_CORPUS_DIRECTORY:/corpus \
    -v $CRS_SARIF_TRACER_TRACE_OUTPUTDIR:/output \
    -v $MULTILANG_BUILD_DIR:/resources \
    -it \
    -e CRS_SARIF_TRACER_CORPUS_DIRECTORY=/corpus \
    -e CRS_SARIF_TRACER_TRACE_OUTPUTDIR=/output \
    -e MULTILANG_BUILD_DIR=/resources \
    sarif-tracer-c
```

### Output examples:

- [c-tracer](tracer-c/README.md)
- [java-tracer](tracer-java/README.md)
