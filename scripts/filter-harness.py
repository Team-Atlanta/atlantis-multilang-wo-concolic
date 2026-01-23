#!/usr/bin/env python

import yaml
import json
import os
import hashlib
import argparse
import subprocess
import logging
from pathlib import Path

_level = os.getenv("LOGLEVEL", "INFO").upper()
numeric_level = getattr(logging, _level, logging.WARNING)

logging.basicConfig(
    level=numeric_level,
    format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class Builder:
    def __init__(
        self,
        harnesses,
        repo_dir,
        patch_path,
        build_dir,
        project,
        oss_fuzz_dir,
    ):
        self.harnesses = harnesses
        self.build_dir = Path(build_dir).absolute().resolve()
        self.repo_dir = Path(repo_dir).absolute().resolve()
        self.patch_path = Path(patch_path).absolute().resolve()
        self.oss_fuzz_dir = Path(oss_fuzz_dir).absolute().resolve()
        self.project = project

    def get_hashes(self):
        base_dir = Path(self.build_dir) if self.build_dir is not None else Path.cwd()

        def _md5sum(path: Path) -> str:
            h = hashlib.md5()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    h.update(chunk)
            return h.hexdigest()

        hashes: dict[str, str] = {}
        for name in self.harnesses:
            path = base_dir / name
            if path.is_file():
                hashes[name] = _md5sum(path)
            else:
                logging.warning("File %s not found in %s", name, base_dir)
        return hashes

    def _build(self, repo_dir):
        cflags = "-O3 -flto -ffat-lto-objects"
        cxxflags = "-O3 -flto -ffat-lto-objects"
        ldflags = "-flto"
        coverage_flags_none = ""

        command = [
            "python3",
            "helper.py",
            "build_fuzzers",
            self.project,
            str(repo_dir),
            "-e",
            f"CFLAGS={cflags}",
            "-e",
            f"CXXFLAGS={cxxflags}",
            "-e",
            f"LDFLAGS={ldflags}",
            "-e",
            f"COVERAGE_FLAGS_none={coverage_flags_none}",
            "--sanitizer",
            "none",
        ]
        logging.debug("Running command: %s", command)
        try:
            result = subprocess.run(
                command,
                check=True,
                cwd=self.oss_fuzz_dir / "infra",
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            logging.error(f"Error running command: {e}")
            return False

        if result.stdout:
            logging.debug("Subprocess stdout:\n%s", result.stdout)
        if result.stderr:
            logging.debug("Subprocess stderr:\n%s", result.stderr)

        return True

    def _apply_patch(self, repo_dir):
        logging.info("Applying patch %s", self.patch_path)
        patch_file = Path(self.patch_path)
        if not patch_file.is_file():
            logging.error("Patch file %s not found", self.patch_path)
            return False
        subprocess.run(["git", "init"], cwd=repo_dir, check=False)
        subprocess.run(["git", "apply", self.patch_path], cwd=repo_dir, check=False)
        return True

    def _get_harness_hashes_commit(self, repo_dir):
        if not self._build(repo_dir):
            logging.error("Build failed")
            return {}
        return self.get_hashes()

    def get_harness_hashes_base(self):
        logging.info("Building base commit")
        head = str(self.repo_dir) + "_head"
        subprocess.run(["cp", "-r", str(self.repo_dir), head], check=False)
        return self._get_harness_hashes_commit(head)

    def get_harness_hashes_ref(self):
        logging.info("Building ref commit")
        ref = str(self.repo_dir) + "_ref"
        subprocess.run(["cp", "-r", str(self.repo_dir), ref], check=False)
        if not self._apply_patch(ref):
            logging.error("Patch application failed")
            return {}
        return self._get_harness_hashes_commit(ref)


def find_different_hashes(base, ref, harnesses):
    result = {
        name: (base[name], ref[name])
        for name in base.keys() & ref.keys()
        if base[name] != ref[name]
    }
    for harness in harnesses:
        if harness not in base or harness not in ref:
            result[harness] = ("N/A", "N/A")
    return result


def filter_harnesses(config, output):
    harnesses = config["harnesses"]
    builder = Builder(
        harnesses=harnesses,
        build_dir=config.get("build_dir"),
        patch_path=config["patch_path"],
        repo_dir=config["repo_dir"],
        project=config["project"],
        oss_fuzz_dir=config["oss_fuzz_dir"],
    )

    base = builder.get_harness_hashes_base()
    logging.debug("Base hashes: %s", base)

    ref = builder.get_harness_hashes_ref()
    logging.debug("Ref hashes: %s", ref)

    hashes = find_different_hashes(base, ref, harnesses)
    for hval, names in hashes.items():
        logging.debug(f"Hash {hval} is different : {', '.join(names)}")

    uninteresting_harnesses = [
        harness for harness in harnesses if harness not in hashes
    ]
    logging.info("Uninteresting harnesses: %s", uninteresting_harnesses)

    res = {
        "base": base,
        "ref": ref,
        "uninteresting_harnesses": uninteresting_harnesses,
        "interesting_harnesses": list(hashes.keys()),
    }
    output_dir = os.path.dirname(output)
    os.makedirs(output_dir, exist_ok=True)
    with open(output, "w") as f:
        f.write(json.dumps(res, indent=4))


def main(args, config):
    # Example:
    # {
    #     "harnesses": ["xinclude", "xpath", "regexp", "html", "reader", "lint", "uri", "schema", "valid", "xpath", "api"],
    #     "build_dir": "./benchmarks/build/out/aixcc/c/libxml2/",
    #     "repo_dir": "/path/to/libxml2",
    #     "patch_path": "./benchmarks/projects/aixcc/c/libxml2/.aixcc/ref.diff",
    #     "project": "aixcc/c/libxml2",
    #     "oss_fuzz_dir": "./benchmarks",
    # }

    filter_harnesses(config, args.output)


def test_project(oss_fuzz_dir, project_path, project_lang, project_name, workdir):
    config_json = {}

    config_yaml = project_path / ".aixcc" / "config.yaml"
    assert config_yaml.is_file(), f"Config file {config_yaml} not found"
    with open(config_yaml, "r") as f:
        config = yaml.safe_load(f)
        harness_files = config.get("harness_files", [])
        names = [hanress_file["name"] for hanress_file in harness_files]
        config_json["harnesses"] = names

        delta_mode = config.get("delta_mode")
        print(delta_mode)
        if delta_mode is None:
            return
        base_commit = delta_mode[0]["base_commit"]
        print(base_commit)

    project_yaml = project_path / "project.yaml"
    assert project_yaml.is_file(), f"Project file {project_yaml} not found"
    with open(project_yaml, "r") as f:
        project_config = yaml.safe_load(f)
        main_repo = project_config.get("main_repo")
        assert main_repo is not None, "Main repo not found in project.yaml"
        print(f"Main repo: {main_repo}")

        clone_dir = Path(workdir) / "aixcc" / project_lang / project_name

        try:
            subprocess.run(["git", "clone", main_repo, str(clone_dir)], check=True)
            subprocess.run(["git", "checkout", base_commit], check=True, cwd=clone_dir)
        except Exception as e:
            logging.error(f"Error cloning repository: {e}")
            raise

        repo_dir = workdir / "aixcc" / project_lang / project_name
        config_json["repo_dir"] = str(repo_dir)

    config_json["build_dir"] = str(
        oss_fuzz_dir / "build" / "out" / "aixcc" / project_lang / project_name
    )
    config_json["patch_path"] = str(project_path / ".aixcc" / "ref.diff")
    config_json["project"] = f"aixcc/{project_lang}/{project_name}"
    config_json["oss_fuzz_dir"] = str(oss_fuzz_dir)

    output = workdir / project_lang / project_name / "output.json"
    filter_harnesses(config_json, output)


def test(args, config):
    # Example:
    # {
    #   "workdir": "./workdir",
    #   "oss_fuzz_dir": "./benchmarks"
    # }

    oss_fuzz_dir = config.get("oss_fuzz_dir")
    assert (
        oss_fuzz_dir is not None
    ), "OSS-Fuzz directory must be specified in the config"
    assert os.path.isdir(oss_fuzz_dir), "OSS-Fuzz directory does not exist"
    oss_fuzz_dir = Path(oss_fuzz_dir).absolute().resolve()

    build_dir = oss_fuzz_dir / "build" / "out"
    project_dir = oss_fuzz_dir / "projects"
    assert os.path.isdir(project_dir), "Project directory does not exist"

    workdir = Path(workdir).absolute().resolve() / "filter-harnesses"
    os.makedirs(workdir, exist_ok=True)

    # loop over all projects
    for lang_dir in (project_dir / "aixcc").iterdir():
        if lang_dir.is_dir():
            if lang_dir.name not in ["c", "cpp"]:
                continue
            for project_path in lang_dir.iterdir():
                if project_path.is_dir():
                    project_name = project_path.name
                    logging.info(
                        f"Processing project: {project_name} in language: {lang_dir.name}"
                    )
                    test_project(
                        oss_fuzz_dir, project_path, lang_dir.name, project_name, workdir
                    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=str, help="Path to the config file", default="config.json"
    )
    parser.add_argument(
        "--output", type=str, help="Output file for the hashes", default="output.json"
    )
    parser.add_argument("--test", action="store_true", help="Run in test mode")
    args = parser.parse_args()
    logging.debug("Arguments: %s", args)

    with open(args.config, "r") as f:
        config = json.load(f)
    logging.info("Configuration: %s", config)

    if args.test:
        test(args, config)
    else:
        main(args, config)
