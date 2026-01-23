import asyncio
import os
import re

# import subprocess
# import tempfile
from collections import defaultdict

# from os import environ
from pathlib import Path
from typing import Dict, List, Optional

import chardet
from loguru import logger
from multilspy import LanguageServer
from multilspy.multilspy_types import SymbolKind, UnifiedSymbolInformation
from pydantic import BaseModel, Field
from unidiff import Hunk, PatchSet

from .cg import FuncInfo, LocationInfo


class FunctionDiff(BaseModel):
    file_path: str
    func_name: str
    hunk_start_line: int  # 1-based
    hunk_end_line: int  # 1-based
    fn_start_line: Optional[int] = Field(default=None)  # 1-based
    fn_end_line: Optional[int] = Field(default=None)  # 1-based
    diff: str
    hunk: Optional[List[Hunk]] = Field(default=None)
    cg_included: bool = Field(default=False)

    model_config = {"arbitrary_types_allowed": True}

    def to_func_info(self) -> FuncInfo:
        return FuncInfo(
            func_location=LocationInfo(
                file_path=self.file_path,
                start_line=self.fn_start_line or -1,
                end_line=self.fn_end_line or -1,
                func_name=self.func_name,
            ),
            need_to_analyze=True,
            tainted_args=[],
        )


def extract_diffs_in_range(
    diffs: List[FunctionDiff],
    start_line: int,
    end_line: int,
    file_path: str,
    set_cg_included: bool = False,
) -> List[FunctionDiff]:
    best_fits = [
        diff
        for diff in diffs
        if diff.file_path == file_path
        and diff.hunk_start_line >= start_line
        and diff.hunk_end_line <= end_line
    ]
    best_fits = sorted(best_fits, key=lambda x: x.hunk_end_line - x.hunk_start_line)
    if best_fits:
        results = best_fits[:1]
    else:
        partial_fits = [
            diff
            for diff in diffs
            if diff.file_path == file_path
            and not (diff.hunk_end_line < start_line or diff.hunk_start_line > end_line)
        ]
        results = partial_fits

    if set_cg_included:
        for diff in results:
            diff.cg_included = True
    return results


def accumulate_diffs(diffs: List[FunctionDiff], in_mcga=False) -> str:
    if len(diffs) == 0:
        return ""
    if len(set([diff.file_path for diff in diffs])) != 1:
        logger.error("All diffs must be in the same file.")
        for diff in diffs:
            logger.error(
                f"- {diff.file_path}:{diff.hunk_start_line}-{diff.hunk_end_line}"
            )
            logger.error(f"{diff.diff}")
        return ""
    if len(diffs) > 1 and in_mcga:
        logger.error("Diffs should be one because LSP-based cropping is used.")
        for diff in diffs:
            logger.error(
                f"- {diff.file_path}:{diff.hunk_start_line}-{diff.hunk_end_line}"
            )
            logger.error(f"{diff.diff}")

    return "\n".join([str(diff.diff) for diff in diffs])


# def change_line_offset(diff: str, start_line: int) -> str:
#     """
#     Change the line offset of the diff.
#     """

#     return ""


class DiffAnalyzer:
    def __init__(
        self,
        cp_src_path: str,
        diff_path: str,
        output_path: str,
        lsp_server: Optional[LanguageServer] = None,
    ):
        self.src_dir = Path(cp_src_path)
        self.diff_path = Path(diff_path)
        self.output_path = Path(output_path)
        self.lsp_server = lsp_server
        if output_path:
            output_dir = self.output_path.parent
            output_dir.mkdir(parents=True, exist_ok=True)

    def _get_patchset(self, diff_path: Path) -> PatchSet:
        try:
            with open(diff_path, "r") as f:
                diff = f.read()
        except UnicodeDecodeError:
            raw = diff_path.read_bytes()
            encoding = chardet.detect(raw)["encoding"] or "utf-8"
            diff = raw.decode(encoding, errors="replace")

        try:
            diffs = PatchSet(diff)
        except Exception as e:
            logger.info(f"Attempting to parse at least partial diff: {diff_path}\n{e}")
            diffs = self._get_patchset_from_invalid_diff(diff)

        return diffs

    def _get_patchset_from_invalid_diff(self, diff: str) -> PatchSet:
        diff_blocks = re.split(r"^diff --git", diff, flags=re.MULTILINE)[1:]
        patchset = PatchSet("")
        for diff_block in diff_blocks:
            try:
                patchset.extend(PatchSet(diff_block))
            except Exception as e:
                logger.warning(f"Failed to parse diff block: {e}\n{diff_block}")
        return patchset

    def parse_diff(self, diff_path: Path) -> Dict[str, List[FunctionDiff]]:
        function_diffs: Dict[str, List[FunctionDiff]] = defaultdict(list)
        patch = self._get_patchset(diff_path)
        for patched_file in patch:
            file_path = self.src_dir / patched_file.path

            for hunk in patched_file:
                function_diffs[str(file_path)].append(
                    FunctionDiff(
                        file_path=str(file_path),
                        func_name="",
                        hunk_start_line=hunk.target_start,
                        hunk_end_line=hunk.target_start + hunk.target_length - 1,
                        fn_start_line=None,
                        fn_end_line=None,
                        diff=str(hunk),
                        hunk=[hunk],
                    )
                )

        return function_diffs

    def accumulate_diffs(
        self, diffs: List[FunctionDiff], range_start: int, range_end: int
    ) -> tuple[
        str, int, int, List[Hunk]
    ]:  # : (accumulated_diff, target_start, target_end, interest_hunks)
        if len(set([diff.file_path for diff in diffs])) != 1:
            logger.error(f"All diffs must be in the same file.\n{diffs}")
            return "", 0, 0, []

        diffs = sorted(diffs, key=lambda x: x.hunk_start_line)
        accumulated_diff = ""
        interest_hunks = []
        _source_start = 0
        _source_end = 0
        total_start = 0
        total_end = 0
        for diff in diffs:
            if not diff.hunk:
                continue

            for hunk in diff.hunk:
                # print(hunk)

                is_target = False
                kept_lines = []
                for line in hunk:
                    if line.target_line_no and line.target_line_no >= range_start - 1:
                        is_target = True
                    if line.target_line_no and line.target_line_no > range_end:
                        is_target = False

                    if is_target:
                        kept_lines.append(line)

                    if line.source_line_no and not kept_lines:
                        _source_start = line.source_line_no
                    elif line.source_line_no and _source_end == 0:
                        _source_end = line.source_line_no

                if not kept_lines:
                    continue

                src_nos = [
                    line.source_line_no for line in kept_lines if line.source_line_no
                ]
                if src_nos:
                    source_start = min(src_nos)
                    source_end = max(src_nos)
                else:
                    source_start = _source_start if _source_start else hunk.source_start
                    source_end = (
                        _source_end
                        if _source_end
                        else hunk.source_start + hunk.source_length
                    )

                tgt_nos = [
                    line.target_line_no for line in kept_lines if line.target_line_no
                ]
                target_start = min(tgt_nos)
                target_end = max(tgt_nos)

                accumulated_diff += (
                    f"@@ -{source_start},{source_end - source_start + 1} "
                    f"+{target_start},{target_end - target_start + 1} @@\n"
                )
                accumulated_diff += "".join([str(line) for line in kept_lines])
                interest_hunks.append(hunk)

                total_start = (
                    min(total_start, target_start) if total_start != 0 else target_start
                )
                total_end = max(total_end, target_end) if total_end != 0 else target_end

        return accumulated_diff, total_start, total_end, interest_hunks

    async def function_mapping(self, file_path: str) -> List[UnifiedSymbolInformation]:
        if not self.lsp_server:
            logger.error(
                "This line should not be reached because analyze_diff() filters the"
                " case"
            )
            return []
        try:
            symbols, _ = await self.lsp_server.request_document_symbols(file_path)
        except Exception as e:
            logger.error(f"Error requesting document symbols for {file_path}: {e}")
            return []
        symbols = [
            symbol
            for symbol in symbols
            if symbol["kind"] == SymbolKind.Function
            or symbol["kind"] == SymbolKind.Method
            or symbol["kind"] == SymbolKind.Constructor
        ]
        return symbols

    def divide_diff_by_symbols(
        self, diffs: List[FunctionDiff], symbols: List[UnifiedSymbolInformation]
    ) -> List[FunctionDiff]:
        divided_diffs = []

        for symbol in symbols:
            # the line number in the LSP symbol is 0-based
            if "range" not in symbol:
                continue
            start_line = symbol["range"]["start"]["line"] + 1
            end_line = symbol["range"]["end"]["line"] + 1

            target_diffs = [
                diff
                for diff in diffs
                if not (
                    diff.hunk_end_line < start_line or diff.hunk_start_line > end_line
                )
            ]

            if not target_diffs:
                continue

            accumulated_diff, target_start, target_end, interest_hunks = (
                self.accumulate_diffs(
                    target_diffs,
                    start_line,
                    end_line,
                )
            )

            if not accumulated_diff:
                continue

            divided_diffs.append(
                FunctionDiff(
                    file_path=diffs[0].file_path,
                    func_name=symbol["name"],
                    hunk_start_line=target_start,
                    hunk_end_line=target_end,
                    fn_start_line=start_line,
                    fn_end_line=end_line,
                    diff=accumulated_diff,
                    hunk=interest_hunks,
                )
            )
        return divided_diffs

    async def analyze_diff(self) -> Dict[str, List[FunctionDiff]]:
        _function_diffs = self.parse_diff(self.diff_path)

        if not self.lsp_server:
            logger.error("LSP is not enabled, skipping function-level diff division")
            return _function_diffs

        exists_file_paths = [
            file_path
            for file_path, _ in _function_diffs.items()
            if os.path.exists(file_path)
        ]

        tasks = [self.function_mapping(file_path) for file_path in exists_file_paths]

        lsp_symbols = await asyncio.gather(*tasks, return_exceptions=True)

        function_diffs = {}
        for file_path, lsp_symbols in zip(exists_file_paths, lsp_symbols):
            if isinstance(lsp_symbols, Exception):
                logger.warning(
                    f"Error analyzing function range for {file_path}: {lsp_symbols}"
                )
                import traceback

                tb_lines = traceback.format_exception(
                    type(lsp_symbols), lsp_symbols, lsp_symbols.__traceback__
                )
                logger.warning("".join(tb_lines))
                continue
            function_diffs[file_path] = self.divide_diff_by_symbols(
                _function_diffs[file_path], lsp_symbols
            )

        logger.info(f"🟢 Parsed function diffs in {len(function_diffs)} files")

        return function_diffs
