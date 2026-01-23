#!/bin/bash
set -eu

echo "=== CRS-Multilang Build Phase ==="
echo "Project name: $PROJECT_NAME"

# Capture source directory (parent's WORKDIR where oss-crs copied source)
# This must be done BEFORE cd'ing to /crs-multilang
SOURCE_DIR=$(pwd)
echo "Source directory: $SOURCE_DIR"

cd /crs-multilang

# Step 1: Verify Docker socket is available (needed for build_crs)
if ! docker info > /dev/null 2>&1; then
    echo "ERROR: Docker socket not available. Ensure /var/run/docker.sock is mounted."
    exit 1
fi
echo "Docker daemon accessible via host socket"

# Step 2: Determine tarball directory
# When HOST_ARTIFACT_DIR is set, use it for tarballs (no duplication in /out)
if [ -n "${HOST_ARTIFACT_DIR:-}" ]; then
    TARBALL_DIR="$HOST_ARTIFACT_DIR/tarballs"
    echo "Using artifact tarball directory: $TARBALL_DIR"
else
    TARBALL_DIR="/out/tarballs"
    echo "Using default tarball directory: $TARBALL_DIR"
fi
mkdir -p "$TARBALL_DIR"

# Step 2.5: Create repo.tar.gz from source (required by run.py build)
# CP_Builder expects repo.tar.gz to exist before building
echo "Creating repo.tar.gz from source directory..."
REPO_TARBALL="$TARBALL_DIR/repo.tar.gz"
if [ ! -f "$REPO_TARBALL" ]; then
    tar --use-compress-program=pigz -cf "$REPO_TARBALL" -C "$SOURCE_DIR" .
    echo "Created $REPO_TARBALL"
else
    echo "repo.tar.gz already exists, skipping creation"
fi

# Step 3: Build CRS docker images using run.py build_crs
echo "Building CRS docker images via run.py build_crs..."
python3 run.py build_crs

# Build multilang-runner-joern (not included in build_crs, but needed for runner)
echo "Building multilang-runner-joern..."
docker build -t multilang-runner-joern -f joern/Dockerfile .

# Tag images with namespace for compatibility
echo "Tagging images with namespace..."
docker tag crs-multilang crs-multilang/crs-multilang:latest
docker tag multilang-lsp-base crs-multilang/multilang-lsp-base:latest
docker tag multilang-c-archive crs-multilang/multilang-c-archive:latest
docker tag multilang-jvm-archive crs-multilang/multilang-jvm-archive:latest
docker tag multilang-runner-joern crs-multilang/multilang-runner-joern:latest

# Sanitize project name for Docker image naming (lowercase for registry compatibility)
SAFE_PROJECT=$(echo "$PROJECT_NAME" | tr '/' '_' | tr '[:upper:]' '[:lower:]')

# Step 4: Build fuzzers using run.py build
echo "Building fuzzers via run.py build..."
python3 run.py build \
    --target "$PROJECT_NAME" \
    --tar-dir "$TARBALL_DIR" \
    --out-dir /out \
    --focus "" \
    --registry local \
    --image-version latest \
    --skip-symcc-verification \
    --start-other-services

# Tag project-specific LSP runner image with namespace
LSP_RUNNER_IMAGE="multilang-lsp-$SAFE_PROJECT"
if docker image inspect "$LSP_RUNNER_IMAGE" > /dev/null 2>&1; then
    echo "Tagging LSP runner image: $LSP_RUNNER_IMAGE"
    docker tag "$LSP_RUNNER_IMAGE" "crs-multilang/$LSP_RUNNER_IMAGE:latest"
else
    echo "WARNING: LSP runner image not found: $LSP_RUNNER_IMAGE"
fi

# Pull redis if not present (runner will use it directly from host daemon)
if ! docker image inspect redis:latest > /dev/null 2>&1; then
    echo "Pulling redis:latest..."
    docker pull redis:latest
fi

# Mark build as complete
touch /out/DONE

echo "=== Build complete ==="
echo "Output in /out/:"
ls -la /out/
