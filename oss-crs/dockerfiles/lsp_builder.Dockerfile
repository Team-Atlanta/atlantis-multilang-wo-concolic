# =============================================================================
# CRS-multilang Builder Dockerfile (wo-concolic variant)
# =============================================================================
# BUILD phase: Sets up build tools, compilation happens at runtime via compile_crs.
#
# This Dockerfile only prepares the build environment (LLVM, Jazzer, compile script).
# Actual compilation is deferred to `docker compose run` where source code is
# available via the framework's source injection (docker-commit).
#
# Build args (provided by OSS-CRS):
#   - parent_image: Project image with deps (e.g., gcr.io/oss-fuzz/json-c)
#   - CRS_TARGET: Target project name
#   - FUZZING_LANGUAGE: Project language (c, c++, rust, go, python, jvm)
# =============================================================================

ARG target_base_image
FROM ${target_base_image}
RUN apt update && apt install -y bear && rm -rf /var/lib/apt/lists/*

COPY --from=libcrs . /libCRS
RUN /libCRS/install.sh

RUN cp /usr/local/bin/compile /usr/local/bin/compile.orig
COPY ./scripts/lsp-prepare.sh /lsp-prepare.sh
CMD ["/lsp-prepare.sh"]
