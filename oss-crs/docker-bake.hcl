# =============================================================================
# CRS-multilang Docker Bake Configuration (wo-concolic variant)
# =============================================================================
#
# Replaces multilang-all.sh with parallel builds and proper dependency tracking.
#
# Build order (with parallelism):
#   parallel:  multilang-clang ─────────┐
#                                                    ├──► multilang-builder ──► multilang-crs
#   parallel:  multilang-builder-jvm ───┘
#
# Usage (OSS-CRS prepare phase - uses cached images from registry):
#   docker buildx bake prepare        # Pull cached images from registry (default)
#   docker buildx bake prepare-c      # C/C++ images only
#   docker buildx bake prepare-jvm    # JVM images only
#
# Build from scratch (Team Atlanta):
#   USE_PREBUILT=false docker buildx bake prepare
#   docker buildx bake --push prepare  # Build and push to registry
#
# Show build plan:
#   docker buildx bake --print
#
# =============================================================================

variable "BASE_IMAGES_DIR" {
  default = "libs/oss-fuzz/infra/base-images"
}

variable "REGISTRY" {
  default = "ghcr.io/team-atlanta/atlantis-multilang-wo-concolic"
}

variable "VERSION" {
  default = "1.0.0"
}

# When true (default): Pull cached images from registry, build only if cache miss
# When false: Build everything from scratch locally
variable "USE_PREBUILT" {
  default = true
}

# OCI labels for automatic GitHub repository linking
variable "REPO_URL" {
  default = "https://github.com/Team-Atlanta/atlantis-multilang-wo-concolic"
}

# Helper function to generate tags
function "tags" {
  params = [name]
  result = [
    "${REGISTRY}/${name}:${VERSION}",
    "${REGISTRY}/${name}:latest",
    "${name}:latest"
  ]
}

# Common labels applied to all targets
function "oci_labels" {
  params = []
  result = {
    "org.opencontainers.image.source" = "${REPO_URL}"
  }
}

# Helper to get image source (registry or local build target)
function "image_source" {
  params = [name]
  result = USE_PREBUILT ? "docker-image://${REGISTRY}/${name}:${VERSION}" : "target:${name}"
}

# -----------------------------------------------------------------------------
# Groups
# -----------------------------------------------------------------------------

group "default" {
  targets = ["prepare"]
}

group "prepare" {
  targets = ["multilang-clang", "multilang-builder", "multilang-builder-jvm", "multilang-c-archive", "multilang-jvm-archive", "multilang-crs", "multilang-joern", "atlantis-multilang-wo-concolic-deps"]
}

group "prepare-c" {
  targets = ["multilang-clang", "multilang-builder", "multilang-c-archive", "multilang-crs", "atlantis-multilang-wo-concolic-deps"]
}

group "prepare-jvm" {
  targets = ["multilang-builder-jvm", "multilang-jvm-archive", "atlantis-multilang-wo-concolic-deps"]
}

# Archive images for extracting build artifacts
group "archives" {
  targets = ["multilang-c-archive", "multilang-jvm-archive"]
}

# -----------------------------------------------------------------------------
# Base Images (PREPARE phase)
# -----------------------------------------------------------------------------

# Custom LLVM/Clang with fuzzing support
# Independent - can build in parallel with multilang-builder-jvm
target "multilang-clang" {
  context    = "${BASE_IMAGES_DIR}/multilang-clang"
  dockerfile = "Dockerfile"
  tags       = tags("multilang-clang")
  labels     = oci_labels()
  cache-from = USE_PREBUILT ? ["type=registry,ref=${REGISTRY}/multilang-clang:${VERSION}"] : []
}

# C/C++ builder with Rust, Python, compile scripts
# Depends on multilang-clang
target "multilang-builder" {
  context    = "${BASE_IMAGES_DIR}/base-builder"
  dockerfile = "Dockerfile.multilang"
  tags       = tags("multilang-builder")
  labels     = oci_labels()
  contexts = {
    multilang-clang = image_source("multilang-clang")
  }
  cache-from = USE_PREBUILT ? ["type=registry,ref=${REGISTRY}/multilang-builder:${VERSION}"] : []
}

# JVM builder with Jazzer
# Independent - can build in parallel with multilang-clang
target "multilang-builder-jvm" {
  context    = "${BASE_IMAGES_DIR}/base-builder-jvm"
  dockerfile = "Dockerfile.multilang"
  tags       = tags("multilang-builder-jvm")
  labels     = oci_labels()
  cache-from = USE_PREBUILT ? ["type=registry,ref=${REGISTRY}/multilang-builder-jvm:${VERSION}"] : []
}

# -----------------------------------------------------------------------------
# CRS Main Image
# -----------------------------------------------------------------------------

# Main CRS image with UniAFL, llvm-cov-custom, FuzzDB, libCRS
target "multilang-crs" {
  context    = "."
  dockerfile = "oss-crs/dockerfiles/multilang-base-runner.Dockerfile"
  tags       = tags("multilang-crs")
  labels     = oci_labels()
  cache-from = USE_PREBUILT ? ["type=registry,ref=${REGISTRY}/multilang-crs:${VERSION}"] : []
}

target "multilang-joern" {
  context    = "."
  dockerfile = "joern/Dockerfile"
  tags       = tags("multilang-joern")
  labels     = oci_labels()
  cache-from = USE_PREBUILT ? ["type=registry,ref=${REGISTRY}/multilang-joern:${VERSION}"] : []
}

# Offline Python and language-server runtime copied into the target-dependent
# LSP image during build-target.
target "atlantis-multilang-wo-concolic-deps" {
  context    = "."
  dockerfile = "oss-crs/deps/deps.Dockerfile"
  tags       = tags("atlantis-multilang-wo-concolic-deps")
  labels     = oci_labels()
  cache-from = USE_PREBUILT ? ["type=registry,ref=${REGISTRY}/atlantis-multilang-wo-concolic-deps:${VERSION}"] : []
}

# -----------------------------------------------------------------------------
# Archive Images (for extracting build artifacts)
# -----------------------------------------------------------------------------

# C/C++ archive - extracts llvm-patched, libclang_rt.fuzzer.a, compile
target "multilang-c-archive" {
  context    = "."
  dockerfile = "oss-crs/dockerfiles/c-archive.Dockerfile"
  tags       = tags("multilang-c-archive")
  labels     = oci_labels()
  contexts = {
    multilang-builder = image_source("multilang-builder")
  }
  cache-from = USE_PREBUILT ? ["type=registry,ref=${REGISTRY}/multilang-c-archive:${VERSION}"] : []
}

# JVM archive - extracts Jazzer artifacts
target "multilang-jvm-archive" {
  context    = "."
  dockerfile = "oss-crs/dockerfiles/jvm-archive.Dockerfile"
  tags       = tags("multilang-jvm-archive")
  labels     = oci_labels()
  contexts = {
    multilang-builder-jvm = image_source("multilang-builder-jvm")
  }
  cache-from = USE_PREBUILT ? ["type=registry,ref=${REGISTRY}/multilang-jvm-archive:${VERSION}"] : []
}
