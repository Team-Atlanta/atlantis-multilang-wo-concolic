#!/bin/bash

# Store the current directory
ORIGINAL_DIR=$(pwd)

# Check if required arguments are provided
if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <oss-fuzz-dir> <build-dir> <language>"
    echo "language should be either 'c', 'cpp' or 'java'"
    exit 1
fi

OSS_FUZZ_DIR=$1
BUILD_DIR=$2
LANGUAGE=$3

# Validate language parameter
if [ "$LANGUAGE" != "c" ] && [ "$LANGUAGE" != "cpp" ] && [ "$LANGUAGE" != "java" ]; then
    echo "Error: Language must be either 'c', 'cpp' or 'java'"
    exit 1
fi

# Create build directory if it doesn't exist
mkdir -p "$BUILD_DIR"/joern-cpg

# Set the project directory based on language
if [ "$LANGUAGE" = "c" ]; then
    PROJECTS_DIR="$OSS_FUZZ_DIR/projects/aixcc/c"
    DOCKER_PATH="aixcc/c"
elif [ "$LANGUAGE" = "cpp" ]; then
    PROJECTS_DIR="$OSS_FUZZ_DIR/projects/aixcc/cpp"
    DOCKER_PATH="aixcc/cpp"
else
    PROJECTS_DIR="$OSS_FUZZ_DIR/projects/aixcc/jvm"
    DOCKER_PATH="aixcc/jvm"
fi

if [ "$LANGUAGE" = "cpp" ]; then
    LANGUAGE="c"
fi

# List all projects in the directory
PROJECTS=$(ls $PROJECTS_DIR)

for PROJECT in $PROJECTS; do
    echo "[+] Running joern-parse for $PROJECT"

    PROJECT_CONFIG_FILE="$PROJECTS_DIR/$PROJECT/.aixcc/config.yaml"

    # Check if the image exists. If not, build image using oss-fuzz scripts
    if docker images | grep -q "gcr.io/oss-fuzz/$DOCKER_PATH/$PROJECT"; then
        echo "[+] Image already exists for $PROJECT, skipping image build"
    else
        echo "[+] Building image for $PROJECT"
        cd $OSS_FUZZ_DIR || exit 1
        python3 infra/helper.py build_image $DOCKER_PATH/$PROJECT --no-pull
        cd "$ORIGINAL_DIR" || exit 1
    fi

    # Run CodeQL analysis
    echo "[+] Running joern-parse for $PROJECT"
    # python ./scripts/joern.py run-joern-parse-in-docker $PROJECT aixcc-afc/$DOCKER_PATH/$PROJECT $LANGUAGE --build_dir $BUILD_DIR | tee $BUILD_DIR/joern-cpg/joern-parse_$PROJECT.log
    ( set -x; python ./scripts/joern.py $PROJECT $LANGUAGE $PROJECT_CONFIG_FILE run-joern-parse-in-docker --build_dir $BUILD_DIR | tee $BUILD_DIR/joern-cpg/joern-parse_$PROJECT.log )
done
