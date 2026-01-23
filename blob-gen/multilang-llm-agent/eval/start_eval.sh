#!/bin/bash

# Check if eval flag is provided
RUN_EVAL=false
for arg in "$@"; do
  if [[ "$arg" == "eval" ]]; then
    shift
    RUN_EVAL=true
    break
  fi
done

CRS_MULTILANG_PATH="../CRS-multilang/"

# Store current directory
CURRENT_DIR="$(pwd)"
# Path configurations
MLLA_PATH="$CURRENT_DIR"
RESULTS_DIR=$MLLA_PATH/results

if [ -z "$TARGET_FILE" ]; then
  TARGET_FILE=$MLLA_PATH/eval/targets-test.txt
fi

set -e
set -x

NUM_PROC=10

if $RUN_EVAL; then
  "$MLLA_PATH/eval/run_parallel.py" \
    --mlla-path "$MLLA_PATH" \
    --max-parallel "$NUM_PROC" \
    --results "$RESULTS_DIR" \
    --output "$MLLA_PATH" \
    --crs-multilang-path "$CRS_MULTILANG_PATH" \
    --target-file "$TARGET_FILE" $*
fi


"$MLLA_PATH/eval/run_parallel.py" \
  --mlla-path "$MLLA_PATH" \
  --max-parallel "$NUM_PROC" \
  --results "$RESULTS_DIR" \
  --output "$MLLA_PATH" \
  --crs-multilang-path "$CRS_MULTILANG_PATH" \
  --target-file "$TARGET_FILE" \
  --print-results $*

# Return to original directory
cd "$CURRENT_DIR" || exit 1
