# =============================================================================
# CRS-multilang Runner Dockerfile (given_fuzzer variant)
# =============================================================================
# Runtime image for OSS-CRS run phase.
# Uses multilang-given_fuzzer-crs as base with run-harness entrypoint.
#
# Usage:
#   docker buildx bake multilang-given_fuzzer-runner
#   docker run multilang-given_fuzzer-runner <harness_name> [args...]
# =============================================================================

# Docker will check local first, then pull from registry if not found
FROM multilang-crs:latest

COPY --from=libcrs . /libCRS
RUN /libCRS/install.sh

WORKDIR /home/crs

COPY bin/* /usr/local/bin/

ENTRYPOINT ["/usr/local/bin/run_crs"]
