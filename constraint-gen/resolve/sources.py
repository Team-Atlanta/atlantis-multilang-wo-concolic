from pathlib import Path
from typing import Optional

SRC_MAPPING_CACHE = {}

def find_best_suffix_match(file: Path, base_dir: Path) -> Path:
    """
    Given a file path 'file' and a directory path 'base_dir', find the file under 'base_dir'
    with the same extension as 'file' and the longest suffix match with 'file.name'.
    Raise an exception if no such file is found.
    """
    if not base_dir.is_dir():
        raise ValueError(f"'base_dir' is not a valid directory path: {base_dir}")

    extension = file.suffix
    target_name = file.name

    best_match = None
    longest_suffix_len = -1

    for candidate in base_dir.rglob(f"*{extension}"):
        if not candidate.is_file():
            continue
        candidate_name = candidate.name
        # Compare suffix match length from the end
        for i in range(1, len(target_name) + 1):
            if target_name[-i:] == candidate_name[-i:]:
                if i > longest_suffix_len:
                    best_match = candidate
                    longest_suffix_len = i
            else:
                break

    if best_match is None:
        raise FileNotFoundError(
            f"No file under '{base_dir}' with extension '{extension}' matches suffix of '{file.name}'"
        )
    return best_match

CRS_SRC = Path("/src")

def locate_src(src: Path, src_base: Path) -> Optional[Path]: 
    """
    Locate the source path in the CRS.

    This is not straightforward because the repository's source path is renamed
    from /src/<project_name> to /src/repo in the CRS, so we need to account for
    that adjustment.
    """

    if src in SRC_MAPPING_CACHE:
        return SRC_MAPPING_CACHE[src]
    
    try:
        src.relative_to(CRS_SRC)
    except:
        if src.exists():
            return src
        else:
            return None
    src = src.resolve()
    best_suffix = find_best_suffix_match(src, src_base)
    SRC_MAPPING_CACHE[src] = best_suffix
    return best_suffix
