#!/bin/bash
BASEDIR=$(dirname $0)
$BASEDIR/run.py build_crs
docker build -f joern/Dockerfile -t multilang-runner-joern .
