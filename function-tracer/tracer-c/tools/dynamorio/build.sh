#!/bin/bash

DYNAMORIO_PACKAGE=DynamoRIO-Linux-11.90.20147.tar.gz
DYNAMORIO_DOWNLOAD_URL=https://github.com/DynamoRIO/dynamorio/releases/download/cronbuild-11.90.20147/DynamoRIO-Linux-11.90.20147.tar.gz

DYNAMORIO_HOME=$(readlink -f DynamoRIO-Linux-11.90.20147)

FUNCTION_TRACE_PROJECT=./function_trace_v2
BUILD_DIR=./build

if [ ! -d $DYNAMORIO_HOME ]; then
    echo "[*] Install Dynamorio"
    wget $DYNAMORIO_DOWNLOAD_URL
    tar xvf $DYNAMORIO_PACKAGE
fi

pushd $FUNCTION_TRACE_PROJECT

if [ -d $BUILD_DIR ]; then
    rm -rf $BUILD_DIR
fi

mkdir $BUILD_DIR

pushd $BUILD_DIR

export DYNAMORIO_HOME=$DYNAMORIO_HOME
cmake -DDynamoRIO_DIR=$DYNAMORIO_HOME/cmake .. && make

popd
popd 

cp $FUNCTION_TRACE_PROJECT/$BUILD_DIR/bin/libfunction_trace_v2.so ./libfunction_trace.so
