import os
import re
import stat


BLOCKED_FUZZ_TARGET_EXTENSIONS = frozenset({
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz",  # archives / seed corpora
    ".options", ".dict",                              # OSS-Fuzz fuzzer config
    ".so", ".a", ".o", ".dylib", ".lib",              # libraries / objects
    ".py", ".sh", ".js", ".rb", ".pl", ".java",       # scripts / source
    ".h", ".c", ".cc", ".cpp", ".rs", ".go",          # source files
    ".html", ".xml", ".json", ".yaml", ".yml",        # data files
    ".txt", ".log", ".md", ".cfg", ".ini", ".csv",    # text / config
    ".png", ".jpg", ".gif", ".svg", ".ico",           # images
})
FUZZ_TARGET_SEARCH_STRING = "LLVMFuzzerTestOneInput"
VALID_TARGET_NAME_REGEX = re.compile(r"^[a-zA-Z0-9._@-]+$")
BLOCKLISTED_TARGET_NAME_REGEX = re.compile(r"^(jazzer_driver.*)$")


def is_executable(file_path):
    """Returns True if |file_path| is an exectuable."""
    return os.path.exists(file_path) and os.access(file_path, os.X_OK)


def get_fuzz_targets(path):
    """Gets fuzz targets in a directory.

    Args:
      path: A path to search for fuzz targets in.

    Returns:
      A list of paths to fuzzers or an empty list if None.
    """
    if not os.path.exists(path):
        return []
    fuzz_target_paths = []
    for root, _, fuzzers in os.walk(path):
        for fuzzer in fuzzers:
            file_path = os.path.join(root, fuzzer)
            if is_fuzz_target_local(file_path):
                fuzz_target_paths.append(file_path)

    return fuzz_target_paths


def is_fuzz_target_local(file_path):
    """Returns whether |file_path| is a fuzz target binary (local path).
    Copied from clusterfuzz src/python/bot/fuzzers/utils.py
    with slight modifications.
    """
    # pylint: disable=too-many-return-statements
    filename, file_extension = os.path.splitext(os.path.basename(file_path))
    if not VALID_TARGET_NAME_REGEX.match(filename):
        # Check fuzz target has a valid name (without any special chars).
        return False

    if BLOCKLISTED_TARGET_NAME_REGEX.match(filename):
        # Check fuzz target an explicitly disallowed name (e.g. binaries used for
        # jazzer-based targets).
        return False

    if file_extension in BLOCKED_FUZZ_TARGET_EXTENSIONS:
        return False

    if not is_executable(file_path):
        return False

    if filename.endswith("_fuzzer"):
        return True

    if os.path.exists(file_path) and not stat.S_ISREG(os.stat(file_path).st_mode):
        return False

    with open(file_path, "rb") as file_handle:
        return file_handle.read().find(FUZZ_TARGET_SEARCH_STRING.encode()) != -1


def get_harness_names(fuzz_dir):
    ret = []
    for path in fuzz_dir.iterdir():
        if is_fuzz_target_local(str(path)):
            ret.append(path.name)
    return ret
