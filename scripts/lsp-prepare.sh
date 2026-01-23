#!/bin/bash -e

if [ -f /work/compile_commands.json ]; then
  echo "compile_commands.json already built"
  exit 0
fi
FUZZING_LANGUAGE=c bear --cdb /work/compile_commands.json /usr/local/bin/compile.orig
