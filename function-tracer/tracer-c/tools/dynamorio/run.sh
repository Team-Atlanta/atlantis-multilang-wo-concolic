#!/bin/bash

DYNAMORIO=DynamoRIO-Linux-11.90.20147
DYNAMORIO_HOME="$(readlink -f $FUNCTION_TRACER_DIR/$DYNAMORIO)"

DYNAMORIO_PLUGIN="$(readlink -f $FUNCTION_TRACER_DIR/libfunction_trace.so)"

OUTPUT_FILE=$1

$DYNAMORIO_HOME/bin64/drrun -disable_traces -c $DYNAMORIO_PLUGIN $OUTPUT_FILE -- ${@:2}