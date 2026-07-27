# =============================================================================
# CRS-multilang Runner Dockerfile (wo-concolic variant)
# =============================================================================
# Runtime image for OSS-CRS run phase.
# Uses multilang-crs as base with run-harness entrypoint.
#
# Usage:
#   docker buildx bake multilang-runner
#   docker run multilang-runner <harness_name> [args...]
# =============================================================================

# Docker will check local first, then pull from registry if not found
FROM oss-crs-deps:latest AS oss-crs-deps
FROM multilang-crs:latest

COPY --from=oss-crs-deps /nix/store /nix/store
COPY --from=oss-crs-deps /usr/local/bin/libCRS /usr/local/bin/libCRS
COPY --from=oss-crs-deps /usr/local/bin/rsync  /usr/local/bin/rsync

WORKDIR /home/crs

COPY bin/* /usr/local/bin/

ENTRYPOINT ["/usr/local/bin/multilang_entrypoint"]
