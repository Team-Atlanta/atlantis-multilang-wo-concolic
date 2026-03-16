# Clang Runtime Compatibility Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CRS target builds work across old clang 18 AIxCC bases and newer clang 22 OSS-Fuzz bases without hardcoded clang-major runtime paths.

**Architecture:** Export `libclang_rt.fuzzer.a` from the multilang builder image into a stable fixed path, then install it into the target image's active clang resource directory detected at runtime. Keep the compatibility logic in small scripts so it can be unit-tested without Docker.

**Tech Stack:** Dockerfiles, POSIX shell, Python `unittest`

---

## Chunk 1: Tests First

### Task 1: Add failing tests for runtime export and install scripts

**Files:**
- Create: `tests/test_libclang_runtime_scripts.py`

- [ ] **Step 1: Write the failing test**

Create tests that:
- build fake clang runtime trees under temporary directories
- verify the export script can find `libclang_rt.fuzzer.a` from clang 18 and clang 22 layouts
- verify the install script writes the runtime into the resource dir reported by a fake `clang --print-resource-dir`

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -p 'test_libclang_runtime_scripts.py' -v`
Expected: FAIL because the scripts do not exist yet.

## Chunk 2: Runtime Path Helpers

### Task 2: Add export helper for the multilang builder image

**Files:**
- Create: `libs/oss-fuzz/infra/base-images/base-builder/export_libclang_rt_fuzzer.sh`

- [ ] **Step 1: Write minimal implementation**

Add a shell script that:
- takes the destination path as its first argument
- prefers the active compiler's resource dir from `${CLANG_BIN:-clang} --print-resource-dir`
- falls back to scanning `${CLANG_LIB_ROOT:-/usr/local/lib/clang}` only when exactly one matching runtime exists
- errors if none are found or if the fallback is ambiguous
- copies the selected runtime to the destination path

- [ ] **Step 2: Run test to verify the relevant export cases pass**

Run: `python3 -m unittest discover -s tests -p 'test_libclang_runtime_scripts.py' -v`
Expected: install-script tests still fail, export-script tests pass.

### Task 3: Add install helper for CRS target builder images

**Files:**
- Create: `oss-crs/dockerfiles/install_libclang_rt_fuzzer.sh`

- [ ] **Step 1: Write minimal implementation**

Add a shell script that:
- takes the archived runtime path as its first argument
- resolves the resource dir with `${CLANG_BIN:-clang} --print-resource-dir`
- installs the archive into `${resource_dir}/lib/${TARGET_TRIPLE_DIR:-$(uname -m)-unknown-linux-gnu}/libclang_rt.fuzzer.a`

- [ ] **Step 2: Run test to verify the full test file passes**

Run: `python3 -m unittest discover -s tests -p 'test_libclang_runtime_scripts.py' -v`
Expected: PASS

## Chunk 3: Dockerfile Wiring

### Task 4: Update multilang builder image to export a stable runtime artifact

**Files:**
- Modify: `libs/oss-fuzz/infra/base-images/base-builder/Dockerfile.multilang`

- [ ] **Step 1: Wire in the export helper**

Copy the helper into the image and run it so `/usr/local/lib/libclang_rt.fuzzer.a` is always present regardless of clang major version.

- [ ] **Step 2: Keep the change narrow**

Do not alter unrelated builder behavior.

### Task 5: Update CRS archive and builder Dockerfiles to use the stable artifact

**Files:**
- Modify: `oss-crs/dockerfiles/c-archive.Dockerfile`
- Modify: `oss-crs/dockerfiles/builder.Dockerfile`

- [ ] **Step 1: Make c-archive read from the stable exported path**

Replace the hardcoded `clang/18` source path with `/usr/local/lib/libclang_rt.fuzzer.a`.

- [ ] **Step 2: Make builder.Dockerfile install into the active clang resource dir**

Copy the archived runtime to a temporary location, add the install helper, and run it instead of writing to a hardcoded `clang/22` path.

- [ ] **Step 3: Re-run tests**

Run: `python3 -m unittest discover -s tests -p 'test_libclang_runtime_scripts.py' -v`
Expected: PASS

## Chunk 4: Verification

### Task 6: Final verification and review

**Files:**
- Verify: `libs/oss-fuzz/infra/base-images/base-builder/Dockerfile.multilang`
- Verify: `oss-crs/dockerfiles/c-archive.Dockerfile`
- Verify: `oss-crs/dockerfiles/builder.Dockerfile`
- Verify: `tests/test_libclang_runtime_scripts.py`

- [ ] **Step 1: Review the diff**

Run: `git diff -- libs/oss-fuzz/infra/base-images/base-builder/Dockerfile.multilang oss-crs/dockerfiles/c-archive.Dockerfile oss-crs/dockerfiles/builder.Dockerfile tests/test_libclang_runtime_scripts.py`
Expected: only the runtime-export/install path logic changes.

- [ ] **Step 2: Run the targeted tests one final time**

Run: `python3 -m unittest discover -s tests -p 'test_libclang_runtime_scripts.py' -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-03-15-clang-runtime-compat-design.md docs/superpowers/plans/2026-03-15-clang-runtime-compat.md tests/test_libclang_runtime_scripts.py libs/oss-fuzz/infra/base-images/base-builder/export_libclang_rt_fuzzer.sh libs/oss-fuzz/infra/base-images/base-builder/Dockerfile.multilang oss-crs/dockerfiles/install_libclang_rt_fuzzer.sh oss-crs/dockerfiles/c-archive.Dockerfile oss-crs/dockerfiles/builder.Dockerfile
git commit -m "fix: support clang runtime paths across builder versions"
```
