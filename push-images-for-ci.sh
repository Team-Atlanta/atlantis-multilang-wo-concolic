#!/bin/bash
REGISTRY=$1
VERSION=$2

push() {
    docker image tag $1 $REGISTRY/crs-multilang/$1:$VERSION
    docker image push $REGISTRY/crs-multilang/$1:$VERSION
}

push "crs-multilang"
push "multilang-builder-jvm"
push "multilang-builder"
