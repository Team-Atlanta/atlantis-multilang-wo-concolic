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
ARG crs_version

# Reference archive images for CRS build tools
FROM multilang-c-archive AS crs-tools-c
FROM multilang-jvm-archive AS crs-tools-jvm

# =============================================================================
# Builder: parent_image + CRS tools
# =============================================================================
FROM oss-crs-deps:latest AS oss-crs-deps
FROM ${target_base_image}

COPY --from=oss-crs-deps /nix/store /nix/store
COPY --from=oss-crs-deps /usr/local/bin/libCRS /usr/local/bin/libCRS
COPY --from=oss-crs-deps /usr/local/bin/rsync  /usr/local/bin/rsync

COPY --from=crs-tools-c /multilang-builder/llvm-patched /opt/llvm-patched
COPY --from=crs-tools-c /multilang-builder/libclang_rt.fuzzer.a /tmp/libclang_rt.fuzzer.a
COPY oss-crs/dockerfiles/install_libclang_rt_fuzzer.sh /usr/local/bin/install_libclang_rt_fuzzer.sh
RUN chmod +x /usr/local/bin/install_libclang_rt_fuzzer.sh && \
    /usr/local/bin/install_libclang_rt_fuzzer.sh /tmp/libclang_rt.fuzzer.a && \
    rm -f /tmp/libclang_rt.fuzzer.a
COPY --from=crs-tools-c /multilang-builder/compile /usr/local/bin/compile
COPY --from=crs-tools-c /multilang-builder/compile_libfuzzer /usr/local/bin/compile_libfuzzer
COPY --from=crs-tools-jvm /multilang-builder/jazzer_agent_deploy.jar /usr/local/bin/jazzer_agent_deploy.jar
COPY --from=crs-tools-jvm /multilang-builder/jazzer_driver /usr/local/bin/jazzer_driver
COPY --from=crs-tools-jvm /multilang-builder/jazzer_api_deploy.jar /usr/local/lib/jazzer_api_deploy.jar
COPY --from=crs-tools-jvm /multilang-builder/jazzer_junit.jar /usr/local/bin/jazzer_junit.jar

COPY bin/compile_target /usr/local/bin/compile_target

CMD ["compile_target"]
