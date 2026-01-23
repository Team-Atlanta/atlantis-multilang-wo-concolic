import sys
import os
import subprocess
import re
import yaml
from collections import defaultdict

import build

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, project_root)

from infra.presubmit import check_project_yaml


def build_multilang_images():
    try:
        root = build.get_oss_fuzz_root()
        script_path = os.path.join(root, "infra", "base-images", "multilang-all.sh")
        command = [script_path]
        print("Running command: %s" % " ".join(command))
        subprocess.check_call(command)
    except subprocess.CalledProcessError:
        return 1
    return 0


def get_modified_benchmarks():
    git_output = build.get_changed_files_output()
    changed_files = git_output.split()
    pattern = r".*projects/aixcc/([^/.][^/]*)/([^/.][^/]*)/"

    language_benchmarks = defaultdict(set)

    for changed_file in changed_files:
        match = re.search(pattern, changed_file)
        if match:
            language, benchmark = match.groups()
            language_benchmarks[language].add(benchmark)

    root = build.get_oss_fuzz_root()
    for language, benchmarks in language_benchmarks.items():
        for benchmark in benchmarks:
            benchmark_dir_path = os.path.join(
                root, "projects", "aixcc", language, benchmark
            )
            if not os.path.isdir(benchmark_dir_path):
                print(f"[Error] {benchmark_dir_path} not a valid benchmark directory")
                return 1, None
    return 0, language_benchmarks


def check_modified_benchmark_yamls():
    git_output = build.get_changed_files_output()
    if not check_project_yaml(git_output):
        return 1
    return 0


def build_benchmark(language, benchmark):
    root = build.get_oss_fuzz_root()
    benchmark_yaml_path = os.path.join(
        root, "projects", "aixcc", language, benchmark, "project.yaml"
    )
    with open(benchmark_yaml_path) as file_handle:
        benchmark_yaml = yaml.safe_load(file_handle)

    if benchmark_yaml.get("disabled", False):
        print(f"Benchmark {language}/{benchmark} is disabled, skipping build.")
        return

    sanitizers = benchmark_yaml.get("sanitizers", [])
    fuzzing_engines = benchmark_yaml.get("fuzzing_engines", [])

    if not sanitizers:
        raise Exception("[Error] Sanitizers not provided for {language}/{benchmark}")
    if not fuzzing_engines:
        raise Exception(
            "[Error] Fuzzing Engines not provided for {language}/{benchmark}"
        )

    print(f"Building Benchmark {language}/{benchmark}")
    for engine in fuzzing_engines:
        for sanitizer in sanitizers:
            root = build.get_oss_fuzz_root()
            script_path = os.path.join(root, "infra", "helper.py")
            helper_command = [
                "build_fuzzers",
                f"aixcc/{language}/{benchmark}",
                "--engine",
                engine,
                "--sanitizer",
                sanitizer,
                "--architecture",
                "x86_64",
            ]
            command = ["python", script_path] + helper_command
            print("Running command: %s" % " ".join(command))
            subprocess.check_call(command)


def build_modified_benchmarks(language_benchmarks):
    failed_benchmarks = []

    for language, benchmarks in language_benchmarks.items():
        for benchmark in benchmarks:
            try:
                build_benchmark(language, benchmark)
            except Exception as e:
                print(e)
                failed_benchmarks.append((language, benchmark))

    if failed_benchmarks:
        print(f"Failed benchmarks: {failed_benchmarks}")
        return 1
    return 0


def check_internal_only(language, benchmark):
    root = build.get_oss_fuzz_root()
    internal_only_path = os.path.join(
        root, "projects", "aixcc", language, benchmark, "internal_only"
    )

    if not os.path.exists(internal_only_path) or not os.path.isdir(internal_only_path):
        raise Exception(
            f"'internal_only' directory does not exist at path: {internal_only_path}"
        )

    pov_path = os.path.join(internal_only_path, "povs")
    if not os.path.exists(pov_path) or not os.path.isdir(pov_path):
        raise Exception(f"'pov' directory does not exist at path: {pov_path}")

    directories = [
        d for d in os.listdir(pov_path) if os.path.isdir(os.path.join(pov_path, d))
    ]

    if not directories:
        raise Exception(f"No directories found inside 'pov' at path: {pov_path}")

    for directory in directories:
        dir_path = os.path.join(pov_path, directory)
        cpv_files = [
            f for f in os.listdir(dir_path) if f.startswith("cpv_") and f[4:].isdigit()
        ]

        if not cpv_files:
            raise Exception(
                f"Directory '{directory}' inside 'pov' does not contain a valid 'cpv_*' file"
            )

    return directories


def check_internal_only_files(language_benchmarks):
    failed_benchmarks = []

    for language, benchmarks in language_benchmarks.items():
        for benchmark in benchmarks:
            try:
                check_internal_only(language, benchmark)
            except Exception as e:
                print(e)
                failed_benchmarks.append((language, benchmark))

    if failed_benchmarks:
        print(f"Failed benchmarks: {failed_benchmarks}")
        return 1
    return 0


def main():
    infra_changed = build.is_infra_changed()
    if infra_changed:
        print("Pulling and building multilang images first.")
        if build_multilang_images():
            print("[Error] Failed to build multilang images.")
            return 1

    print("Finding modified benchmarks...")
    err_code, modified_language_benchmarks = get_modified_benchmarks()
    if err_code:
        print("[Error] Failed to find modified language benchmarks.")
        return 1
    print("The following benchmarks have been modified")
    for language, benchmarks in modified_language_benchmarks.items():
        print(f"{language}: {benchmarks}")

    print("Verifing project.yaml files...")
    result = check_modified_benchmark_yamls()
    if result:
        print("[Error] Verifing project.yaml files failed")
        return 1
    print("project.yaml files have been verified")

    print("Verifing internal_only files...")
    result = check_internal_only_files(modified_language_benchmarks)
    if result:
        print("[Error] Verifing internal_only files failed")
        return 1
    print("internal_only files have been verified")

    print("Building benchmarks")
    result = build_modified_benchmarks(modified_language_benchmarks)
    if result:
        print("[Error] Building benchmarks failed")
        return 1
    print("Building benchmarks finished")

    return 0


if __name__ == "__main__":
    sys.exit(main())
