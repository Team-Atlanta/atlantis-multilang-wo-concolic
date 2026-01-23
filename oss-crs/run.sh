#!/bin/bash
set -eu

HARNESS_NAME="$1"
shift || true

# Sanitize names for docker compose project/container naming
# Docker Compose requires lowercase project names
sanitize_name() {
    echo "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '_' | sed 's/_*$//'
}

# Set COMPOSE_PROJECT_NAME early so cleanup can use it
SAFE_TARGET=$(sanitize_name "${CRS_TARGET:-crs}")
SAFE_HARNESS=$(sanitize_name "$HARNESS_NAME")
export SAFE_TARGET
export SAFE_HARNESS
export COMPOSE_PROJECT_NAME="${SAFE_TARGET}_${SAFE_HARNESS}"

# Cleanup function to stop docker-compose services on signal
cleanup() {
    echo "=== Signal received, stopping services... ==="
    cd /app 2>/dev/null || true
    # Try both compose files (one will be active)
    docker compose -f docker-compose.mlla.yml down --remove-orphans 2>/dev/null || true
    docker compose -f docker-compose.yml down --remove-orphans 2>/dev/null || true
    exit 130
}

# Trap signals to ensure docker-compose cleanup
trap cleanup INT TERM

echo "=== CRS-Multilang Run Phase (Host Docker) ==="
echo "Harness: $HARNESS_NAME"

# Get our own container ID for cleanup sidecar
RUNNER_CONTAINER_ID=$(cat /proc/self/cgroup 2>/dev/null | grep -oP '(?<=docker/)[a-f0-9]+' | head -1)
# Fallback: hostname is often container ID
[ -z "$RUNNER_CONTAINER_ID" ] && RUNNER_CONTAINER_ID=$(hostname)
export RUNNER_CONTAINER_ID
echo "Runner container ID: $RUNNER_CONTAINER_ID"

echo "Environment:"
echo "  CPUSET_CPUS: ${CPUSET_CPUS:-not set}"
echo "  MEMORY_LIMIT: ${MEMORY_LIMIT:-not set}"
echo "  RUN_FUZZER_MODE: ${RUN_FUZZER_MODE:-not set}"
echo "  CRS_INPUT_GENS: ${CRS_INPUT_GENS:-given_fuzzer}"

# Verify Docker socket is available (using host docker daemon)
if ! docker info > /dev/null 2>&1; then
    echo "ERROR: Docker socket not available. Ensure /var/run/docker.sock is mounted."
    exit 1
fi
echo "Docker daemon accessible via host socket"

# Verify required images exist on host daemon (built by builder phase)
echo "Verifying images on host docker daemon..."
for img in crs-multilang/crs-multilang:latest crs-multilang/multilang-runner-joern:latest redis:latest; do
    if ! docker image inspect "$img" > /dev/null 2>&1; then
        echo "ERROR: Image not found: $img"
        echo "Ensure builder phase completed successfully."
        exit 1
    fi
    echo "  Found: $img"
done

# Set environment variables for docker-compose
export HARNESS_NAME="$HARNESS_NAME"
export CPUSET_CPUS="${CPUSET_CPUS:-0-7}"
export MEMORY_LIMIT="${MEMORY_LIMIT:-16G}"
export LITELLM_URL="${LITELLM_URL:-}"

# Derive LSP runner image name from CRS_TARGET (provided by oss-crs)
# LSP runner image follows pattern: multilang-lsp-{project_name}
if [ -n "${CRS_TARGET:-}" ]; then
    # Sanitize project name same way as build.sh does (lowercase for registry compatibility)
    PROJECT_SAFE_NAME=$(echo "$CRS_TARGET" | tr '/' '_' | tr '[:upper:]' '[:lower:]')
    export LSP_RUNNER="crs-multilang/multilang-lsp-${PROJECT_SAFE_NAME}:latest"
    echo "LSP Runner image: $LSP_RUNNER"
else
    echo "WARNING: CRS_TARGET not set, LSP service may not start"
    export LSP_RUNNER=""
fi
export CRS_TARGET="${CRS_TARGET:-}"
export CRS_NAME="${CRS_NAME:-crs-multilang}"
export CRS_SKIP_SAVE="${CRS_SKIP_SAVE:-}"

# Use LITELLM_KEY env var if set, otherwise read from /keys/api_key (oss-crs convention)
if [ -z "${LITELLM_KEY:-}" ] && [ -f /keys/api_key ]; then
    export LITELLM_KEY="$(cat /keys/api_key)"
fi

# HOST_OUT_DIR is used for docker volume mounts when using host docker socket
# If not set, defaults to /out (for DinD mode compatibility)
export HOST_OUT_DIR="${HOST_OUT_DIR:-/out}"

# HOST_ARTIFACT_DIR contains tarballs (created by builder phase)
# If not set, defaults to HOST_OUT_DIR for backward compatibility
export HOST_ARTIFACT_DIR="${HOST_ARTIFACT_DIR:-$HOST_OUT_DIR}"

# Per-harness artifact directories for POVs, corpus, and CRS data
# Uses run/{harness}/ convention for per-harness isolation
export HOST_POV_DIR="${HOST_POV_DIR:-$HOST_ARTIFACT_DIR/run/$HARNESS_NAME/povs}"
export HOST_CORPUS_DIR="${HOST_CORPUS_DIR:-$HOST_ARTIFACT_DIR/run/$HARNESS_NAME/corpus}"
export HOST_CRS_DATA_DIR="${HOST_CRS_DATA_DIR:-$HOST_ARTIFACT_DIR/run/$HARNESS_NAME/crs-data}"

# Network configuration:
# - crs-internal: Always created, project-scoped, isolated per project/harness
# - crs-external: For LiteLLM connectivity
#   - If CRS_EXTERNAL_NETWORK is set: join the existing external network (for oss-crs)
#   - If not set: create a local network (for standalone testing)
if [ -n "${CRS_EXTERNAL_NETWORK:-}" ]; then
    echo "Using external network for LiteLLM: $CRS_EXTERNAL_NETWORK"
    export CRS_NETWORK_EXTERNAL="true"
    # CRS_EXTERNAL_NETWORK is already set by the caller
else
    echo "Using local networks (standalone mode)"
    export CRS_NETWORK_EXTERNAL="false"
fi

# Set unique external network name for standalone mode
if [ "${CRS_NETWORK_EXTERNAL}" = "false" ]; then
    # Use unique network name per project/harness to avoid conflicts
    export CRS_EXTERNAL_NETWORK="${SAFE_TARGET}_${SAFE_HARNESS}_external"
    echo "External network (local): $CRS_EXTERNAL_NETWORK"
fi

# Generate harness-specific crs.config to avoid conflicts with concurrent runs
HOST_CRS_CONFIG="${HOST_OUT_DIR}/crs.config.${SAFE_HARNESS}"

# Convert CRS_INPUT_GENS from comma-separated to JSON array
# Default: given_fuzzer
# Options: given_fuzzer, concolic_input_gen, testlang_input_gen, dict_input_gen, mlla
INPUT_GENS="${CRS_INPUT_GENS:-given_fuzzer}"
# Convert "a,b,c" to ["a", "b", "c"]
INPUT_GENS_JSON=$(echo "$INPUT_GENS" | sed 's/,/", "/g' | sed 's/^/["/' | sed 's/$/"]/')
echo "Input generators: $INPUT_GENS_JSON"

cat > "/out/crs.config.${SAFE_HARNESS}" << EOF
{
    "target_harnesses": ["${HARNESS_NAME}"],
    "modules": ["uniafl"],
    "others": {
        "input_gens": ${INPUT_GENS_JSON}
    }
}
EOF

export HOST_CRS_CONFIG
echo "Generated crs.config at $HOST_CRS_CONFIG:"
cat "/out/crs.config.${SAFE_HARNESS}"

# Start services with docker compose
# COMPOSE_PROJECT_NAME is set at the top of the script for cleanup trap
echo "Starting services with docker compose (project: $COMPOSE_PROJECT_NAME)..."
cd /app

# Check if other services are needed (mlla or testlang_input_gen)
# These require: redis, joern, codeindexer, lsp (profile: others)
NEEDS_OTHER_SERVICES=false
if echo "$INPUT_GENS" | grep -qE "(mlla|testlang_input_gen)"; then
    NEEDS_OTHER_SERVICES=true
    # MLLA mode requires CRS_TARGET and LSP runner image
    if [ -z "${CRS_TARGET:-}" ]; then
        echo "ERROR: CRS_TARGET must be set for MLLA mode"
        exit 1
    fi
    if [ -z "$LSP_RUNNER" ]; then
        echo "ERROR: LSP_RUNNER not set. CRS_TARGET is required for MLLA mode."
        exit 1
    fi
    if ! docker image inspect "$LSP_RUNNER" > /dev/null 2>&1; then
        echo "ERROR: LSP runner image not found: $LSP_RUNNER"
        echo "Ensure builder phase created the LSP runner image for project: $CRS_TARGET"
        exit 1
    fi
    echo "  Found: $LSP_RUNNER"
fi

# Run services using appropriate compose file
# - docker-compose.yml: fuzzing-only mode (redis + crs)
# - docker-compose.mlla.yml: mlla/testlang mode (redis -> codeindexer, joern, lsp -> crs)
set +e
if [ "$NEEDS_OTHER_SERVICES" = "true" ]; then
    echo "Using docker-compose.mlla.yml (mlla or testlang detected)"
    COMPOSE_FILE="docker-compose.mlla.yml"
    # Use up -d + wait for mlla mode (has one-shot codeindexer service)
    # --exit-code-from implies --abort-on-container-exit which aborts when codeindexer exits
    docker compose -f "$COMPOSE_FILE" up -d
    CRS_CONTAINER="crs_${SAFE_TARGET}_${SAFE_HARNESS}"
    echo "Waiting for crs container to complete..."
    docker wait "$CRS_CONTAINER"
    EXIT_CODE=$?
else
    echo "Using docker-compose.yml (fuzzing-only mode)"
    COMPOSE_FILE="docker-compose.yml"
    # Fuzzing-only mode has no one-shot services, --exit-code-from works fine
    docker compose -f "$COMPOSE_FILE" up --exit-code-from crs
    EXIT_CODE=$?
fi
set -e

# Cleanup containers
echo "Cleaning up containers..."
docker compose -f "$COMPOSE_FILE" down --remove-orphans 2>/dev/null || true

echo "=== Run complete (exit code: $EXIT_CODE) ==="
exit $EXIT_CODE
