#!/usr/bin/env bash

set -ex

export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_GRAMMARINATOR=0.1.0

LIB_PATH=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")

pip3 install "poetry >=2.1, <3.0"
pip3 install "grpcio-tools ==1.71.0"

python -m grpc_tools.protoc -Icustomgen/rpc=$LIB_PATH/proto \
  --python_out=$LIB_PATH/src --pyi_out=$LIB_PATH/src --grpc_python_out=$LIB_PATH/src \
  $LIB_PATH/proto/customgen.proto

pip3 install --force-reinstall "$LIB_PATH"

# Reinstall with codegen
mkdir -p "$LIB_PATH/src/customgen/generated/antlr4"
if [ -n "$(ls "$LIB_PATH/src/customgen/generated/antlr4")" ]; then
    rm -r "$LIB_PATH"/src/customgen/generated/antlr4/*
fi
touch "$LIB_PATH/src/customgen/generated/__init__.py"
touch "$LIB_PATH/src/customgen/generated/antlr4/__init__.py"
python3 -m customgen.antlr4 codegen "$LIB_PATH/third_party/antlr4-grammars/grammars.json" "$LIB_PATH/src/customgen/generated/antlr4"
pip3 install --force-reinstall "$LIB_PATH"

python3 -m customgen -l - - > "$LIB_PATH/custom_ids.json"
