# CRS-Multilang OSS-CRS Integration Notes

This document summarizes the findings, bugs, fixes, and challenges encountered when integrating CRS-multilang with OSS-CRS.

## Overview

CRS-multilang uses a host Docker socket architecture for both build and run phases, which required careful handling of path mappings between containers and the host filesystem.

---

## Architecture Evolution: DinD to Host Docker Socket

### Initial Approach: Docker-in-Docker (DinD)

The initial integration attempted to use Docker-in-Docker (DinD) architecture, where a Docker daemon runs inside the builder/runner containers. This approach seemed natural since CRS-multilang heavily uses Docker for building and running fuzzing containers.

**DinD Implementation:**
```
oss-crs (host)
└── Builder Container (DinD)
    └── Docker Daemon
        └── CRS Build Containers
```

**Key Files (now removed):**
- `oss-crs/config.sh` - DinD configuration
- `oss-crs/load-cache.sh` - Cache loading into DinD
- `oss-crs/prepare-cache.sh` - Cache preparation scripts
- `oss-crs/verify-cache.sh` - Cache verification

### DinD Performance Problems

DinD suffered from severe performance issues:

1. **Image Loading Overhead**: CRS-multilang images are large (multiple GB). Loading these into a fresh DinD daemon on every build was extremely slow, even with caching.

2. **No Layer Sharing**: DinD cannot share layers with the host Docker daemon, resulting in duplicate storage and no benefit from host-side layer caching.

3. **Build Time**: Building CRS images inside DinD meant rebuilding from scratch each time, as layer cache was not persistent.

4. **Cache Management Complexity**: Required complex scripts to export/import Docker images as tarballs, adding I/O overhead.

### AIXCC Architecture Incompatibility

The AIXCC evaluation framework expects a specific container interface:
- Builder and Runner are separate containers
- Each receives specific volume mounts (`/work`, `/out`)
- No nested Docker daemons expected

DinD added complexity that didn't align with this model, requiring workarounds for networking, storage, and process isolation.

### Solution: Host Docker Socket

Switched to mounting the host Docker socket (`/var/run/docker.sock`) into builder/runner containers:

```
oss-crs (host)
├── Docker Daemon (host)
│   ├── CRS Build Containers (direct)
│   └── CRS Runner Containers (direct)
├── Builder Container (docker CLI only)
└── Runner Container (docker CLI only)
```

**Benefits:**
- **Instant layer caching**: Host daemon's layer cache is immediately available
- **No image loading**: Images built once are available everywhere
- **Simpler architecture**: No nested daemons to manage
- **AIXCC compatible**: Fits expected builder/runner container model

**Trade-offs:**
- Requires `HOST_WORK_DIR` and `HOST_OUT_DIR` environment variables for path mapping
- Must handle path translation between container and host paths
- Host Docker daemon is shared (potential isolation concerns in multi-tenant scenarios)

### Key Commits

```
252a7abb4 feat(dind): add builder container for oss-crs integration
5708cca07 feat(dind): add runner container for oss-crs integration
db55dbbd6 feat(dind): add cache loading and runtime scripts
c0687c85a refactor(build): use host docker socket instead of DinD for layer caching
6e405bcbf feat: add host docker socket mode with HOST_WORK_DIR/HOST_OUT_DIR env vars
6d0103edb feat(runner): use host docker socket instead of DinD
e79f491bc chore: remove old DinD cache scripts and update README
```

---

## Output Format Incompatibility

### OSS-CRS Default Interface

OSS-CRS provides builders and runners with two volume mounts:
- `/work` - Build working directory
- `/out` - Build output directory

Both are designed for the **build process**, not for storing fuzzing results.

### CRS-Multilang Output Needs

CRS-multilang produces various outputs during fuzzing:
- **POVs**: Proof-of-vulnerability inputs
- **Corpus**: Test cases discovered during fuzzing
- **Coverage data**: Code coverage information
- **Workdir**: Full working directory with intermediate data

These are stored in `/crs-workdir` inside the container, which is ephemeral.

### The `/out` Directory Problem

Using `/out` for results was problematic:
- `/out` is cleaned on each rebuild
- Build outputs (fuzzers) and fuzzing results (POVs, corpus) have different lifecycles
- Mixing them causes data loss on rebuild

### Solution: `/artifacts` Directory

Introduced `HOST_ARTIFACT_DIR` mapped to `/artifacts`:

```
HOST_OUT_DIR → /out           # Build outputs (ephemeral)
HOST_ARTIFACT_DIR → /artifacts # Results (persistent)
```

**Structure:**
```
HOST_ARTIFACT_DIR/
├── tarballs/          # Build artifacts (repo, project, fuzzers)
├── povs/              # POV files
├── corpus/            # Corpus files
└── crs-data/          # CRS runtime data
    ├── workdir_result/    # Full workdir backup
    └── eval_result/       # Evaluation results
```

This separation ensures:
- Build outputs can be cleaned without losing results
- Results survive across multiple build/run cycles
- Clear distinction between build artifacts and fuzzing results

---

## Key Challenges

### 1. Host Docker Socket Path Mapping

**Problem:** When using host Docker socket from within a container, volume mount paths must be host paths, not container paths.

```
Builder Container                    Host Docker Daemon
┌─────────────────────┐              ┌─────────────────────┐
│ /work/artifacts/... │──docker run──│ Looks for           │
│ (exists here)       │   -v /work.. │ /work/artifacts/... │
│                     │              │ (doesn't exist!)    │
└─────────────────────┘              └─────────────────────┘
```

**Solution:** Implemented path conversion in `helper.py` using `HOST_WORK_DIR` and `HOST_OUT_DIR` environment variables to translate container paths to host paths for Docker volume mounts.

**Files Modified:**
- `libs/oss-fuzz/infra/helper.py` - Added `_to_host_path()` function
- `run.py` - Added `to_host_path()` method and path conversion logic

### 2. Build Output Separation (HOST_OUT_SUBDIR)

**Problem:** Different build types (main, symcc, coverage, lsp) were overwriting each other's outputs when stored in the same directory.

**Solution:** Added `HOST_OUT_SUBDIR` mechanism to create separate output directories for each build type.

```python
# Example: symcc build uses separate output
HOST_OUT_SUBDIR=symcc → outputs to HOST_OUT_DIR/symcc/
```

**Files Modified:**
- `run.py` - Added `HOST_OUT_SUBDIR` handling in `__run_build()`
- `oss-crs/build.sh` - Exports `HOST_OUT_SUBDIR` for symcc/coverage/lsp builds
- `libs/oss-fuzz/infra/helper.py` - Reads `HOST_OUT_SUBDIR` and adjusts paths

### 3. Docker Network Isolation

**Problem:** Multiple concurrent fuzzing runs had container name and network collisions.

**Solution:**
- Use project-scoped networks (`crs-internal`) for Redis/Joern isolation
- Unique container names using `${SAFE_TARGET}_${SAFE_HARNESS}` pattern
- Sanitize names to handle special characters

**Files Modified:**
- `oss-crs/docker-compose.yml` - Added network configuration
- `oss-crs/run.sh` - Added `sanitize_name()` function and network setup

### 4. Results/Output Storage

**Problem:** Fuzzing results (corpus, POVs, coverage) were lost when containers exited because `/crs-workdir` was not mounted.

**Solution:**
- Mount `HOST_ARTIFACT_DIR` to `/artifacts` in container
- Copy results from `/crs-workdir` to `/artifacts` at end of run
- Added `CRS_SKIP_SAVE` option to disable if needed

**Files Modified:**
- `oss-crs/docker-compose.yml` - Added `/artifacts` volume mount
- `bin/run_crs` - Added result saving logic
- `oss-crs/run.sh` - Pass through `CRS_SKIP_SAVE`

### 5. CPU Count vs Cpuset Mismatch

**Problem:** When using Docker's `cpuset` to limit container to specific CPUs (e.g., `cpuset: 0-7`), CRS-multilang still spawned fuzzers based on host's total CPU count.

```
Host: 128 CPUs
Container cpuset: 0-7 (8 CPUs)
Fuzzers spawned: 128 (wrong!)
Result: 128 processes competing for 8 cores → severe contention
```

**Root Cause:** Python's `os.cpu_count()` returns the host's total CPU count, ignoring cgroup/cpuset restrictions.

**Solution:** Use `os.sched_getaffinity(0)` which returns the set of CPUs the process can actually use, respecting cpuset limits.

```python
def get_available_cpus() -> int:
    """Get CPUs available to this process (respects cpuset/cgroup)."""
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 1
```

**Files Modified:**
- `libs/libCRS/libCRS/config.py` - Added `get_available_cpus()`, replaced `os.cpu_count()` calls
- `libs/libCRS/libCRS/__init__.py` - Export `get_available_cpus`
- `bin/main.py` - Use `get_available_cpus()` for shm_size calculation

### 6. Signal Handling for Result Saving

**Problem:** When fuzzing was interrupted with Ctrl+C (SIGINT) or `docker stop` (SIGTERM), results were not saved because the cleanup code never ran.

**Root Cause:** Multiple issues:
1. Bash trap handlers are deferred while waiting on foreground processes
2. Docker sends signals to PID 1 only, not to child processes
3. If main.py doesn't respond to SIGTERM, cleanup blocks indefinitely
4. Docker-compose spawned containers keep running after runner exit

**Solution (Multi-layer):**

**Layer 1: CRS Container (`bin/run_crs`)**
- Run `main.py` in background with `wait` (interruptible by signals)
- Add `init: true` to docker-compose.yml for tini as PID 1
- Trap handler with 5-second timeout and SIGKILL fallback

```bash
cleanup() {
    if [ -n "$MAIN_PID" ] && kill -0 $MAIN_PID 2>/dev/null; then
        kill -TERM $MAIN_PID 2>/dev/null
        # Wait up to 5 seconds for graceful shutdown
        for i in 1 2 3 4 5; do
            if ! kill -0 $MAIN_PID 2>/dev/null; then break; fi
            sleep 1
        done
        # Force kill if still running
        if kill -0 $MAIN_PID 2>/dev/null; then
            kill -KILL $MAIN_PID 2>/dev/null
        fi
        wait $MAIN_PID 2>/dev/null
    fi
    save_results
    exit 130
}

trap cleanup INT TERM
main.py &
MAIN_PID=$!
wait $MAIN_PID
```

**Layer 2: Runner Container (`oss-crs/run.sh`)**
- Trap signals to stop docker-compose services on interrupt
- Set COMPOSE_PROJECT_NAME early so cleanup can use it

```bash
# Set project name early for cleanup function
export COMPOSE_PROJECT_NAME="${SAFE_TARGET}_${SAFE_HARNESS}"

cleanup() {
    echo "=== Signal received, stopping services... ==="
    cd /app 2>/dev/null || true
    docker compose down --remove-orphans 2>/dev/null || true
    exit 130
}

trap cleanup INT TERM
```

**Files Modified:**
- `bin/run_crs` - Background process, trap handler with timeout/SIGKILL fallback
- `oss-crs/run.sh` - Signal trap for docker-compose cleanup
- `oss-crs/docker-compose.yml` - Added `init: true` for tini as PID 1

### 7. Code Indexer Initialization

**Problem:** Code indexer was not running in the oss-crs docker-compose setup. During standalone builds, it's initialized via `target.run({"init_codeindexer": True})` in run.py, but in the oss-crs setup, the crs container runs `run_crs` directly without initializing the code index.

**Impact:** LLM agents that depend on the code index (for code search, understanding, etc.) would fail or have degraded functionality.

**Solution:** Added `codeindexer` as a separate service in docker-compose.yml. This ensures:
1. Tarballs (project.tar.gz, repo.tar.gz) are extracted to /src/
2. Code index is built and stored in Redis
3. CRS waits for codeindexer to complete before starting (via depends_on)
4. Agents can query the code index during fuzzing

```yaml
codeindexer:
  image: crs-multilang/crs-multilang:latest
  container_name: codeindexer_${SAFE_TARGET}_${SAFE_HARNESS}
  environment:
    - PYTHONUNBUFFERED=1
    - TARBALL_DIR=/tarballs
    - CODE_INDEXER_REDIS_URL=redis://redis:6379
  volumes:
    - ${HOST_ARTIFACT_DIR:-/out}/tarballs:/tarballs:ro
  depends_on:
    - redis
  networks:
    - crs-internal
  command: ["init_codeindexer"]
  restart: "no"  # Exit after indexing complete
```

**Files Modified:**
- `oss-crs/docker-compose.yml` - Added `codeindexer` service, added dependency from `crs`

### 8. LSP Service Integration

**Problem:** LSP (Language Server Protocol) server was not running in the oss-crs docker-compose setup. LSP provides code navigation and analysis features used by LLM agents.

**Solution:** Added `lsp` as a separate service in docker-compose.yml:
1. Build phase tags project-specific LSP runner image (`multilang-lsp-{project}`)
2. Runner phase reads project name and sets LSP_RUNNER environment variable
3. CRS container connects to LSP via internal network

```yaml
lsp:
  image: ${LSP_RUNNER:-crs-multilang/multilang-lsp-base:latest}
  container_name: lsp_${SAFE_TARGET}_${SAFE_HARNESS}
  restart: unless-stopped
  environment:
    - CRS_TARGET=${CRS_TARGET:-}
  volumes:
    - ${HOST_ARTIFACT_DIR:-/out}/tarballs:/tarballs:ro
  depends_on:
    - redis
  networks:
    - crs-internal
  ports:
    - "3303"
```

**Files Modified:**
- `oss-crs/build.sh` - Save project name, tag LSP runner image
- `oss-crs/run.sh` - Read project name and set LSP_RUNNER env var
- `oss-crs/docker-compose.yml` - Added `lsp` service, LSP_URL env var, dependency from `crs`

### 9. Configurable Input Generators

**Problem:** The input generators were hardcoded to `["given_fuzzer"]` in crs.config, preventing users from enabling additional fuzzing techniques.

**Solution:** Added `CRS_INPUT_GENS` environment variable to configure input generators at runtime.

**Available Options:**
- `given_fuzzer` - Use provided seed corpus (default)
- `concolic_input_gen` - Concolic execution for input generation
- `testlang_input_gen` - Test language-based input generation
- `dict_input_gen` - Dictionary-based input generation
- `mlla` - Multi-language LLM agent for input generation

**Usage:** Set `CRS_INPUT_GENS` as comma-separated list:
```bash
CRS_INPUT_GENS=given_fuzzer,mlla,dict_input_gen
```

**Implementation in `run.sh`:**
```bash
# Convert CRS_INPUT_GENS from comma-separated to JSON array
INPUT_GENS="${CRS_INPUT_GENS:-given_fuzzer}"
INPUT_GENS_JSON=$(echo "$INPUT_GENS" | sed 's/,/", "/g' | sed 's/^/["/' | sed 's/$/"]/')

cat > "/out/crs.config.${SAFE_HARNESS}" << EOF
{
    "target_harnesses": ["${HARNESS_NAME}"],
    "modules": ["uniafl"],
    "others": {
        "input_gens": ${INPUT_GENS_JSON}
    }
}
EOF
```

**Files Modified:**
- `oss-crs/run.sh` - Added `CRS_INPUT_GENS` parsing and crs.config generation

### 10. CPU Constraints for All Services

**Problem:** Only the main CRS container had CPU limits. Support services (redis, joern, codeindexer, lsp) could use CPUs outside the allocated set, potentially interfering with other workloads on the host.

**Solution:** Added `cpuset` constraint to all docker-compose services:

| Service | cpuset | mem_limit | Rationale |
|---------|--------|-----------|-----------|
| redis | `${CPUSET_CPUS}` | - | Shares allocated CPUs |
| joern | `${CPUSET_CPUS}` | - | Shares allocated CPUs |
| codeindexer | `${CPUSET_CPUS}` | - | Shares allocated CPUs |
| lsp | `${CPUSET_CPUS}` | - | Shares allocated CPUs |
| crs | `${CPUSET_CPUS}` | `${MEMORY_LIMIT}` | Main fuzzing workload |

All services share the same CPU set, ensuring they don't interfere with other workloads outside the allocated CPUs. Memory limits are only applied to the main CRS container.

**Files Modified:**
- `oss-crs/docker-compose.yml` - Added `cpuset` to all services

### 11. Separate Docker Compose Files for Different Modes

**Problem:** Services like joern, codeindexer, and lsp are only needed when certain input generators (mlla, testlang_input_gen) are configured. Additionally, `--exit-code-from` implies `--abort-on-container-exit`, which causes Docker Compose to abort when codeindexer (a one-shot service) exits after completing indexing.

**Solution:** Split into two separate docker-compose files:

| File | Mode | Services | Startup Strategy |
|------|------|----------|------------------|
| `docker-compose.yml` | Fuzzing-only | redis, crs, cleanup | `--exit-code-from crs` |
| `docker-compose.mlla.yml` | MLLA/Testlang | redis, codeindexer, joern, lsp, crs, cleanup | `up -d` + `docker wait` |

**Dependency Order in MLLA mode:**
```
redis → codeindexer (service_completed_successfully)
      → joern (service_started)
      → lsp (service_started)
            → crs (waits for all above)
```

**Implementation in `run.sh`:**
```bash
# Check if other services are needed (mlla or testlang_input_gen)
NEEDS_OTHER_SERVICES=false
if echo "$INPUT_GENS" | grep -qE "(mlla|testlang_input_gen)"; then
    NEEDS_OTHER_SERVICES=true
fi

# Run services using appropriate compose file
if [ "$NEEDS_OTHER_SERVICES" = "true" ]; then
    COMPOSE_FILE="docker-compose.mlla.yml"
    # Use up -d + wait for mlla mode (has one-shot codeindexer service)
    docker compose -f "$COMPOSE_FILE" up -d
    docker wait "crs_${SAFE_TARGET}_${SAFE_HARNESS}"
    EXIT_CODE=$?
else
    COMPOSE_FILE="docker-compose.yml"
    # Fuzzing-only mode has no one-shot services
    docker compose -f "$COMPOSE_FILE" up --exit-code-from crs
    EXIT_CODE=$?
fi
```

**Why two files instead of profiles:**
- Profiles with `--exit-code-from` still abort when any container exits
- Separate files allow different startup strategies per mode
- Each file is self-contained and easy to read
- `service_completed_successfully` ensures proper startup order in MLLA mode

**Files Modified:**
- `oss-crs/docker-compose.yml` - Fuzzing-only mode (redis, crs, cleanup)
- `oss-crs/docker-compose.mlla.yml` - MLLA mode with all services and proper dependencies
- `oss-crs/run.sh` - Selects compose file based on `CRS_INPUT_GENS`
- `runner.Dockerfile` - Copies both compose files to `/app/`

### 12. Always Save Eval Result

**Problem:** The `save_eval_result()` function generates valuable analysis data (seed creation times, coverage over time, crash reports, summary), but it only ran in eval mode (`EVAL_SEC > 0`). Non-eval runs didn't get this analysis data.

**Solution:** Modified `bin/main.py` to always call `save_eval_result()` after `crs.run(True)` completes, unless:
1. Already in eval mode (eval mode saves during `async_evaluate()`)
2. `CRS_SKIP_SAVE=True` is set (allows oss-crs to skip when not needed)

**Implementation:**
```python
start_time = int(time.time())
crs.run(True)

# Always save eval result after run completes (eval mode already saves during run)
# Skip if CRS_SKIP_SAVE is set (used by oss-crs to avoid redundant saves)
if not is_eval() and os.environ.get("CRS_SKIP_SAVE") != "True":
    eval_time = int(time.time()) - start_time
    asyncio.run(crs.save_eval_result(eval_time))
```

**Output:** Creates `/artifacts/crs-data/eval_result/` with:
- Seed creation times
- Coverage over time data
- Crash reports
- Summary statistics

**Files Modified:**
- `bin/main.py` - Added `save_eval_result()` call after `crs.run(True)`

### 13. Container Cleanup Sidecar

**Problem:** When the runner container is stopped externally (e.g., by oss-crs timeout or `docker stop`), the containers spawned by docker-compose (redis, crs, joern, etc.) continue running as orphans on the host Docker daemon. This happens because:
1. Runner uses host Docker socket, so spawned containers are siblings, not children
2. Docker doesn't have native post-stop hooks
3. `docker compose down` in trap handler doesn't run if runner is killed with SIGKILL

**Solution:** Implemented a cleanup sidecar container that monitors the runner and stops all services when runner exits:

```yaml
cleanup:
  image: docker:cli
  container_name: cleanup_${SAFE_TARGET}_${SAFE_HARNESS}
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
  environment:
    - RUNNER_CONTAINER_ID=${RUNNER_CONTAINER_ID:-}
    - COMPOSE_PROJECT_NAME
  entrypoint: ["/bin/sh", "-c"]
  command:
    - |
      echo "Cleanup sidecar started, monitoring runner: $RUNNER_CONTAINER_ID"
      if [ -z "$RUNNER_CONTAINER_ID" ]; then
        echo "WARNING: RUNNER_CONTAINER_ID not set, cleanup disabled"
        sleep infinity
      fi
      docker wait $RUNNER_CONTAINER_ID || true
      echo "Runner exited, stopping all services..."
      docker ps -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" | xargs -r docker stop
  restart: "no"
```

**How it works:**
1. Runner detects its own container ID via `/proc/self/cgroup` or hostname
2. `RUNNER_CONTAINER_ID` is exported and passed to docker-compose
3. Cleanup sidecar uses `docker wait` to block until runner exits
4. When runner exits (for any reason), sidecar stops all containers in the compose project

**Files Modified:**
- `oss-crs/docker-compose.yml` - Added `cleanup` sidecar service
- `oss-crs/run.sh` - Added `RUNNER_CONTAINER_ID` detection and export

---

## Bugs Fixed

### Bug 1: Source Path Not Found in Nested Docker Build

**Symptom:** `clang: error: no such file or directory: '/src/mock-c/mock.c'`

**Root Cause:** Volume mount used container path (`/work/...`) instead of host path when building with host Docker socket.

**Fix:** Convert paths using `_to_host_path()` before passing to Docker.

### Bug 2: Redis URL Parsing Error

**Symptom:** CRS couldn't connect to Redis service.

**Root Cause:** Redis URL was being parsed incorrectly when service names were used.

**Fix:** Use Docker network service discovery with fixed URLs like `redis://redis:6379`.

### Bug 3: Container Name Collision

**Symptom:** `container name already in use` errors when running multiple instances.

**Root Cause:** All instances used same container names (`redis`, `joern`, `crs`).

**Fix:** Append `${SAFE_TARGET}_${SAFE_HARNESS}` to container names.

### Bug 4: `.aixcc/` Directory Missing

**Symptom:** Test metadata not present in build output.

**Root Cause:** `.aixcc/` is only copied when `CRS_TEST=True`, which is only set for test builds.

**Status:** Not a bug - intentional behavior. `.aixcc/` contains evaluation metadata only needed for test mode.

---

## Configuration Changes

### Environment Variables Added

| Variable | Purpose |
|----------|---------|
| `HOST_WORK_DIR` | Host path for `/work` directory mapping |
| `HOST_OUT_DIR` | Host path for `/out` directory mapping |
| `HOST_ARTIFACT_DIR` | Host path for artifacts (tarballs, results) |
| `HOST_OUT_SUBDIR` | Subdirectory for build type separation |
| `CRS_SKIP_SAVE` | Skip saving results to `/artifacts` |
| `CRS_EXTERNAL_NETWORK` | External network name for LiteLLM |
| `CRS_INPUT_GENS` | Comma-separated input generators (default: `given_fuzzer`) |
| `RUN_FUZZER_MODE` | Fuzzer execution mode (default: `interactive`) |
| `HELPER` | OSS-Fuzz helper mode flag (default: `True`) |
| `CRS_INTERACTIVE` | CRS interactive mode flag (default: `True`) |
| `SEED_SHARE_DIR` | Directory for shared seeds between harnesses |
| `LSP_SERVER_URL` | LSP server URL for code analysis |

### Docker Compose Files

**Fuzzing-only mode (`docker-compose.yml`):**
```yaml
services:
  redis:        # Always runs
  crs:          # depends_on redis (service_started)
  cleanup:      # Sidecar for container cleanup
```

**MLLA mode (`docker-compose.mlla.yml`):**
```yaml
services:
  redis:        # Always runs
  codeindexer:  # depends_on redis, restart: "no" (one-shot)
  joern:        # depends_on redis
  lsp:          # depends_on redis
  crs:          # depends_on codeindexer (service_completed_successfully),
                #            joern (service_started), lsp (service_started)
  cleanup:      # Sidecar for container cleanup
```

**Common configuration (both files):**
```yaml
volumes:
  - ${HOST_ARTIFACT_DIR:-/out}/tarballs:/tarballs:ro   # Build artifacts
  - ${HOST_ARTIFACT_DIR:-/out}:/artifacts              # Results output
  - ${HOST_OUT_DIR:-/out}:/out                         # Build output

environment:
  # Fuzzing configuration (must match run.py expectations)
  - FUZZING_ENGINE=${FUZZING_ENGINE:-libfuzzer}
  - SANITIZER=${SANITIZER:-address}
  - RUN_FUZZER_MODE=${RUN_FUZZER_MODE:-interactive}
  - HELPER=True
  - CRS_INTERACTIVE=True
  - SEED_SHARE_DIR=/seed_share_dir
  # Service URLs
  - CODE_INDEXER_REDIS_URL=redis://redis:6379
  - DICTGEN_REDIS_URL=redis://redis:6379
  - JOERN_URL=http://joern:9909
  - LSP_SERVER_URL=http://lsp:3303  # Note: LSP_SERVER_URL, not LSP_URL
  # Other
  - CRS_SKIP_SAVE=${CRS_SKIP_SAVE:-}
  - RUNNER_CONTAINER_ID=${RUNNER_CONTAINER_ID:-}       # For cleanup sidecar

networks:
  crs-internal:    # Project-scoped, isolated
  crs-external:    # Shared for LiteLLM
```

### API Key Configuration

**Path:** `/keys/api_key` (oss-crs convention)

```bash
# Read LiteLLM key from /keys/api_key (oss-crs convention)
if [ -f /keys/api_key ]; then
    export LITELLM_KEY="$(cat /keys/api_key)"
fi
```

---

## OSS-CRS Repository Changes

The following changes were made to the OSS-CRS repository:

### 1. Example Config (.env)

**File:** `example_configs/crs-multilang/.env`

Added:
```bash
# Skip saving results (povs, corpus, workdir) to /artifacts/
# Set to "True" to disable saving results (default: save results)
#CRS_SKIP_SAVE=True
```

### 2. Future Work (render_compose.py)

To fully support `skip_save` from config-crs.yaml, OSS-CRS's `render_compose.py` needs to:
1. Read `skip_save` option from config-crs.yaml
2. Set `CRS_SKIP_SAVE` environment variable when launching runner

Currently, `CRS_SKIP_SAVE` must be set manually in `.env` or environment.

---

## Output Structure

### Build Artifacts (`HOST_ARTIFACT_DIR/tarballs/`)

```
tarballs/
├── repo.tar.gz       # Project source code
├── project.tar.gz    # OSS-Fuzz project files
└── fuzzers.tar.gz    # Built fuzzer binaries
```

### Fuzzing Results (`HOST_ARTIFACT_DIR/`)

```
HOST_ARTIFACT_DIR/
├── tarballs/              # Build artifacts
├── povs/                  # POV files
├── corpus/                # Corpus from uniafl_corpus
├── crs-data/              # CRS runtime data
│   ├── workdir_result/    # Full workdir copy
│   │   ├── uniafl_corpus/
│   │   ├── uniafl_cov/
│   │   ├── pov/
│   │   ├── others_corpus/
│   │   └── uniafl/
│   └── eval_result/       # (if EVAL_SEC > 0)
└── crs.config.{harness}   # Runtime config
```

---

## Naming Conventions

| Container Path | Host Path | Notes |
|----------------|-----------|-------|
| `/artifacts` | `HOST_ARTIFACT_DIR` | Plural for consistency |
| `/tarballs` | `HOST_ARTIFACT_DIR/tarballs` | Read-only mount |
| `/out` | `HOST_OUT_DIR` | Build outputs |
| `/work` | `HOST_WORK_DIR` | Build working directory |
| `/crs-workdir` | (internal) | Runtime workdir, not mounted |

---

## Testing Checklist

- [ ] Build completes without path errors
- [ ] Multiple concurrent runs don't collide
- [ ] Results are saved to `HOST_ARTIFACT_DIR`
- [ ] `CRS_SKIP_SAVE=True` disables result saving
- [ ] Symcc/coverage/lsp builds use separate directories
- [ ] Redis and Joern services are accessible
- [ ] Cleanup sidecar stops containers when runner exits
- [ ] Profile "others" only starts joern/codeindexer/lsp when mlla/testlang enabled
- [ ] `CRS_INPUT_GENS` correctly configures input generators

---

## Commit Summary

### CRS-Multilang Repository

#### Runtime Fixes
```
c36e062c7 feat(main): always save eval_result after run, respecting CRS_SKIP_SAVE
b842af94e fix: use sched_getaffinity for CPU count to respect cpuset limits
b5e7cbed1 fix: handle signals properly for result saving on interrupt
83a249a4a fix: add timeout and SIGKILL fallback in signal handler to prevent blocking
f942c9b2f fix: add signal trap in run.sh to stop docker-compose services on interrupt
```

#### libCRS Integration
```
fb830353e refactor: convert libCRS from submodule to regular directory
```

#### Results Storage
```
9c9a2a96d refactor: rename /artifact to /artifacts for consistency with host path
942d49dcb feat(oss-crs): pass CRS_SKIP_SAVE from run.sh to docker-compose
1a80da369 feat(oss-crs): add CRS_SKIP_SAVE env var to docker-compose
ed5f3f195 feat: save fuzzing results to /artifacts/ by default in run_crs
85b4ab59d feat(oss-crs): add /artifacts volume mount for results storage
0306aa2e8 feat: use HOST_ARTIFACT_DIR for artifacts separate from oss-fuzz work/out dirs
```

#### Build Output Separation
```
66c958100 feat: add HOST_OUT_SUBDIR for coverage/symcc/lsp build separation
9eca34bee refactor(build): separate tarballs to /out/tarballs/ subdirectory
d59254aa4 refactor: eliminate /out/tarballs, use /out directly
```

#### Network & Container Isolation
```
b577ee649 fix(oss-crs): add network isolation and fix Redis URL parsing
803b52d20 feat(compose): add container names and resource limits for isolation
378414be0 feat(run): add harness-specific crs.config and sanitized container names
11c2e3714 fix(runner): add network connectivity, cleanup, and unique project names
```

#### Service Orchestration
```
2a279d473 refactor(oss-crs): split docker-compose into fuzzing-only and mlla modes
37d1940ae fix(oss-crs): add redis dependency to crs service
85a1fba4c feat(oss-crs): add cleanup sidecar and fix redis profile
fe36119ba feat(oss-crs): add configurable input generators and optional service profiles
acd484b65 fix(oss-crs): use CRS_TARGET env var for LSP runner image name
643b01c08 feat(oss-crs): add LSP service to docker-compose
```

#### DinD to Host Docker Socket Migration
```
252a7abb4 feat(dind): add builder container for oss-crs integration
5708cca07 feat(dind): add runner container for oss-crs integration
db55dbbd6 feat(dind): add cache loading and runtime scripts
cafba7825 fix(dind): align runner with oss-crs interface
687b1e77b feat(cache): support both .tar and .tar.gz formats, prefer .tar for speed
7e4b959e1 refactor(cache): mount cache in runner instead of copying from builder
c0687c85a refactor(build): use host docker socket instead of DinD for layer caching
6e405bcbf feat: add host docker socket mode with HOST_WORK_DIR/HOST_OUT_DIR env vars
6d0103edb feat(runner): use host docker socket instead of DinD
e79f491bc chore: remove old DinD cache scripts and update README
```

#### Build Fixes
```
708e48753 fix(build): add target.build() to compile fuzzers in build_for_multilang
ef038ee1d fix(build): capture source dir from parent WORKDIR before cd
c9b851506 fix(builder): remove WORKDIR to preserve parent's for oss-crs source copy
d4458f71c fix(builder): copy oss-fuzz project files from additional context
ae4bb286e fix(build): skip image pull when registry is 'local'
2243e2e54 fix(build): use run.py build_crs for CRS image building
18ea74967 fix(build): skip docker load, use parent image directly from host
5bf70cc78 fix(build): copy fuzzers.tar.gz instead of re-tarring entire /out
176538fb6 fix(build): create project.tar.gz from actual project directory
e174dff07 fix(run.py): convert to host path only in __construct_cmd for Docker mounts
```

#### Runner/Builder Simplification
```
64f07d2bb refactor(runner): simplify to docker-cli only orchestrator
1016e8a6b refactor(build): simplify source extraction with /src mount
97c68febd feat(builder): use parent_image as base with source at /src
23ea9aebd feat(run.py): add --start-other-services flag for build command
```

#### Infrastructure
```
f68175c00 fix(Dockerfile): build Python 3.10 from source instead of ppa
4dd715bc2 build: exclude cache directory from git and docker context
```

#### Documentation
```
7ca9f7804 docs: expand commit summary with all commits grouped by feature
04f7d1c51 docs: add CPU count and signal handling challenges to INTEGRATION.md
362371b24 docs: add DinD migration journey and output format sections to INTEGRATION.md
7cf381fa0 docs(oss-crs): add results output section and integration notes
6f65d1fa5 refactor: document CONTAINER_MODE vs HOST_MODE execution contexts
```

### OSS-CRS Repository

```
936dc50 refactor: rename /artifact to /artifacts in comment for consistency
a8bc8b3 feat(crs-multilang): add CRS_SKIP_SAVE option to example config
```
