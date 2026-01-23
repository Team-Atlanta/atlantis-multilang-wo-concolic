################################################################################
### This is for archieving files for CP build
################################################################################

FROM ghcr.io/aixcc-finals/base-builder:v1.3.0
COPY --from=multilang-builder /opt/llvm-patched /multilang-builder/llvm-patched
COPY --from=multilang-builder /usr/local/lib/clang/18/lib/x86_64-unknown-linux-gnu/libclang_rt.fuzzer.a /multilang-builder/libclang_rt.fuzzer.a
COPY --from=multilang-builder --chmod=777 /usr/local/bin/compile /multilang-builder/compile
