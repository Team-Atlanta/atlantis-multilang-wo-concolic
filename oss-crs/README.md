# OSS-CRS Host Docker Socket Integration

This directory contains scripts for running CRS-multilang using the host Docker socket for both build and run phases.

## Overview

The CRS-multilang system uses the host Docker daemon directly (via socket mounting) to leverage Docker's built-in layer caching. This provides:

1. **Fast builds** - Host Docker layer cache eliminates redundant image building
2. **No image transfer** - Runner accesses images directly from host daemon
3. **Simple architecture** - No DinD complexity, just Docker CLI with socket mount

## Quick Start

### 1. Build Fuzzers for a Project

```bash
# Builder uses host docker socket for layer caching
docker build -t crs-builder -f builder.Dockerfile .

docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /path/to/project-image.tar:/project-image.tar:ro \
  -v /path/to/out:/out \
  -e PROJECT_NAME=myproject \
  -e PARENT_IMAGE=gcr.io/oss-fuzz/myproject \
  -e HOST_OUT_DIR=/path/to/out \
  crs-builder
```

### 2. Run Fuzzing

```bash
docker build -t crs-runner -f runner.Dockerfile .

docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /path/to/out:/out \
  -e CPUSET_CPUS=0-7 \
  -e MEMORY_LIMIT=16G \
  -e HOST_OUT_DIR=/path/to/out \
  crs-runner my_harness_name
```

## Directory Structure

```
oss-crs/
├── README.md              # This file
├── INTEGRATION.md         # Detailed integration notes and bug fixes
├── build.sh               # Build phase script (uses host docker socket)
├── run.sh                 # Run phase script (uses host docker socket)
├── docker-compose.yml     # Fuzzing-only mode (redis, crs, cleanup)
└── docker-compose.mlla.yml # MLLA mode (redis, codeindexer, joern, lsp, crs, cleanup)
```

## How It Works

### Build Phase (build.sh)
1. Verifies host Docker socket is available
2. Loads project image from tarball
3. Extracts source code to repo.tar.gz
4. Builds CRS images via `run.py build_crs` (cached on host)
5. Tags images with namespace (e.g., `crs-multilang/crs-multilang:latest`)
6. Builds fuzzers via `run.py build`
7. Creates tarballs (fuzzers.tar.gz, project.tar.gz)
8. Pulls redis if not present

### Run Phase (run.sh)
1. Verifies host Docker socket is available
2. Verifies required images exist on host daemon
3. Generates crs.config with harness name
4. Selects compose file based on `CRS_INPUT_GENS`:
   - **Fuzzing-only** (`docker-compose.yml`): redis, crs, cleanup
   - **MLLA mode** (`docker-compose.mlla.yml`): redis, codeindexer, joern, lsp, crs, cleanup
5. Starts services via `docker compose`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECT_NAME` | (required) | OSS-Fuzz project name |
| `PARENT_IMAGE` | (required) | Docker image with project source |
| `HOST_OUT_DIR` | `/out` | Host path to output directory (for volume mounts) |
| `HOST_ARTIFACT_DIR` | `HOST_OUT_DIR` | Host path to artifacts directory |
| `CPUSET_CPUS` | `0-7` | CPU cores for fuzzing |
| `MEMORY_LIMIT` | `16G` | Memory limit for fuzzing |
| `LITELLM_URL` | (optional) | LLM service URL |
| `LITELLM_KEY` | (optional) | LLM service API key |
| `CRS_TARGET` | (optional) | Target project name |
| `CRS_NAME` | `crs-multilang` | CRS instance name |
| `CRS_SKIP_SAVE` | (empty) | Set to `True` to skip saving results |
| `CRS_INPUT_GENS` | `given_fuzzer` | Comma-separated input generators (e.g., `given_fuzzer,mlla`) |

## Required Images

These images must exist on the host Docker daemon (built by build phase):

| Image | Purpose |
|-------|---------|
| `crs-multilang/crs-multilang:latest` | Main CRS fuzzing engine |
| `crs-multilang/multilang-runner-joern:latest` | Joern code analysis service |
| `redis:latest` | State/cache storage |

## Tarballs

Build phase creates these tarballs in `/out/tarballs/`:

| Tarball | Contents |
|---------|----------|
| `repo.tar.gz` | Project source code extracted from parent image |
| `project.tar.gz` | OSS-Fuzz project files (project.yaml, .aixcc/, fuzz/) |
| `fuzzers.tar.gz` | Built fuzzer binaries and artifacts |

## Results Output

After fuzzing, results are automatically saved to `HOST_ARTIFACT_DIR` (mapped to `/artifacts` inside container):

```
HOST_ARTIFACT_DIR/
├── tarballs/                    # Build artifacts (from build phase)
├── povs/                        # Proof-of-Vulnerability inputs
├── corpus/                      # Corpus inputs (from uniafl_corpus)
└── crs-data/                    # CRS runtime data
    ├── workdir_result/          # Full workdir copy
    │   ├── uniafl_corpus/
    │   ├── uniafl_cov/
    │   ├── pov/
    │   ├── others_corpus/
    │   └── uniafl/
    └── eval_result/             # (if EVAL_SEC > 0)
```

To disable automatic result saving, set `CRS_SKIP_SAVE=True`.

## Troubleshooting

### "Docker socket not available"

Ensure `/var/run/docker.sock` is mounted:
```bash
docker run -v /var/run/docker.sock:/var/run/docker.sock ...
```

### "Image not found"

Ensure build phase completed successfully and images are on host:
```bash
docker images | grep crs-multilang
```

### Volume mount issues

When using host docker socket, volume paths must be host paths. Set `HOST_OUT_DIR` to the absolute host path:
```bash
-e HOST_OUT_DIR=/home/user/project/out
```

## Integration with oss-crs

This is designed to work with the oss-crs framework. Set `host_docker_builder: true` in config-crs.yaml to enable host docker socket mode for both builder and runner.
