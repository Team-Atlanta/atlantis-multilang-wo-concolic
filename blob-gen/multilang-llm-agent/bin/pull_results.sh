#!/bin/bash

set -e
set -x

sudo chown -R $USER:$USER results
pushd results
git checkout main
git pull origin main --rebase
popd
