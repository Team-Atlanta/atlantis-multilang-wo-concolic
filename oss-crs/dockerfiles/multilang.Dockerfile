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
FROM multilang-crs:latest

COPY --from=libcrs . /libCRS
RUN /libCRS/install.sh --cli

WORKDIR /home/crs

COPY bin/* /usr/local/bin/

ENTRYPOINT ["/usr/local/bin/multilang_entrypoint"]
