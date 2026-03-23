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
FROM multilang-joern:latest

COPY --from=libcrs . /libCRS
RUN /libCRS/install.sh

ENTRYPOINT ["/bin/bash", "/usr/local/bin/run_joern"]
