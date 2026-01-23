#!/usr/bin/env bash

set -ex

LIB_PATH=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")

pip3 install "hatchling >= 1.26"
pip3 install --force-reinstall "$LIB_PATH"
