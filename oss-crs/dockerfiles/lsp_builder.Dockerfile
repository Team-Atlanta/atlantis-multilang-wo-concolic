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
FROM atlantis-multilang-wo-concolic-deps:latest AS multilang-deps
FROM oss-crs-deps:latest AS oss-crs-deps
FROM ${target_base_image}

COPY --from=oss-crs-deps /nix/store /nix/store
COPY --from=oss-crs-deps /usr/local/bin/libCRS /usr/local/bin/libCRS
COPY --from=oss-crs-deps /usr/local/bin/rsync  /usr/local/bin/rsync

COPY --from=multilang-deps /nix/store /nix/store
COPY --from=multilang-deps /usr/local/bin/bear /usr/local/bin/bear

RUN cp /usr/local/bin/compile /usr/local/bin/compile.orig
COPY ./lsp/bear.yml /work/bear.yml
COPY ./scripts/lsp-prepare.sh /lsp-prepare.sh
CMD ["/lsp-prepare.sh"]
