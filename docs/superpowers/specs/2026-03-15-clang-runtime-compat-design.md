# Clang Runtime Compatibility Design

## Goal

Make the CRS builder pipeline work with both older AIxCC-style clang 18 builder images and newer OSS-Fuzz-style clang 22 builder images without hardcoding a clang major version.

## Problem

The current build flow hardcodes clang runtime paths in two places:

- `oss-crs/dockerfiles/c-archive.Dockerfile` reads `libclang_rt.fuzzer.a` from `/usr/local/lib/clang/18/...`
- `oss-crs/dockerfiles/builder.Dockerfile` writes `libclang_rt.fuzzer.a` into `/usr/local/lib/clang/22/...`

That creates version skew. It breaks when:

- our multilang builder still produces clang 18 layouts
- the target base image still expects clang 18 layouts
- both clang 18 and clang 22 runtime trees exist and downstream globbing resolves to multiple files

## Design

Use two stable behaviors instead of one hardcoded version:

1. In the multilang builder image, export `libclang_rt.fuzzer.a` from whichever clang runtime tree is actually present into a fixed path.
2. In the CRS target builder image, install that archived runtime into the active resource directory reported by `clang --print-resource-dir`.

The export helper should prefer the active compiler resource dir. If that is unavailable, it should only fall back to a directory scan when exactly one matching runtime exists; ambiguous mixed-version layouts should fail explicitly.

## File Changes

- Add a small script in `libs/oss-fuzz/infra/base-images/base-builder/` that locates the available `libclang_rt.fuzzer.a` and copies it to a fixed output path.
- Add a small script in `oss-crs/dockerfiles/` that installs the archived runtime into the active clang resource directory inside the target base image.
- Update `Dockerfile.multilang`, `oss-crs/dockerfiles/c-archive.Dockerfile`, and `oss-crs/dockerfiles/builder.Dockerfile` to use those stable paths.
- Add unit tests that simulate clang 18 and clang 22 layouts and verify both scripts.

## Constraints

- Must work for official OSS-Fuzz targets and original AIxCC `base-builder:v1.3.0` targets.
- Must not copy into multiple clang major directories, because that leaves downstream wildcard consumers vulnerable to multiple-match failures.
- Must keep the change localized to the runtime archive/install path logic.

## Verification

- Unit tests cover:
  - exporting from a fake clang 18 tree
  - exporting from a fake clang 22 tree
  - installing into a fake clang 18 resource dir
  - installing into a fake clang 22 resource dir
- Manual diff review confirms the Dockerfiles no longer hardcode clang major versions.
