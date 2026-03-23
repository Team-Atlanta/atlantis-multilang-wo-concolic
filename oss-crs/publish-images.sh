#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CRS_YAML="${SCRIPT_DIR}/crs.yaml"

REGISTRY="${REGISTRY:-ghcr.io/team-atlanta/atlantis-multilang-wo-concolic}"

IMAGES=(
    "multilang-clang"
    "multilang-builder"
    "multilang-builder-jvm"
    "multilang-c-archive"
    "multilang-jvm-archive"
    "multilang-crs"
    "multilang-joern"
)

log() {
    echo "[publish-images] $*"
}

resolve_version() {
    if [ -n "${VERSION:-}" ]; then
        echo "${VERSION}"
        return
    fi

    awk '/^version:/ { print $2; exit }' "${CRS_YAML}"
}

VERSION="$(resolve_version)"

usage() {
    cat <<EOF
Build and publish prepare images.

USAGE:
    ./oss-crs/publish-images.sh <command> [options]

COMMANDS:
    prepare         Prepare canonical images using bake defaults
                    Pass --rebuild to force USE_PREBUILT=false
    push            Push the canonical prepare images to the registry
    prepare-push    Run prepare, then push
    status          Show whether expected local image tags exist
    help            Show this help

ENVIRONMENT:
    REGISTRY        Registry prefix (default: ghcr.io/team-atlanta/atlantis-multilang-wo-concolic)
    VERSION         Version tag to push alongside latest (default: oss-crs/crs.yaml version)

IMAGES:
$(printf '    - %s\n' "${IMAGES[@]}")
EOF
}

ensure_local_tag() {
    local image="$1"
    local tag="$2"
    docker image inspect "${REGISTRY}/${image}:${tag}" >/dev/null 2>&1
}

prepare_images() {
    local rebuild="${1:-false}"
    local mode="using bake defaults"

    if [ "${rebuild}" = "true" ]; then
        mode="from scratch (USE_PREBUILT=false)"
    fi

    log "Preparing canonical images ${mode}"
    (
        cd "${PROJECT_DIR}"
        if [ "${rebuild}" = "true" ]; then
            USE_PREBUILT=false VERSION="${VERSION}" REGISTRY="${REGISTRY}" \
                docker buildx bake \
                -f oss-crs/docker-bake.hcl \
                prepare
        else
            VERSION="${VERSION}" REGISTRY="${REGISTRY}" \
                docker buildx bake \
                -f oss-crs/docker-bake.hcl \
                prepare
        fi
    )
}

push_one() {
    local image="$1"
    local tag="$2"
    if ! ensure_local_tag "${image}" "${tag}"; then
        echo "missing local image: ${REGISTRY}/${image}:${tag}" >&2
        exit 1
    fi
    log "Pushing ${REGISTRY}/${image}:${tag}"
    docker push "${REGISTRY}/${image}:${tag}"
}

push_images() {
    log "Pushing canonical prepare images to ${REGISTRY}"
    for image in "${IMAGES[@]}"; do
        push_one "${image}" "${VERSION}"
        if [ "${VERSION}" != "latest" ]; then
            push_one "${image}" "latest"
        fi
    done
}

status_images() {
    log "Local image status (registry: ${REGISTRY}, version: ${VERSION})"
    echo ""
    printf "%-35s %-8s %-8s\n" "IMAGE" "VERSION" "LATEST"
    printf "%-35s %-8s %-8s\n" "-----" "-------" "------"

    for image in "${IMAGES[@]}"; do
        version_status="no"
        latest_status="no"

        if ensure_local_tag "${image}" "${VERSION}"; then
            version_status="yes"
        fi
        if ensure_local_tag "${image}" "latest"; then
            latest_status="yes"
        fi

        printf "%-35s %-8s %-8s\n" "${image}" "${version_status}" "${latest_status}"
    done
}

main() {
    case "${1:-help}" in
        prepare)
            case "${2:-}" in
                "")
                    prepare_images
                    ;;
                --rebuild)
                    prepare_images true
                    ;;
                *)
                    echo "unknown option for prepare: ${2}" >&2
                    exit 1
                    ;;
            esac
            ;;
        push)
            push_images
            ;;
        prepare-push)
            case "${2:-}" in
                --rebuild)
                    prepare_images true
                    ;;
                *)
                    prepare_images
                    ;;
            esac
            push_images
            ;;
        status)
            status_images
            ;;
        help|--help|-h)
            usage
            ;;
        *)
            echo "unknown command: ${1}" >&2
            echo "" >&2
            usage >&2
            exit 1
            ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
