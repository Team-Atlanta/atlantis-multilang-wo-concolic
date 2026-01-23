#!/usr/bin/env python3

import argparse
import asyncio
import glob
import json
import logging
import multiprocessing.pool
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from argparse import ArgumentParser
from pathlib import Path

import coloredlogs
import yaml
from python_on_whales import DockerClient, docker

logger = logging.getLogger(__name__)
coloredlogs.install(fmt="%(asctime)s %(levelname)s %(message)s")
CUR_FILE = __file__
CUR_DIR = Path(os.path.dirname(__file__))
OSS_FUZZ = CUR_DIR / "libs/oss-fuzz"
OSS_FUZZ_BUILD = OSS_FUZZ / "build/out/"
OSS_FUZZ_WORK = OSS_FUZZ / "build/work/"
OSS_FUZZ_HELPER = str(OSS_FUZZ / "infra/helper.py")
# Execution Mode Detection:
#
# This script can run in two modes:
#
# 1. HOST MODE (original): run.py executes on the host machine
#    - Uses OSS_FUZZ paths directly (e.g., /home/user/crs-multilang/libs/oss-fuzz/...)
#    - Docker volume mounts work because paths exist on host
#    - HOST_*_DIR env vars are NOT set
#
# 2. CONTAINER MODE (oss-crs integration): run.py executes inside a builder container
#    - Container paths (e.g., /crs-multilang/...) don't exist on host
#    - Uses host Docker socket, so Docker daemon runs on host
#    - HOST_*_DIR env vars provide actual host paths for Docker volume mounts
#    - Same-path mounts make these host paths accessible inside container
#
HOST_WORK_DIR = os.environ.get("HOST_WORK_DIR")
HOST_OUT_DIR = os.environ.get("HOST_OUT_DIR")
HOST_ARTIFACT_DIR = os.environ.get("HOST_ARTIFACT_DIR")
# True when running inside container with host Docker socket
CONTAINER_MODE = HOST_WORK_DIR is not None
SUPPORTED_LANGS = ["c", "c++", "cpp", "jvm"]
CONCOLIC_COMMON_ADDITIONAL_ARGS = (
    " --engine none -e COMPILE_SYMCC=1 -e CLANG_CRASH_DIAGNOSTICS_DIR=/out"
)
SYMCC_INSTRUMENTATION_ADDITIONAL_ARGS = CONCOLIC_COMMON_ADDITIONAL_ARGS
LSP_BASE = "multilang-lsp-base"
DOCKER_REGISTRY_BASE = ""


class OtherDockers:
    def __init__(self, name, need_to_run):
        self.name = name
        self.proj_name = name.replace("/", "_")
        self.need_to_run = need_to_run

    def __enter__(self):
        compose_file = CUR_DIR / "other-services.yml"
        os.environ["CP"] = self.name
        os.environ["LSP_RUNNER"] = lsp_runner_docker_name(self.name)
        self.docker = DockerClient(
            compose_files=[compose_file], compose_project_name=self.proj_name
        )
        if self.need_to_run:
            self.docker.compose.up(build=True, detach=True, force_recreate=True)
        return self

    def get_redis_name(self):
        if not self.need_to_run:
            return None
        containers = self.docker.ps()
        redis = list(
            filter(lambda x: "redis" in x.name and self.proj_name in x.name, containers)
        )[0]
        return redis.name

    def get_joern_name(self):
        if not self.need_to_run:
            return None
        containers = self.docker.ps()
        joern_lst = list(
            filter(lambda x: "joern" in x.name and self.proj_name in x.name, containers)
        )
        if len(joern_lst) == 0:
            logger.warning("Joern is not running")
            return None
        return joern_lst[0].name

    def get_lsp_name(self):
        # LSP will be launched by user, when used for testing.
        if not self.need_to_run:
            return None
        containers = self.docker.ps()
        lsp_lst = list(
            filter(lambda x: "lsp" in x.name and self.proj_name in x.name, containers)
        )
        if len(lsp_lst) == 0:
            logger.warning("LSP is not running")
            return None

        return lsp_lst[0].name

    def get_networks(self, name):
        if not self.need_to_run:
            return []
        return self.docker.container.inspect(name).network_settings.networks.keys()

    def __exit__(self, *args):
        if not self.need_to_run:
            return
        self.docker.compose.kill()
        self.docker.compose.down(timeout=600)


MULTILANG_DOCKERFILE_ADDITIONAL = """
COPY --from=multilang-c-archive /multilang-builder/libclang_rt.fuzzer.a /usr/local/lib/clang/18/lib/x86_64-unknown-linux-gnu/libclang_rt.fuzzer.a
COPY --from=crs-multilang /usr/local/bin/multilang_build.py /multilang_build.py
ENTRYPOINT ["/multilang_build.py"]
"""

JVM_MULTILANG_DOCKERFILE_ADDITIONAL = """
COPY --from=multilang-jvm-archive /multilang-builder/jazzer_agent_deploy.jar /usr/local/bin/jazzer_agent_deploy.jar 
COPY --from=multilang-jvm-archive /multilang-builder/jazzer_driver /usr/local/bin/jazzer_driver
COPY --from=multilang-jvm-archive /multilang-builder/jazzer_api_deploy.jar /usr/local/lib/jazzer_api_deploy.jar
COPY --from=multilang-jvm-archive /multilang-builder/jazzer_junit.jar /usr/local/bin/jazzer_junit.jar
COPY --from=crs-multilang /usr/local/bin/multilang_build.py /multilang_build.py
ENTRYPOINT ["/multilang_build.py"]
"""

SYMCC_DOCKERFILE_ADDITIONAL = """
COPY --from=crs-multilang /symcc /symcc
COPY --from=multilang-c-archive /multilang-builder/compile /usr/local/bin/compile
COPY --from=multilang-c-archive /multilang-builder/compile_symcc /usr/local/bin/compile_symcc
"""

LSP_DOCKERFILE_ADDITIONAL = (
    MULTILANG_DOCKERFILE_ADDITIONAL
    + """
RUN apt update && apt install -y bear && rm -rf /var/lib/apt/lists/*
RUN cp /usr/local/bin/compile /usr/local/bin/compile.orig
COPY --from=crs-multilang /multilang-builder/scripts/lsp-prepare.sh /usr/local/bin/compile 
"""
)

COVERAGE_DOCKERFILE_ADDITIONAL = (
    MULTILANG_DOCKERFILE_ADDITIONAL
    + """
COPY --from=multilang-c-archive /multilang-builder/llvm-patched /opt/llvm-patched
COPY --from=multilang-c-archive /multilang-builder/compile /usr/local/bin/compile
ENV SANITIZER_FLAGS_coverage "-fsanitize=address"
"""
)


def extract_base_image(dockerfile):
    for line in dockerfile.split("\n"):
        line = line.strip()
        tokens = list(filter(lambda x: x != "", line.split(" ")))
        if len(tokens) < 2:
            continue
        if tokens[0] not in ["FROM", "from"]:
            continue
        return tokens[1]


def lsp_runner_docker_name(proj_name):
    # Lowercase for Docker registry compatibility
    return f"multilang-lsp-{proj_name.replace('/', '_').lower()}"


def build_cp_docker_image(proj_name, image_name=None):
    cmd = ["python3", OSS_FUZZ_HELPER, "build_image", proj_name, "--no-pull", "--cache"]
    if image_name is not None:
        cmd += ["--image_name", image_name]
    for i in range(10):
        msg = f"[{i}th try] Build CP docker image for {proj_name}"
        if image_name is not None:
            msg += f" ({image_name})"
        logger.info(msg)
        if run(cmd, error_ok=True):
            return


class LspRunnerDockerBuild:
    def __init__(self, target):
        self.name = target.name.replace("/", "_")
        self.target = target
        self.dockerfile = target.target_path / "Dockerfile"
        self.prev = None

    def __rewrite_dockerfile(self):
        self.prev = self.dockerfile.read_text()
        new = self.prev
        base_image = extract_base_image(self.prev)
        if base_image == LSP_BASE:
            return
        new = new.replace(base_image, LSP_BASE)
        self.dockerfile.write_text(new)

    def build(self):
        build_cp_docker_image(
            self.target.name, lsp_runner_docker_name(self.target.name)
        )

    def __enter__(self):
        self.__rewrite_dockerfile()
        return self

    def __exit__(self, *args):
        if self.prev:
            self.dockerfile.write_text(self.prev)


class MultilangDockerBuild:
    def __init__(self, target, out_dir, build_type):
        self.target = target
        self.build_type = build_type
        self.dockerfile = target.target_path / "Dockerfile"
        self.out_dir = out_dir
        self.prev = None
        self.backup = None

    def __rewrite_dockerfile(self):
        self.prev = self.dockerfile.read_text()
        new = None
        if self.build_type == "multilang":
            new = self.prev + "\n"
            if self.target.language == "jvm":
                new += JVM_MULTILANG_DOCKERFILE_ADDITIONAL
            else:
                new += MULTILANG_DOCKERFILE_ADDITIONAL
        elif self.build_type == "lsp":
            new = self.prev + "\n"
            new += LSP_DOCKERFILE_ADDITIONAL
        elif self.build_type == "coverage":
            new = self.prev + "\n"
            new += COVERAGE_DOCKERFILE_ADDITIONAL
        elif self.build_type == "symcc":
            new = self.prev + "\n"
            new += SYMCC_DOCKERFILE_ADDITIONAL
        if new is not None:
            self.dockerfile.write_text(new)

    def __docker_mv(self, src, dst):
        assert src.parent == dst.parent
        # When using host docker socket, run mv directly
        if HOST_WORK_DIR:
            run(["rm", "-rf", str(dst)])
            run(["mv", str(src), str(dst)])
        else:
            parent = src.parent
            docker_cmd = [
                "docker",
                "run",
                "--privileged",
                "--shm-size=2g",
                "--platform",
                "linux/amd64",
            ]
            docker_cmd += ["-v", f"{parent}:/out"]
            docker_cmd += ["-t", "crs-multilang"]

            for cmd in [
                ["rm", "-rf", f"/out/{dst.name}"],
                ["mv", f"/out/{src.name}", f"/out/{dst.name}"],
            ]:
                run(docker_cmd + cmd)

    def __backup_prev_out(self):
        if self.out_dir == None:
            return
        # Skip backup when using host docker socket mode (outputs go to mount point)
        if HOST_OUT_DIR:
            return
        out = Path(self.out_dir)
        prev_out = OSS_FUZZ_BUILD / self.target.name
        if out.parent != prev_out.parent:
            error(f"out.parent != prev_out.parent: {out.parent} != {prev_out.parent}")
            return
        if not prev_out.exists():
            return
        self.backup = OSS_FUZZ_BUILD / f"{self.target.name}.backup"
        self.__docker_mv(prev_out, self.backup)

    def __restore_out(self):
        if self.backup == None:
            return
        # Skip restore when using host docker socket mode
        if HOST_OUT_DIR:
            return
        prev_out = OSS_FUZZ_BUILD / self.target.name
        # if SymCC or coverage build failed, prev_out will be nonexistent
        if prev_out.exists():
            self.__docker_mv(prev_out, self.out_dir)
        self.__docker_mv(self.backup, prev_out)

    def __enter__(self):
        self.__backup_prev_out()
        self.__rewrite_dockerfile()
        return self

    def __exit__(self, *args):
        self.__restore_out()
        if self.prev:
            self.dockerfile.write_text(self.prev)


def read_targets(list_fname):
    if list_fname == None:
        return None
    return list_fname.read_text().split("\n")


def read_symcc_target(list_fname):
    if list_fname == None or not list_fname.exists():
        return None
    ret = {}
    for line in list_fname.read_text().split("\n"):
        tmp = line.split(",")
        ret[tmp[0]] = tmp[1:]
    return ret


SYMCC_TEST_TAREGT = read_symcc_target(CUR_DIR / "tests/symcc.list")


def run(
    cmd: list,
    show_out=False,
    interactive=False,
    prefix="",
    error_ok=False,
    cwd=None,
    timeout=None,
):
    cmd = list(map(str, cmd))
    logger.info(f"{prefix}Run " + " ".join(cmd))
    if cwd == None:
        cwd = os.getcwd()
    cwd = str(cwd)
    interactive=True
    if interactive:
        ret = subprocess.run(cmd, check=False, cwd=cwd)
        if ret.returncode != 0 and not error_ok:
            error(f"Err {ret.returncode} while executing {' '.join(cmd)}")
        return ret.returncode == 0
    try:
        ret = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        ret = subprocess.CompletedProcess(
            stdout=b"", stderr=b"", returncode=1, args=cmd
        )
    if ret.returncode != 0 and not error_ok:
        error(
            f"{prefix}Fail while executing {cmd}\n"
            + ret.stdout.decode("utf-8", "ignore")
        )
    if show_out:
        logger.info(
            f"{prefix}Output from {cmd}\n"
            + ">>\n"
            + ret.stdout.decode("utf-8", "ignore")
        )
    return ret.returncode == 0


def error(msg):
    logger.error(msg)
    sys.exit(-1)


def build_docker_image(name, dockerfile):
    prefix = "[CRS] "
    run(["docker", "build", "-t", name, "-f", dockerfile, CUR_DIR], prefix=prefix)


def build_crs(args):
    prefix = "[CRS] "
    logger.info(prefix + "Build OSS-fuzz base images")
    if args.get("build_base_img", False):
        run([OSS_FUZZ / "infra/base-images/multilang-all.sh"], prefix=prefix)
    if args.get("skip_build_crs", False):
        logger.info(prefix + "Skip building CRS docker images")
        return
    build_docker_image("crs-multilang", "Dockerfile")
    build_docker_image("multilang-c-archive", "Dockerfile.c_archive")
    build_docker_image("multilang-jvm-archive", "Dockerfile.jvm_archive")
    build_docker_image(LSP_BASE, "lsp/Dockerfile")


def new_target_project(path, silent=False, force=False):
    prefix = str(OSS_FUZZ / "projects") + "/"
    return Target(str(path).split(prefix)[1], silent=silent, force=force)


def find_target(target, force=False):
    if "/" in target:
        target_path = OSS_FUZZ / "projects" / target
        if not target_path.exists():
            error(f"Target ({target}) does not exist")
        return new_target_project(target_path, force=force)
    else:
        candidates = glob.glob(str(OSS_FUZZ / f"projects/**/{target}"), recursive=True)
        if len(candidates) == 1:
            return new_target_project(candidates[0], force=force)
        elif len(candidates) == 0:
            error(f"Target ({target}) does not exist")
        elif len(candidates) > 0:
            msg = "Specify more detail target path\n"
            msg += "Multiple target candidates:\n"
            for c in candidates:
                msg += str(c) + "\n"
            error(msg)


class Target:
    def __init__(self, target_name, silent=False, src_path=None, force=False):
        self.name = target_name
        self.silent = silent
        self.src_path = src_path
        self.target_path = OSS_FUZZ / "projects" / target_name
        # In CONTAINER_MODE, use HOST_ARTIFACT_DIR (separate from /work and /out)
        # In HOST_MODE, use OSS_FUZZ internal path
        if CONTAINER_MODE:
            self.artifact_path = Path(HOST_ARTIFACT_DIR)
        else:
            self.artifact_path = OSS_FUZZ / "build/artifacts" / target_name
        self.workdir_path = OSS_FUZZ / "build/workdir" / target_name
        self.tarball_dir = self.artifact_path / "tarballs"
        # With same-path mounts, artifact_path works for both local ops and Docker mounts
        self.host_artifact_path = self.artifact_path
        self.host_tarball_dir = self.tarball_dir
        os.makedirs(self.artifact_path, exist_ok=True)
        os.makedirs(self.tarball_dir, exist_ok=True)
        with open(self.target_path / "project.yaml") as f:
            self.proj_yaml = yaml.safe_load(f)
        self.disabled = self.proj_yaml.get("disabled", False)
        if force:
            self.disabled = False
            self.log(f"Force enabled {target_name}")
        if self.disabled:
            self.log("Disabled")
        if "atl_jazzer" in (self.target_path / "Dockerfile").read_text():
            self.disabled = True
            self.log("Still in previous format: atl_jazzer")

        self.language = self.get_language()
        if self.language not in SUPPORTED_LANGS:
            self.disabled = True
            self.log(f"{self.language} is not supported yet")

        self.aixcc_conf = {}
        aixcc_conf_path = self.target_path / ".aixcc/config.yaml"
        if aixcc_conf_path.exists():
            with open(aixcc_conf_path, "r") as f:
                self.aixcc_conf = yaml.safe_load(f)

    def log(self, msg):
        if not self.silent:
            logging.info(f"[{self.name}] {msg}")

    def warning(self, msg):
        if not self.silent:
            logging.warning(f"[{self.name}] {msg}")

    def fuzzer_dir(self):
        """Returns the fuzzer output directory. Same path works for local ops and Docker mounts."""
        if CONTAINER_MODE:
            return Path(HOST_OUT_DIR)
        return OSS_FUZZ_BUILD / self.name

    def host_fuzzer_dir(self):
        """Alias for fuzzer_dir (same-path mounts make them identical)."""
        return self.fuzzer_dir()

    def work_dir(self):
        """Returns the work directory. Same path works for local ops and Docker mounts."""
        if CONTAINER_MODE:
            return Path(HOST_WORK_DIR)
        return OSS_FUZZ_WORK / self.name

    def __coverage_dir(self):
        # In CONTAINER_MODE, use subdirectory of HOST_OUT_DIR
        # In HOST_MODE, use sibling directory of fuzzer_dir
        if CONTAINER_MODE:
            return Path(HOST_OUT_DIR) / "coverage"
        return Path(str(self.fuzzer_dir()) + "-coverage")

    def __lsp_dir(self):
        if CONTAINER_MODE:
            return Path(HOST_OUT_DIR) / "lsp"
        return Path(str(self.fuzzer_dir()) + "-lsp")

    def __symcc_dir(self):
        if CONTAINER_MODE:
            return Path(HOST_OUT_DIR) / "symcc"
        return Path(str(self.fuzzer_dir()) + "-symcc")

    def __symcc_bin_dir(self):
        if CONTAINER_MODE:
            return Path(HOST_OUT_DIR) / "symcc-bin"
        return Path(str(self.fuzzer_dir()) + "-symcc-bin")

    def __run_cmd(
        self,
        cmd: list,
        show_out=False,
        interactive=False,
        error_ok=False,
        cwd=None,
        timeout=None,
    ):
        return run(
            cmd,
            show_out,
            interactive,
            prefix=f"[{self.name}] ",
            error_ok=error_ok,
            cwd=cwd,
            timeout=timeout,
        )

    def get_language(self):
        return self.proj_yaml.get("language", "c")

    def is_lsp_target(self, args):
        return self.get_language() in ["c", "cpp", "c++"]

    def is_symcc_target(self, args):
        return self.get_language() in ["c", "cpp", "c++"]

    def is_coverage_target(self, args):
        return self.get_language() in ["c", "cpp", "c++", "rust", "go"]

    def __get_base_commit(self):
        return self.aixcc_conf.get("full_mode", {}).get("base_commit", "")

    def __get_benchmark_commit(self):
        ret = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(OSS_FUZZ / "projects")
        )
        return ret.strip().decode("utf-8")

    def __is_benchmark_modified(self, from_hash):
        ret = subprocess.check_output(
            ["git", "diff", from_hash], cwd=str(OSS_FUZZ / "projects")
        )
        return bytes(f"a/projects/{self.name}", "utf-8") in ret

    def __get_multilang_builder_name(self):
        lang = self.language
        if lang in ["c", "cpp", "c++"]:
            return "multilang-builder"
        elif lang in ["jvm"]:
            return "multilang-builder-jvm"
        elif lang in ["go"]:
            return "multilang-builder-go"
        elif lang in ["rust"]:
            return "multilang-builder-rust"
        else:
            error(f"{lang} is not supported yet")

    def __get_multilang_builder_docker_hash(self):
        cmd = ["docker", "inspect", "--format='{{index .Id}}'"]
        cmd += [self.__get_multilang_builder_name()]
        try:
            ret = subprocess.check_output(cmd)
            return ret.strip().decode("utf-8")
        except:
            return None

    def to_host_path(self, container_path):
        """Convert container path to host path for Docker volume mounts.

        When using host Docker socket, paths like /work/... need to be
        converted to HOST_WORK_DIR/... for Docker volume mounts.
        """
        container_path = str(container_path)
        if HOST_WORK_DIR and container_path.startswith("/work/"):
            return container_path.replace("/work/", HOST_WORK_DIR + "/", 1)
        if HOST_OUT_DIR and container_path.startswith("/out/"):
            return container_path.replace("/out/", HOST_OUT_DIR + "/", 1)
        return container_path

    def load_src(self, dir_name="repo"):
        src = self.artifact_path / dir_name
        if self.src_path != None:
            self.__run_cmd(["cp", "-r", self.src_path, src])
            return src
        base_commit = self.__get_base_commit()
        main_repo = self.proj_yaml.get("main_repo", "")
        if main_repo == "":
            return ""
        if not src.exists():
            self.__run_cmd(["git", "clone", main_repo, src])
        if base_commit != "":
            self.__run_cmd(["git", "fetch", "origin"], cwd=src)
            self.__run_cmd(["git", "checkout", "-f", base_commit], cwd=src)
        return src

    def __need_build_lsp(self):
        out_dir = self.__lsp_dir()
        if not out_dir.exists():
            return True
        if not (out_dir / "compile_commands.json").exists():
            return True
        return False

    def __need_build(self, out_dir=None, invalidate=False):
        if out_dir == None:
            out_dir = self.fuzzer_dir()
        # Always ensure out_dir exists before checking build info
        os.makedirs(out_dir, exist_ok=True)
        if self.src_path != None:
            return not (out_dir / ".build_info").exists()
        if invalidate and out_dir.exists():
            # make sure not to delete .backup
            for f in out_dir.iterdir():
                if f.is_file():
                    f.unlink()
            return True
        fname = out_dir / ".build_info"
        if not fname.exists():
            return True
        ret = fname.read_text().split("//")
        commit = ret[0].strip()
        docker_hash = ret[1].strip()
        if self.__is_benchmark_modified(commit):
            return True
        if docker_hash != self.__get_multilang_builder_docker_hash():
            return True
        return False

    def __update_build_info(self, out_dir=None):
        if out_dir == None:
            out_dir = self.fuzzer_dir()
        if self.src_path != None:
            (out_dir / ".build_info").write_text("DONE")
            return
        commit = self.__get_benchmark_commit()
        docker_hash = self.__get_multilang_builder_docker_hash()
        fname = out_dir / ".build_info"
        fname.write_text(commit + " // " + docker_hash)

    def __build_cp_image(self):
        return build_cp_docker_image(self.name)

    def build(self, args):
        src = self.load_src()
        if args.get("skip_build", False):
            return src
        self.__build_cp_image()
        self.__build_basic(src, args)
        self.build_coverage_only(args)
        self.build_symcc_only(args)
        self.build_lsp(args)
        return src

    def build_coverage_only(self, args):
        built_coverage = self.__build_coverage(args)
        if built_coverage:
            self.__tar("coverage.tar.gz", self.__coverage_dir())
            self.__mark_done("coverage")

    def build_symcc_only(self, args):
        built_symcc = self.__build_symcc(args)
        if built_symcc:
            self.__tar("symcc.tar.gz", self.__symcc_bin_dir())
            self.__mark_done("symcc")

    def build_lsp(self, args):
        if self.__build_lsp(args):
            self.__run_cmd(
                [
                    "cp",
                    self.__lsp_dir() / "compile_commands.json",
                    self.tarball_dir / "compile_commands.json",
                ]
            )
        self.__build_lsp_runner(args)

    def __mark_done(self, name):
        done_file = self.tarball_dir / f"{name}.done"
        done_file.write_text("DONE")

    def __tar(self, dst, src):
        if not src.exists():
            self.log(f"Skip tar {dst} because {src} doesn't exist")
            return
        # When using host docker socket, run tar directly (builder has pigz installed)
        if HOST_WORK_DIR:
            tar_cmd = [
                "tar",
                "--use-compress-program=pigz",
                "-cv",
                "-f",
                str(self.tarball_dir / dst),
                "-C",
                str(src),
                ".",
            ]
            self.__run_cmd(tar_cmd)
        else:
            tar_cmd = [
                "tar",
                "--use-compress-program=pigz",
                "-cv",
                "-f",
                f"/tarballs/{dst}",
                "-C",
                "/src/",
                ".",
            ]
            docker_cmd = [
                "docker",
                "run",
                "--privileged",
                "--shm-size=2g",
                "--platform",
                "linux/amd64",
                "-v",
                f"{self.tarball_dir}:/tarballs",
                "-v",
                f"{src}:/src",
                "-t",
                "crs-multilang",
            ]
            self.__run_cmd(docker_cmd + tar_cmd)

    def __docker_rsync(self, src, dst, excludes=[]):
        # When using host docker socket, run rsync directly (builder has rsync installed)
        if HOST_WORK_DIR:
            cmd = ["rsync", "-a"]
            for ex in excludes:
                cmd += [f"--exclude={ex}"]
            os.makedirs(dst, exist_ok=True)
            cmd += [str(src) + "/", str(dst) + "/"]
            self.__run_cmd(cmd)
        else:
            cmd = ["rsync", "-a"]
            for ex in excludes:
                cmd += [f"--exclude={ex}"]
            cmd += ["/src/", "/dst/"]
            docker_cmd = [
                "docker",
                "run",
                "--privileged",
                "--shm-size=2g",
                "--platform",
                "linux/amd64",
                "-v",
                f"{src}:/src",
                "-v",
                f"{dst}:/dst",
                "-t",
                "crs-multilang",
            ]
            self.__run_cmd(docker_cmd + cmd)

    def __build_basic(self, src, args):
        if not self.__need_build(invalidate=args.get("fail_symcc", False)):
            self.log("Use already compiled CP")
            return False
        is_test = args.get("test", False)
        self.__run_build(src, is_test)
        self.__update_build_info()
        self.__tar("repo.tar.gz", src)
        self.__tar("fuzzers.tar.gz", self.fuzzer_dir())
        workdir = self.work_dir() / "multilang_proj"
        self.__docker_rsync(self.target_path, workdir, ["pkgs"])
        self.__tar("project.tar.gz", workdir)
        return True

    def __build_lsp(self, args):
        if args.get("create_conf") is not None or args.get("skip_build_lsp"):
            self.log("Skip LSP build for create_conf or skip_build_lsp flag")
            return False

        if not self.is_lsp_target(args):
            self.log(f"Skip compiling with LSP for {self.name}; only C/C++ need this")
            return False

        out_dir = self.__lsp_dir()
        os.makedirs(out_dir, exist_ok=True)

        if not self.__need_build_lsp():
            self.log("Use already compiled LSP")
            return False

        src = self.load_src("repo-lsp")
        is_test = args.get("test", False)
        try:
            self.__run_build(src, is_test, is_lsp=True)
            shutil.copy(
                self.work_dir() / "compile_commands.json",
                out_dir / "compile_commands.json",
            )
        except:
            self.log(f"Fail to LSP build")
        return True

    def __build_lsp_runner(self, args):
        if args.get("create_conf") is not None or args.get("skip_build_lsp"):
            self.log("Skip building LSP runner for create_conf or skip_build_lsp flag")
            return
        with LspRunnerDockerBuild(self) as lsp_runner:
            self.log(f"Build LSP runner for {self.name}")
            lsp_runner.build()

    def __build_symcc(self, args):
        if (
            not self.is_symcc_target(args)
            or args.get("create_conf") is not None
            or args.get("init_codeindexer") is not None
        ):
            self.warning("Skip compiling with symcc")
            return False
        if args.get("fail_symcc", False):
            self.warning("Forcing employment of SymQEMU by deliberately failing SymCC")
            fail_symcc = True
        else:
            fail_symcc = False
        suffix = "symcc"
        out_dir = self.__symcc_dir()
        if not self.__need_build(out_dir, invalidate=fail_symcc):
            self.log("Use already compiled symcced CP")
            return False
        else:
            src = self.load_src("repo-symcc")
            is_test = args.get("test", False)
            self.__run_build(
                src,
                is_test,
                symcc_harness=True,
                fail_symcc=fail_symcc,
                out_dir=out_dir,
                # 60 min timeout for SymCC build
                timeout=60.0 * 60,
            )
        if out_dir.exists():
            self.__to_symcc_bin_dir(out_dir, suffix)
        self.__update_build_info(out_dir)
        return True

    def __build_coverage(self, args):
        if not self.is_coverage_target(args) or args.get("create_conf") is not None:
            return False
        out_dir = self.__coverage_dir()
        if not self.__need_build(
            out_dir=out_dir, invalidate=args.get("fail_symcc", False)
        ):
            self.log("Use already compiled CP for coverage")
            return False
        src = self.load_src("repo-coverage")
        is_test = args.get("test", False)
        self.__run_build(src, is_test, out_dir=out_dir, coverage_harness=True)
        self.__update_build_info(out_dir)
        return True

    def __diag_post_build(
        self, args, proc: subprocess.CompletedProcess, build_name: str
    ):
        report_dir = Path(args.get("report_dir")) / self.name
        if proc.returncode != 0:
            self.log("Fail to build symcc")
        if not report_dir.exists():
            report_dir.mkdir(parents=True)
        returncode = report_dir / "returncode.txt"
        stdout = report_dir / "stdout.txt"
        stderr = report_dir / "stderr.txt"
        returncode.write_text(str(proc.returncode))
        stdout_bytes = proc.stdout if proc.stdout else b""
        stderr_bytes = proc.stderr if proc.stderr else b""
        stdout.write_bytes(stdout_bytes)
        stderr.write_bytes(stderr_bytes)
        self.log(f"Done building {build_name} for {self.name}")

        docker_image_name = f"gcr.io/oss-fuzz/{self.name}"
        if not self.__run_cmd(["docker", "inspect", docker_image_name], error_ok=True):
            self.__run_cmd(
                ["docker", "rmi", "-f", f"{docker_image_name}:multilang"],
                error_ok=False,
            )
            self.log(f"Docker image {docker_image_name} removed after build.")
        else:
            # This is unreachable
            self.log(f"Docker image {docker_image_name} isn't found after build.")

    def build_symcc_diag(self, args):
        if (
            not self.is_symcc_target(args)
            or args.get("create_conf") is not None
            or args.get("init_codeindexer") is not None
        ):
            self.warning("Skip compiling with symcc")
            return False
        fuzzer_dir = self.fuzzer_dir()
        suffix = "-symcc"
        out_dir = Path(str(self.fuzzer_dir()) + f"-{suffix}")
        if not self.__need_build(out_dir, invalidate=False):
            self.log("Use already compiled symcced CP")
            return False
        else:
            src = self.load_src("repo-symcc")
            is_test = args.get("test", False)
            self.__run_build_verbose(
                src,
                is_test,
                symcc_harness=True,
                out_dir=out_dir,
            )
        if out_dir.exists():
            self.log("Copy fuzzer to symcc bin dir")
            self.__to_symcc_bin_dir(out_dir, suffix)
        self.log(f"Update build info for {self.name}")
        self.__update_build_info(out_dir)
        return True

    def run(self, args):
        self.build(args)
        if args.get("build_only"):
            return
        self.__run_crs(args)

    def eval(self, args):
        self.run(args)
        out_dir = args["out"] / self.name
        self.__run_cmd(["rm", "-rf", out_dir])
        os.makedirs(out_dir, exist_ok=True)
        # eval_result = self.artifact_path / "eval_result"
        eval_result = self.artifact_path / "crs-data" / "eval_result"
        self.__run_cmd([f"cp", "-r", eval_result, out_dir])

        if args.get("copy_workdir"):
            # workdir_result = self.artifact_path / "workdir_result"
            workdir_result = self.artifact_path / "crs-data" / "workdir_result"
            self.__run_cmd([f"cp", "-r", workdir_result, out_dir])

    def __run_crs(self, args):
        create_conf = args.get("create_conf")
        cov_runner = args.get("cov_runner")
        cmd = [
            "docker",
            "run",
            "--privileged",
            "--cgroupns=host",
            "--shm-size=2g",
            "--platform",
            "linux/amd64",
        ]
        if sys.stdin.isatty():
            cmd += ["-it"]
        cmd += ["--rm", "-e", "FUZZING_ENGINE=libfuzzer"]
        sanitizer = args.get("sanitizer", "address")
        cmd += ["-e", f"SANITIZER={sanitizer}"]
        cmd += ["-e", "RUN_FUZZER_MODE=interactive", "-e", "HELPER=True"]
        cmd += ["-v", f"{self.host_tarball_dir}:/tarballs"]
        if create_conf != None:
            cmd += ["-v", f"{self.host_fuzzer_dir()}:/out"]
        config = args.get("config", "")
        if config != "":
            cmd += ["-v", f"{config}:/crs.config"]
        mlla_path = args.get("mlla")
        if mlla_path != None:
            cmd += ["-v", f"{mlla_path}:/mlla"]
        cmd += ["-e", f"CRS_TARGET={self.name}"]
        cmd += ["-e", "CRS_INTERACTIVE=True"]
        if args.get("start_core_id"):
            cmd += ["-e", "START_CORE_ID=" + args.get("start_core_id")]
        if args.get("shell", False):
            cmd += ["-e", "RUN_SHELL=1"]
        if args.get("test", False):
            cmd += ["-e", "CRS_TEST=True"]
            if self.name in SYMCC_TEST_TAREGT:
                cmd += ["-e", "TEST_SYMCC=" + ",".join(SYMCC_TEST_TAREGT[self.name])]
        if args.get("ncpu"):
            cmd += ["-e", "N_CPU=" + args.get("ncpu")]
        if args.get("skip_test_wo_harness"):
            cmd += ["-e", "CRS_TEST_WO_HARNESS=False"]
        llm_key = os.environ.get("LITELLM_KEY")
        llm_url = os.environ.get("LITELLM_URL")
        if llm_key:
            cmd += ["-e", f"LITELLM_KEY={llm_key}"]
        if llm_url:
            cmd += ["-e", f"LITELLM_URL={llm_url}"]
        if args.get("log", False):
            cmd += ["-e", "LOG=True"]
        if args.get("test_round", False):
            cmd += ["-e", "TEST_ROUND=True"]
        eval_sec = args.get("seconds", 0)
        if eval_sec != 0:
            cmd += ["-e", f"EVAL_SEC={eval_sec}"]
            cmd += ["-v", f"{self.host_artifact_path}:/artifacts"]
        if args.get("copy_workdir"):
            cmd += ["-e", "SAVE_WORKDIR_RESULT=True"]
        if args.get("llm_test"):
            cmd += ["-e", "LLM_TEST=" + args.get("llm_test")]
        if args.get("delta_mode"):
            cmd += ["-e", "TEST_DELTA_MODE=True"]
        with OtherDockers(self.name, args.get("start_other_services")) as other_dockers:
            redis = other_dockers.get_redis_name()
            if redis is None:
                redis = os.getenv("CODE_INDEXER_REDIS_URL")
            if redis:
                cmd += ["-e", f"CODE_INDEXER_REDIS_URL={redis}"]
                cmd += ["-e", f"DICTGEN_REDIS_URL={redis}"]
            lsp = other_dockers.get_lsp_name()
            if lsp is None:
                lsp = os.getenv("LSP_SERVER_URL")
            if lsp:
                cmd += ["-e", f"LSP_SERVER_URL={lsp}"]
            joern = other_dockers.get_joern_name()
            if joern is None:
                joern = os.getenv("JOERN_URL")
            if joern:
                cmd += ["-e", f"JOERN_URL={joern}"]
            if redis:
                for network in other_dockers.get_networks(redis):
                    cmd += ["--network", network]
            else:
                for network in other_dockers.get_networks(lsp):
                    cmd += ["--network", network]
            cmd += ["-t", "crs-multilang"]
            if mlla_path != None:
                cmd += ["run_mlla"]
                cmd += args["rest"][1:]  # removes the first "--"
            elif create_conf != None:
                cmd += ["create_conf", create_conf]
            elif cov_runner == True:
                cmd += ["run_cov_runner"]
            elif args.get("init_codeindexer") == True:
                cmd += ["init_codeindexer"]
            elif args.get("lsp_test"):
                cmd += ["test_lsp"]
            else:
                cmd += ["run_crs"]
            self.__run_cmd(cmd, True, True)

    def __construct_cmd(
        self,
        src,
        is_test=False,
        symcc_harness=False,
        fail_symcc=False,
        coverage_harness=False,
    ) -> str:
        # --clean deletes /out and /work before build. Safe because:
        # - In CONTAINER_MODE: artifacts are in separate HOST_ARTIFACT_DIR
        # - In HOST_MODE: artifacts are in OSS_FUZZ/build/artifacts (not /out or /work)
        cmd = f"python3 {OSS_FUZZ_HELPER} build_fuzzers --clean"
        if is_test:
            cmd += " -e CRS_TEST=True"
        if symcc_harness:
            cmd += SYMCC_INSTRUMENTATION_ADDITIONAL_ARGS
        if fail_symcc:
            cmd += "-e LIBSYMCC_RT_PATH=/wrong-path/libsymcc-rt.so"
        if coverage_harness:
            cmd += " --sanitizer coverage"
        cmd = cmd.split(" ")
        cmd += [self.name, str(src)]
        return cmd

    def __run_build(
        self,
        src,
        is_test=False,
        symcc_harness=False,
        fail_symcc=False,
        out_dir=None,
        coverage_harness=False,
        is_lsp=False,
        timeout=None,
    ):
        cmd = self.__construct_cmd(
            src, is_test, symcc_harness, fail_symcc, coverage_harness
        )
        if symcc_harness:
            self.log("Instrument the target with symcc")
        build_type = "multilang"
        if is_lsp:
            build_type = "lsp"
        elif coverage_harness:
            build_type = "coverage"
        elif symcc_harness:
            build_type = "symcc"
        self.log(f"Build for {build_type}")

        # Set HOST_OUT_SUBDIR for non-main builds so helper.py writes to subdirectory
        # This avoids polluting main output with coverage/symcc/lsp build artifacts
        if HOST_OUT_DIR and build_type != "multilang":
            os.environ["HOST_OUT_SUBDIR"] = build_type
            self.log(f"Set HOST_OUT_SUBDIR={build_type}")

        try:
            with MultilangDockerBuild(self, out_dir, build_type) as f:
                return self.__run_cmd(
                    cmd, error_ok=symcc_harness or coverage_harness, timeout=timeout
                )
        finally:
            # Clear HOST_OUT_SUBDIR after build
            if "HOST_OUT_SUBDIR" in os.environ:
                del os.environ["HOST_OUT_SUBDIR"]

    def __run_build_verbose(
        self,
        src,
        is_test=False,
        symcc_harness=False,
        coverage_harness=False,
        out_dir=None,
        is_lsp=False,
    ) -> subprocess.CompletedProcess:
        cmd = self.__construct_cmd(
            src,
            is_test,
            symcc_harness=symcc_harness,
        )
        build_type = "multilang"
        if is_lsp:
            build_type = "lsp"
        elif coverage_harness:
            build_type = "coverage"
        elif symcc_harness:
            build_type = "symcc"

        # Set HOST_OUT_SUBDIR for non-main builds so helper.py writes to subdirectory
        if HOST_OUT_DIR and build_type != "multilang":
            os.environ["HOST_OUT_SUBDIR"] = build_type

        try:
            with MultilangDockerBuild(self, out_dir, build_type) as f:
                try:
                    res = subprocess.run(
                        cmd,
                        shell=True,
                        check=False,
                        # stdout=subprocess.PIPE,
                        # stderr=subprocess.PIPE,
                        timeout=600,
                    )
                except subprocess.TimeoutExpired:
                    logger.error("Timeout expired")
                    res = subprocess.CompletedProcess(
                        cmd, -1, stdout=b"", stderr=b"Timeout expired"
                    )
            return res
        finally:
            # Clear HOST_OUT_SUBDIR after build
            if "HOST_OUT_SUBDIR" in os.environ:
                del os.environ["HOST_OUT_SUBDIR"]

    def __to_symcc_bin_dir(self, out_dir, suffix):
        symcc_bin_dir = self.__symcc_bin_dir()
        if not symcc_bin_dir.exists():
            os.makedirs(symcc_bin_dir)
        for maybe_fuzzer in out_dir.iterdir():
            if maybe_fuzzer.is_file() and maybe_fuzzer.stat().st_mode & 0o111:
                dst = symcc_bin_dir / f"{maybe_fuzzer.name}-{suffix}"
                self.__run_cmd(["cp", maybe_fuzzer, dst], error_ok=True)


def run_parallel(cmds, n=4, core_per_thread=24):
    lock = threading.Lock()
    max_n = max(int(os.cpu_count() / core_per_thread), 1)
    n = min(max_n, n)

    def run(cmd):
        idx = int(threading.current_thread().name.split("-")[1].split(" ")[0]) - 1
        start_core = idx * core_per_thread
        cmd += f" --start-core-id {start_core} --ncpu {core_per_thread}"
        ret = subprocess.run(
            cmd.split(" "),
            check=False,
            capture_output=True,
        )
        with lock:
            sys.stderr.buffer.write(ret.stdout)
        return ret

    with multiprocessing.pool.ThreadPool(n) as pool:
        results = pool.map(run, cmds)
    success = True
    for ret in results:
        if ret.returncode != 0:
            success = False
    if not success:
        logger.error("At least, one of tasks in run_parallel failed")
        for ret in results:
            if ret.returncode != 0:
                logger.error("=" * 80)
                sys.stderr.buffer.write(ret.stdout)
                sys.stderr.buffer.write(ret.stderr)
        sys.exit(-1)
    return success


def filter_targets(args):
    targets = read_targets(args["list"])
    ret = []
    for lang in SUPPORTED_LANGS:
        for target in glob.glob(str(OSS_FUZZ / f"projects/aixcc/{lang}/*")):
            target = new_target_project(target, args.get("force", False))
            if targets != None and target.name not in targets:
                target.log("Not in target")
                continue
            if target.disabled:
                continue
            ret.append(target)
    return ret


def main_test_all(args):
    build_crs(args)
    args["test"] = True
    cmds = []
    skip_test_wo_harness = False

    for target in filter_targets(args):
        cmd = f"python3 {CUR_FILE} run --target {target.name}"
        cmd += " --test"
        if skip_test_wo_harness:
            cmd += " --skip-test-wo-harness"
        if args["start_other_services"]:
            cmd += " --start-other-services"
        if args["llm_test"]:
            cmd += " --llm-test " + args["llm_test"]
        if args["create_conf"]:
            cmd += " --create-conf " + args["create_conf"]
        if args["lsp_test"]:
            cmd += " --lsp-test"
        if args["build_only"]:
            cmd += " --build-only"
        skip_test_wo_harness = True
        cmds.append(cmd)
    if not run_parallel(cmds):
        sys.exit(-1)


def eval_all(args):
    build_crs(args)
    cmds = []
    for target in filter_targets(args):
        cmd = f"python3 {CUR_FILE} eval --target {target.name}"
        cmd += f" --seconds {args.seconds} --out {args.out}"
        if args["start_other_services"]:
            cmd += " --start-other-services"
        cmds.append(cmd)
    if not run_parallel(cmds, core_per_thread=int(args.get("ncpu", 16))):
        sys.exit(-1)


def show_targets(args):
    for lang in SUPPORTED_LANGS:
        for target in glob.glob(str(OSS_FUZZ / f"projects/aixcc/{lang}/*")):
            target = new_target_project(target, True)
            if target.disabled:
                continue
            print(target.name)


def main_run(args):
    target = find_target(args["target"], args.get("force", False))
    if target.disabled:
        return
    build_crs(args)
    target.run(args)


def main_instrument_all(args):
    build_crs(args)
    targets = read_targets(args["list"])
    for lang in ["c", "cpp", "c++"]:
        for target in glob.glob(str(OSS_FUZZ / f"projects/aixcc/{lang}/*")):
            target = new_target_project(target, args.get("force", False))
            if target.disabled:
                continue
            if targets != None and target.name not in targets:
                continue
            target.build_symcc_diag(args)


def main_eval(args):
    target = find_target(args["target"], args.get("force", False))
    if target.disabled:
        return
    build_crs(args)
    target.eval(args)


class CP_Builder:
    def __init__(self, target, tar_dir, focus, registry, image_version):
        self.target = target
        self.tar_dir = tar_dir
        self.workdir = CUR_DIR / "workdir"
        self.registry = registry
        self.focus = focus
        self.image_version = image_version
        os.makedirs(self.workdir, exist_ok=True)

    def get_workdir(self, name):
        workdir = self.workdir / name
        os.makedirs(workdir, exist_ok=True)
        return workdir

    def log(self, msg):
        logging.info(f"[CP Builder] {msg}")

    def rsync(self, src, dst, exclude=None):
        cmd = ["rsync", "-a"]
        if exclude:
            for pattern in exclude:
                cmd.extend(["--exclude", pattern])
        cmd.extend([src, dst])
        while True:
            if run(cmd, error_ok=True):
                return
            self.log(f"Retry rsync -a {src} {dst}")
            time.sleep(1)

    def touch_done(self, dst):
        try:
            tmp = Path("/tmp/DONE")
            tmp.write_text("DONE")
        except:
            pass
        self.rsync(tmp, dst)

    def build_for_multilang(self, out_dir, start_other_services=False):
        self.__prepare_ossfuzz()
        src_dir, diff_path = self.__prepare_repo()
        target = Target(self.target, src_path=src_dir)
        self.__pull_multilang_builder_imgs(target.language)
        # Build fuzzers (creates repo.tar.gz, fuzzers.tar.gz, project.tar.gz)
        target.build({})
        target.run({"create_conf": "/out/aixcc_conf.yaml"})
        conf_src = target.fuzzer_dir() / "aixcc_conf.yaml"
        conf_dst = target.tarball_dir / "aixcc_conf.yaml"
        run(["cp", "-r", conf_src, conf_dst])
        if diff_path:
            run(["cp", "-r", diff_path, f"{target.tarball_dir / 'ref.diff'}"])
            """
            if target.language in ["c", "c++", "cpp"]:
                harnesses = self.__load_conf_harnesses(conf_dst)
                if len(harnesses) > 1:
                    filter_json = self.__filter_harness(diff_path, harnesses)
                    if filter_json:
                        run(["cp", filter_json, target.tarball_dir / filter_json.name])
                        self.__rewrite_conf(conf_dst, filter_json)
            """
        # When HOST_ARTIFACT_DIR is set, tarballs are already in artifacts/tarballs
        # Don't duplicate them in out_dir
        exclude_patterns = ["*.tar.gz"] if HOST_ARTIFACT_DIR else None
        self.rsync(str(target.tarball_dir) + "/", out_dir, exclude=exclude_patterns)
        self.touch_done(f"{out_dir / 'DONE'}")
        # LSP and coverage already built by target.build(), just start LSP and finalize
        self.__start_lsp(target)
        self.__finalize_tarball(target, out_dir, "coverage")
        target.run({"init_codeindexer": True, "start_other_services": start_other_services})

    def build_for_symcc(self, out_dir):
        self.__prepare_ossfuzz()
        src_dir, diff_path = self.__prepare_repo()
        target = Target(self.target, src_path=src_dir)
        target.build_symcc_only({})
        self.__finalize_tarball(target, out_dir, "symcc")

    def __finalize_tarball(self, target, out_dir, name):
        tarball_path = f"{target.tarball_dir}/{name}.tar.gz"
        # Skip copying tarball to out_dir when HOST_ARTIFACT_DIR is set
        # Tarballs are already in artifacts/tarballs and mounted to runner
        if Path(tarball_path).exists() and not HOST_ARTIFACT_DIR:
            self.rsync(tarball_path, out_dir)
        self.touch_done(f"{out_dir / name}.done")

    def __start_lsp(self, target):
        compose_file = CUR_DIR / "azure-other-services.yml"
        proj_name = target.name.replace("/", "_")
        os.environ["CP"] = target.name
        os.environ["LSP_RUNNER"] = lsp_runner_docker_name(target.name)
        os.environ["TARBALL_DIR"] = str(target.tarball_dir)
        client = DockerClient(
            compose_files=[compose_file], compose_project_name=proj_name
        )
        client.compose.up(services=["lsp"], detach=True, force_recreate=True)

    def __load_conf_harnesses(self, conf_path):
        if not conf_path.exists():
            return []
        ret = []
        with open(conf_path) as f:
            conf = yaml.safe_load(f)
            for harness in conf.get("harness_files", []):
                if "name" in harness:
                    ret.append(harness["name"])
        return ret

    def __rewrite_conf(self, conf_path, filter_json):
        if not conf_path.exists() or filter_json == None:
            return
        filter_json = json.loads(filter_json.read_text())
        if "uninteresting_harnesses" in filter_json:
            uninteresting = filter_json["uninteresting_harnesses"]
        else:
            return
        ret = []
        with open(conf_path) as f:
            conf = yaml.safe_load(f)
            for harness in conf.get("harness_files", []):
                if "name" in harness:
                    name = harness["name"]
                    if name not in uninteresting:
                        ret.append(harness)
        with open(conf_path, "w") as f:
            yaml.dump({"harness_files": ret}, f)

    def __filter_harness(self, diff_path, harnesses):
        if len(harnesses) == 0:
            return None
        repo_tar = self.tar_dir / "repo.tar.gz"
        repo = self.get_workdir("repo_for_filter")
        self.__untar(repo_tar, repo)
        src_dir = repo / self.focus
        workdir = self.get_workdir("workdir_for_filter")
        target = Target(self.target, src_path=src_dir)
        conf = {
            "harnesses": harnesses,
            "build_dir": str(target.fuzzer_dir()),
            "repo_dir": str(src_dir),
            "patch_path": str(diff_path),
            "project": self.target,
            "oss_fuzz_dir": str(CUR_DIR / "libs/oss-fuzz"),
        }
        conf_path = workdir / "conf"
        conf_path.write_text(json.dumps(conf))
        out_path = workdir / "filter-harness.json"
        cmd = [
            "python3",
            CUR_DIR / "scripts/filter-harness.py",
            "--config",
            conf_path,
            "--output",
            out_path,
        ]
        run(cmd, interactive=True)
        if out_path.exists():
            return out_path
        return None

    def __untar(self, tar, dst):
        run(["tar", "--use-compress-program=pigz", "-xf", tar, "-C", dst])

    def __patch(self, cwd, diff):
        run(["git", "init"], cwd=cwd, error_ok=True)
        run(["git", "apply", "--reject", diff], cwd=cwd, error_ok=True)
        run(["rm", "-rf", ".git"], cwd=cwd, error_ok=True)

    def __prepare_ossfuzz(self):
        if Path(OSS_FUZZ_HELPER).exists():
            return
        ossfuzz_tar = self.tar_dir / "oss-fuzz.tar.gz"
        ossfuzz = self.workdir / "oss-fuzz-untar"
        os.makedirs(ossfuzz, exist_ok=True)
        self.__untar(ossfuzz_tar, ossfuzz)
        ossfuzz = list(ossfuzz.iterdir())[0]
        os.makedirs(CUR_DIR / "libs", exist_ok=True)
        run(["mv", ossfuzz, CUR_DIR / "libs/oss-fuzz"])
        # overwrite helper.py to use ours
        run(["cp", CUR_DIR / "helper.py", OSS_FUZZ_HELPER])

    def __prepare_repo(self):
        repo_tar = self.tar_dir / "repo.tar.gz"
        repo = self.get_workdir("repo")
        src_dir = repo / self.focus
        diff_path = self.workdir / "diff/ref.diff"
        if src_dir.exists() and diff_path.exists():
            return (src_dir, diff_path)
        self.__untar(repo_tar, repo)
        if not src_dir.exists():
            candidates = list(
                filter(lambda x: not str(x).startswith("."), list(repo.iterdir()))
            )
            self.focus = candidates[0]
            src_dir = repo / self.focus

        diff_tar = self.tar_dir / "diff.tar.gz"
        diff_path = None
        if diff_tar.exists():
            self.__untar(diff_tar, self.workdir)
            diff_path = self.workdir / "diff/ref.diff"
            self.__patch(src_dir, diff_path)
        return (src_dir, diff_path)

    def __pull_multilang_builder_imgs(self, language):
        self.__wait_docker()
        # Skip pulling if registry is "local" - images are already loaded
        if self.registry == "local":
            self.log("Registry is 'local', skipping image pull (images should be pre-loaded)")
            return
        self.__pull_img("crs-multilang/crs-multilang")
        self.__pull_img("crs-multilang/multilang-lsp-base")
        if language == "jvm":
            self.__pull_img("crs-multilang/multilang-jvm-archive")
        else:
            self.__pull_img("crs-multilang/multilang-c-archive")

    def __pull_img(self, name):
        url = f"{self.registry}/{name}:{self.image_version}"
        self.log(f"Pull {url}")
        run(["docker", "image", "pull", url])
        name = name.split("/")[-1]
        run(["docker", "image", "tag", url, name])

    def __wait_docker(self):
        while True:
            try:
                info = docker.info()
                return True
            except Exception as e:
                time.sleep(1)


def main_build(args):
    builder = CP_Builder(
        args["target"],
        args["tar_dir"],
        args["focus"],
        args["registry"],
        args["image_version"],
    )
    out_dir = args["out_dir"]
    if args["symcc"]:
        return builder.build_for_symcc(out_dir)
    else:
        return builder.build_for_multilang(
            args["out_dir"],
            start_other_services=args.get("start_other_services", False),
        )


def add_run_args(parser):
    parser.add_argument(
        "--target",
        help="Target project name or relative path from oss-fuzz/projects",
        required=True,
    )
    parser.add_argument("--config", help="CRS Config path", default="")
    parser.add_argument(
        "--skip-build", help="skip building target", action="store_true", default=False
    )
    parser.add_argument(
        "--build-base-img",
        help="building base image",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--start-other-services",
        help="start other services",
        action="store_true",
        default=False,
    )
    parser.add_argument("--fail-symcc", action="store_true", default=False)
    parser.add_argument("--ncpu", help="# of cores")
    parser.add_argument("--start-core-id", help="Start Core ID", default="0")
    parser.add_argument("--llm-test", help="LLM module to test")
    parser.add_argument(
        "--lsp-test", help="Test LSP server", action="store_true", default=False
    )
    parser.add_argument("--sanitizer", help="Sanitizer", default="address")
    parser.add_argument(
        "--create-conf", help="create conf dst path in docker", default=None
    )
    parser.add_argument(
        "--cov-runner", help="cov-runner mode", action="store_true", default=False
    )
    parser.add_argument(
        "--skip-build-crs",
        help="skip building crs",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--copy-workdir",
        action="store_true",
        help="copy workdir results after execution",
        default=False,
    )
    parser.add_argument(
        "--delta-mode", help="delta mode", action="store_true", default=False
    )


def verify_symcc_artifacts_hash(image_name: str, args):
    if args.get("skip_symcc_verification", False):
        return
    multilang_root = Path(__file__).parent
    symcc_prebuild = multilang_root / "uniafl/src/concolic/executor/symcc/prebuild/"
    source_hash = subprocess.check_output(
        ["python3", "hash_symcc.py", "."], text=True, cwd=symcc_prebuild
    ).strip()
    image_hash = subprocess.check_output(
        ["docker", "run", "--rm", image_name, "cat", "/symcc/symcc_hash.txt"], text=True
    ).strip()
    if source_hash != image_hash:
        raise ValueError(
            f"Source hash {source_hash} does not match image hash {image_hash} for {image_name}. "
            "Please rebuild and push the recent image via infra/helper/base-images/multilang-all.sh && ./push-images-for-ci.sh <version>"
        )
    else:
        logger.info(
            f"Source hash {source_hash} successfully verified against source for image {image_name}"
        )


def pull_docker_image(args):
    if DOCKER_REGISTRY_BASE == "":
        print("Skip pulling docker image for now")
        return
    version_file = Path(__file__).parent / "crs-image-version.txt"
    if not version_file.exists():
        raise FileNotFoundError(
            f"Version file not found at {version_file}. Please create it with the version string."
        )
    version = version_file.read_text().strip()
    base = DOCKER_REGISTRY_BASE
    for name in [
        "crs-multilang",
        "multilang-builder-jvm",
        "multilang-builder",
    ]:
        full_name = base + name + ":" + version
        if (not docker.image.exists(full_name)) or (
            not docker.image.exists(f"{name}:latest")
        ):
            print(
                'If error.. echo "<YOUR_PERSONAL_ACCESS_TOKEN>" | docker login ghcr.io -u <YOUR_GITHUB_USERNAME> --password-stdin'
            )
            docker.pull(full_name)
            docker.tag(full_name, name)
        if name != "multilang-builder-jvm":
            verify_symcc_artifacts_hash(name, args)


if __name__ == "__main__":
    parser = ArgumentParser(description="Run CRS to find bugs in the target")
    subparsers = parser.add_subparsers(title="commands", required=True)
    parser_show = subparsers.add_parser("show-targets", help="Show all AIxCC projects")
    parser_show.add_argument(
        "--skip-symcc-verification",
        help="skip symcc verification",
        action="store_true",
        default=False,
    )
    parser_show.set_defaults(func=show_targets)

    parser_test_all = subparsers.add_parser(
        "test-all", help="Run test against all AIxCC projects"
    )
    parser_test_all.add_argument(
        "--list",
        help="a file path of a target list",
        type=Path,
    )
    parser_test_all.add_argument(
        "--build-base-img",
        help="building base image",
        action="store_true",
        default=False,
    )
    parser_test_all.add_argument(
        "--start-other-services",
        help="start other services",
        action="store_true",
        default=False,
    )
    parser_test_all.add_argument(
        "--fail-symcc", help="force to fail symcc", action="store_true", default=False
    )
    parser_test_all.add_argument(
        "--skip-symcc-verification",
        help="skip symcc verification",
        action="store_true",
        default=False,
    )
    parser_test_all.add_argument(
        "--create-conf", help="create conf dst path in docker", default=None
    )
    parser_test_all.add_argument("--llm-test", help="LLM module to test")
    parser_test_all.add_argument(
        "--lsp-test", help="Test LSP server", action="store_true", default=False
    )
    parser_test_all.add_argument(
        "--build-only", help="build only", action="store_true", default=False
    )
    parser_test_all.set_defaults(func=main_test_all)

    parser_instrument_all = subparsers.add_parser(
        "instrument-all", help="Instrument all AIxCC projects"
    )
    parser_instrument_all.add_argument(
        "--list",
        help="a file path of a target list",
        type=Path,
    )
    parser_instrument_all.add_argument(
        "--report-dir",
        help="path to save report",
        required=True,
        type=Path,
    )
    parser_instrument_all.add_argument(
        "--skip-symcc-verification",
        help="skip symcc verification",
        action="store_true",
        default=False,
    )
    parser_instrument_all.set_defaults(func=main_instrument_all)

    parser_run = subparsers.add_parser("run", help="Run the target project")
    add_run_args(parser_run)
    parser_run.add_argument(
        "--skip-symcc-verification",
        help="skip symcc verification",
        action="store_true",
        default=False,
    )
    parser_run.add_argument(
        "--test", help="test crs", action="store_true", default=False
    )
    parser_run.add_argument(
        "--force", help="force run disabled CPs", action="store_true"
    )
    parser_run.add_argument(
        "--skip-test-wo-harness",
        help="skip test wo harness",
        action="store_true",
        default=False,
    )
    parser_run.add_argument(
        "--shell", help="shell on crs", action="store_true", default=False
    )
    parser_run.add_argument(
        "--log", help="log on crs", action="store_true", default=False
    )
    parser_run.add_argument(
        "--test_round", help="test round", action="store_true", default=False
    )
    parser_run.add_argument(
        "--build-only", help="build only", action="store_true", default=False
    )
    parser_run.add_argument("--mlla", help="run mlla only", default=None)
    parser_run.add_argument("rest", nargs=argparse.REMAINDER)
    parser_run.set_defaults(func=main_run)

    parser_eval = subparsers.add_parser("eval", help="Eval the target project")
    add_run_args(parser_eval)
    parser_eval.add_argument(
        "--seconds", help="seconds for evaluation", type=int, required=True
    )
    parser_eval.add_argument(
        "--out", help="Path to save result", type=Path, required=True
    )
    parser_eval.add_argument(
        "--log", help="log on crs", action="store_true", default=False
    )
    parser_eval.add_argument(
        "--force", help="force run disabled CPS", action="store_true"
    )
    parser_eval.add_argument(
        "--skip-symcc-verification",
        help="skip symcc verification",
        action="store_true",
        default=False,
    )
    parser_eval.set_defaults(func=main_eval)

    parser_build = subparsers.add_parser("build", help="Build target CP")
    parser_build.add_argument(
        "--target",
        help="Target project name or relative path from oss-fuzz/projects",
        required=True,
    )
    parser_build.add_argument(
        "--tar-dir",
        help="directory having target tars",
        required=True,
        type=Path,
    )
    parser_build.add_argument(
        "--out-dir",
        help="output directory will have compiled tars",
        required=True,
        type=Path,
    )
    parser_build.add_argument(
        "--focus",
        help="src directory in repo",
        required=True,
    )
    parser_build.add_argument(
        "--registry",
        help="docker registry url",
        required=True,
    )
    parser_build.add_argument(
        "--image-version",
        help="docker image version",
        required=True,
    )
    parser_build.add_argument(
        "--skip-symcc-verification",
        help="skip symcc verification",
        action="store_true",
        default=False,
    )
    parser_build.add_argument(
        "--symcc",
        help="Build symcc",
        action="store_true",
        default=False,
    )
    parser_build.add_argument(
        "--start-other-services",
        help="start other services (redis for codeindexer)",
        action="store_true",
        default=False,
    )
    parser_build.set_defaults(func=main_build)

    parser_build_crs = subparsers.add_parser("build_crs", help="Build docker images")
    parser_build_crs.add_argument(
        "--build-base-img",
        help="building base image",
        action="store_true",
        default=True,
    )
    parser_build_crs.add_argument(
        "--skip-symcc-verification",
        help="skip symcc verification",
        action="store_true",
        default=False,
    )
    parser_build_crs.set_defaults(func=build_crs)

    logger.info("[CRS] Starting CRS")
    args = parser.parse_args()
    if args.func != main_build and args.func != build_crs:
        pull_docker_image(vars(args))
    args.func(vars(args))
