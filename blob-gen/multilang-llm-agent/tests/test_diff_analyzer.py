import asyncio
import os
import tempfile
from pathlib import Path
from typing import Dict, List

import pytest
from loguru import logger
from multilspy.multilspy_types import SymbolKind, UnifiedSymbolInformation
from unidiff import PatchSet

from mlla.utils.cp import sCP, sCP_Harness
from mlla.utils.diff_analyzer import DiffAnalyzer, FunctionDiff, extract_diffs_in_range
from tests.dummy_context import DummyContext
from tests.test_multilspy import replace_compile_db


@pytest.fixture
def setup_repo_and_diff():
    with tempfile.TemporaryDirectory() as tmpdir:
        cp_dir = Path(tmpdir)
        cp_src_path = cp_dir / "repo"
        cp_src_path.mkdir()

        file_path = cp_src_path / "sample.c"
        file_path.write_text("int main() {\n    int a = 2;\n    return 1;\n}\n")

        diff_text = """\
diff --git a/sample.c b/sample.c
index e69de29..4b825dc 100644
--- a/sample.c
+++ b/sample.c
@@ -1,3 +1,4 @@
 int main() {
-    return 0;
+    int a = 2;
+    return 1;
 }
 """
        diff_path = cp_dir / "sample.diff"
        diff_path.write_text(diff_text)

        output_path = cp_dir / "output.log"

        yield str(cp_src_path), str(diff_path), str(output_path), str(file_path)


def test_parse_diff(setup_repo_and_diff):
    cp_src_path, diff_path, output_path, file_path = setup_repo_and_diff
    analyzer = DiffAnalyzer(cp_src_path, diff_path, output_path)

    results = analyzer.parse_diff(diff_path)

    diffs = results[str(file_path)]
    assert len(diffs) == 1
    for diff in diffs:
        assert isinstance(diff, FunctionDiff)
        assert "-    return 0;" in str(diff.diff) and "+    return 1;" in str(diff.diff)
        assert diff.file_path.endswith("sample.c")
        assert diff.hunk_start_line == 1
        assert diff.hunk_end_line == 4


def test_patchset_all_benchmarks(crs_multilang_path):

    benchmarks_dir = crs_multilang_path.absolute() / "benchmarks" / "projects" / "aixcc"
    benchmarks = benchmarks_dir.glob("**/ref.diff")

    analyzer = DiffAnalyzer("", "", "")
    for benchmark in benchmarks:
        diffs = analyzer._get_patchset(benchmark)
        assert len(diffs) > 0


def test_non_utf8_diff():
    with tempfile.TemporaryDirectory() as tmpdir:
        cp_dir = Path(tmpdir)
        repo_dir = cp_dir / "repo"
        repo_dir.mkdir()
        diff_path = cp_dir / "non-utf8.diff"
        diff_path.write_text(
            r"""diff --git a/src/core/ngx_core.h b/src/core/ngx_core.h
index 88db7dc..977ef91 100644
--- a/src/core/ngx_core.h
+++ b/src/core/ngx_core.h
@@ -117,4 +117,22 @@ void ngx_cpuinfo(void);
 #define NGX_DISABLE_SYMLINKS_NOTOWNER   2
 #endif

+
+#define BITS_PER_LONG (8 * sizeof(long))
+
+#define BITS_TO_LONGS(nr) \
+  (((nr) + (8 * sizeof(long) - 1)) / (8 * sizeof(long)))
+
+#define DECLARE_BITMAP(name, bits) \
+  unsigned long name[BITS_TO_LONGS(bits)]
+
+#define SET_BIT(bitmap, bit) \
+    do { \
+        (bitmap)[(bit) / BITS_PER_LONG] |= (1UL << ((bit) % BITS_PER_LONG)); \
+    } while (0) // non-utf8 테스트
+
+#define GET_BIT(bitmap, bit) \
+    ((bitmap)[(bit) / BITS_PER_LONG] & (1UL << ((bit) % BITS_PER_LONG)))
+
+
 #endif /* _NGX_코어_H_포함됨_ */
diff --git
index f44c9e7..65745e6 100644
--- a/src/http/ngx_http_request.c
+++ b/src/http/ngx_http_request.c
@@ -28,6 +28,8 @@ static ngx_int_t ngx_http_process_connection(ngx_http_request_t *r,
     ngx_table_elt_t *h, ngx_uint_t offset);
 static ngx_int_t ngx_http_process_user_agent(ngx_http_request_t *r,
     ngx_table_elt_t *h, ngx_uint_t offset);
+static ngx_int_t ngx_http_process_custom_features(ngx_http_request_t *r,
+    ngx_table_elt_t *h, ngx_uint_t offset);

 static ngx_int_t ngx_http_find_virtual_server(ngx_connection_t *c,
     ngx_http_virtual_names_t *virtual_names, ngx_str_t *host,
"""
        )

        analyzer = DiffAnalyzer("", "", "")
        diffs = analyzer._get_patchset(diff_path)
        assert len(diffs) == 2


def test_invalid_diff():
    with tempfile.TemporaryDirectory() as tmpdir:
        cp_dir = Path(tmpdir)
        repo_dir = cp_dir / "repo"
        repo_dir.mkdir()
        diff_path = cp_dir / "invalid.diff"
        diff_path.write_text(
            r"""diff --git a/src/core/ngx_core.h b/src/core/ngx_core.h
index 88db7dc..977ef91 100644
--- a/src/core/ngx_core.h
+++ b/src/core/ngx_core.h
@@ -117,4 +117,22 @@ void ngx_cpuinfo(void);
 #define NGX_DISABLE_SYMLINKS_NOTOWNER   2
 #endif

+
+#define BITS_PER_LONG (8 * sizeof(long))
+
+#define BITS_TO_LONGS(nr) \
+  (((nr) + (8 * sizeof(long) - 1)) / (8 * sizeof(long)))
+
+#define DECLARE_BITMAP(name, bits) \
+  unsigned long name[BITS_TO_LONGS(bits)]
+
+#define SET_BIT(bitmap, bit) \
+    do { \
+        (bitmap)[(bit) / BITS_PER_LONG] |= (1UL << ((bit) % BITS_PER_LONG)); \
+    } while (0)
+
+#define GET_BIT(bitmap, bit) \
+    ((bitmap)[(bit) / BITS_PER_LONG] & (1UL << ((bit) % BITS_PER_LONG)))
+
+
 #endif
diff --git
index f44c9e7..65745e6 100644
--- a/src/http/ngx_http_request.c
+++ b/src/http/ngx_http_request.c
@@ -28,6 +28,8 @@ static ngx_int_t ngx_http_process_connection(ngx_http_request_t *r,
     ngx_table_elt_t *h, ngx_uint_t offset);
 static ngx_int_t ngx_http_process_user_agent(ngx_http_request_t *r,
     ngx_table_elt_t *h, ngx_uint_t offset);
+static ngx_int_t ngx_http_process_custom_features(ngx_http_request_t *r,
diff --git a/src/http/ngx_http_request.h b/src/http/ngx_http_request.h
index 65c8333..3356d04 100644
--- a/src/http/ngx_http_request.h
+++ b/src/http/ngx_http_request.h
@@ -151,6 +151,14 @@
 #define NGX_HTTP_SUB_BUFFERED              0x02
 #define NGX_HTTP_COPY_BUFFERED             0x04

+typedef enum {
+    NGX_CUSTOM_FEATURE_0 = 0,
+    NGX_CUSTOM_FEATURE_1 = 1,
+    NGX_CUSTOM_FEATURE_2 = 2,
+    NGX_CUSTOM_FEATURE_3 = 3,
+    NGX_CUSTOM_FEATURE_4 = 4,
+    NGX_CUSTOM_FEATURE__NR = 5
+} ngx_custom_feature_flags_t;

 typedef enum {
     NGX_HTTP_INITING_REQUEST_STATE = 0,
"""
        )
        analyzer = DiffAnalyzer("", "", "")
        diffs = analyzer._get_patchset(diff_path)
        assert len(diffs) == 2
        assert diffs[0].path == "src/core/ngx_core.h"
        assert diffs[0][0][0].value == "#define NGX_DISABLE_SYMLINKS_NOTOWNER   2\n"
        assert diffs[1].path == "src/http/ngx_http_request.h"
        assert (
            diffs[1][0][0].value == "#define NGX_HTTP_SUB_BUFFERED              0x02\n"
        )


@pytest.fixture
def setup_java_repo_and_diff():
    with tempfile.TemporaryDirectory() as tmpdir:
        cp_dir = Path(tmpdir)
        repo_dir = cp_dir / "repo"
        repo_dir.mkdir()

        # Write a Java file with two methods
        java_file = repo_dir / "MyClass.java"
        java_file.write_text(
            r"""public class MyClass {
    public void methodA() {
        System.out.println("Hello A");
    }

    public void methodB() {
        System.out.println("Hello B");
    }

    public void dummy1() {
        System.out.println("Hello dummy1");
    }

    public void dummy2() {
        System.out.println("Hello dummy2");
    }

    public void dummy3() {
        System.out.println("Hello dummy3");
    }

    public void dummy4() {
        System.out.println("Hello dummy4");
    }
}
"""
        )

        # Create a diff that only changes methodA
        diff_text = r"""\
diff --git a/MyClass.java b/MyClass.java
index e69de29..abcdef1 100644
--- a/MyClass.java
+++ b/MyClass.java
@@ -6,3 +6,3 @@
     public void methodB() {
-        System.out.println("Hello B changed");
+        System.out.println("Hello B");
    }
"""
        diff_path = cp_dir / "java_method.diff"
        diff_path.write_text(diff_text)

        output_path = cp_dir / "output.log"
        yield str(repo_dir), str(diff_path), str(output_path), str(java_file)


@pytest.mark.asyncio
async def test_java_method_diff_only(setup_java_repo_and_diff):
    repo_dir, diff_path, output_path, file_path = setup_java_repo_and_diff
    analyzer = DiffAnalyzer(repo_dir, diff_path, output_path)

    # Parse and assert only one FunctionDiff for methodA
    _results = await analyzer.analyze_diff()
    results = _results[str(file_path)]
    assert len(results) == 1, "Expected exactly one method-level diff"
    diff = results[0]

    assert isinstance(diff, FunctionDiff)
    assert "dummy4" not in str(diff.diff)


# def test_update_interest_info_java(setup_java_repo_and_diff):
#     repo_dir, diff_path, output_path, file_path = setup_java_repo_and_diff
#     analyzer = DiffAnalyzer(repo_dir, diff_path, output_path)

#     func_info = FuncInfo(
#         func_location=LocationInfo(
#             file_path=str(Path(repo_dir) / "MyClass.java"),
#             func_name="methodB",
#             start_line=6,
#             end_line=8,
#         ),
#         func_body="_dummy_body_",
#     )

#     # Parse and assert only one FunctionDiff for methodA
#     _results = analyzer.analyze_diff()
#     results = _results[str(file_path)]
#     assert len(results) == 1, "Expected exactly one method-level diff"
#     diff = results[0]

#     assert 0


@pytest.mark.parametrize("setup_lsp", [["aixcc/c/mock-c"]], indirect=True)
@pytest.mark.asyncio
async def test_diffanalyzer_mockc(
    setup_lsp, cp_mockc_path: Path, oss_fuzz_workdir: Path
):
    """
    Multi functions in a hunk
    """
    # repo_dir, diff_path, output_path, file_path = setup_mockc_repo_and_diff
    cp_src_path = cp_mockc_path.resolve() / "repo"
    diff_path = cp_mockc_path / ".aixcc" / "ref.diff"
    file_path = cp_src_path / "mock.c"
    compile_db_json = oss_fuzz_workdir / "aixcc/c/mock-c" / "compile_commands.json"

    replace_compile_db(compile_db_json, cp_mockc_path.resolve())

    # Create harness for mock.c
    mock_harness = sCP_Harness(
        name="ossfuzz-1",
        src_path=cp_mockc_path / "fuzz/ossfuzz-1.c",
        bin_path=None,
    )

    gc = DummyContext(
        no_llm=False,
        language="c",
        scp=sCP(
            name="mock-c",
            proj_path=cp_mockc_path,
            cp_src_path=cp_src_path,
            aixcc_path=cp_mockc_path / ".aixcc",
            built_path=None,
            language="c",
            harnesses={
                "mock-c": mock_harness,
            },
        ),
    )

    lsp_server = gc._init_lsp_server()

    lsp_container_url = setup_lsp["aixcc/c/mock-c"]
    logger.info(f"lsp_container_url: {lsp_container_url}")
    assert lsp_container_url is not None

    os.environ["LSP_SERVER_URL"] = lsp_container_url

    server_cm = lsp_server.start_server()
    await asyncio.create_task(server_cm.__aenter__())

    analyzer = DiffAnalyzer(
        cp_src_path.as_posix(), diff_path.as_posix(), "", lsp_server
    )
    target_1 = "process_input_header"
    target_2 = "parse_buffer_section"

    _results = await analyzer.analyze_diff()
    results = _results[str(file_path)]
    assert len(results) == 2, f"Expected 2 FunctionDiff {target_1} and {target_2}"
    target_1_diff = next(diff for diff in results if diff.func_name == target_1)
    target_2_diff = next(diff for diff in results if diff.func_name == target_2)

    assert target_1_diff is not None
    assert target_2_diff is not None
    assert target_2 not in target_1_diff.diff
    assert target_1 not in target_2_diff.diff
    assert target_1_diff.diff != target_2_diff.diff

    if server_cm:
        await server_cm.__aexit__(None, None, None)


@pytest.mark.skip(reason="Java LSP needs revise")
@pytest.mark.parametrize("setup_lsp", [["aixcc/jvm/mock-java"]], indirect=True)
@pytest.mark.asyncio
async def test_best_fits_in_extract_diffs_in_range(setup_lsp, cp_mockjava_path: Path):
    """
    Test best_fits in extract_diffs_in_range
    """
    # repo_dir, diff_path, output_path, file_path = setup_mockjava_repo_and_diff

    cp_src_path = cp_mockjava_path.resolve() / "repo"
    diff_path = cp_mockjava_path / ".aixcc" / "ref.diff"

    mock_harness = sCP_Harness(
        name="OssFuzz1",
        src_path=cp_mockjava_path / "fuzz/OssFuzz1.c",
        bin_path=None,
    )

    gc = DummyContext(
        no_llm=False,
        language="jvm",
        scp=sCP(
            name="mock-java",
            proj_path=cp_mockjava_path,
            cp_src_path=cp_src_path,
            aixcc_path=cp_mockjava_path / ".aixcc",
            built_path=None,
            language="jvm",
            harnesses={
                "OssFuzz1": mock_harness,
            },
        ),
    )

    lsp_container_url = setup_lsp["aixcc/jvm/mock-java"]
    assert lsp_container_url is not None
    os.environ["LSP_SERVER_URL"] = lsp_container_url

    server_cm = gc.lsp_server.start_server()
    await asyncio.create_task(server_cm.__aenter__())

    analyzer = DiffAnalyzer(
        cp_src_path.as_posix(), diff_path.as_posix(), "", gc.lsp_server
    )

    _results = await analyzer.analyze_diff()

    logger.info(_results)
    logger.info(list(_results.keys()))

    file_paths = list(_results.keys())
    assert len(file_paths) == 1
    file_path = file_paths[0]
    parse_results = _results[str(file_path)]
    assert len(parse_results) == 1, "Only method should be parsed"

    # Range of executeCommand
    start_line = 12
    end_line = 21

    diffs = extract_diffs_in_range(parse_results, start_line, end_line, file_path)
    assert len(diffs) == 1
    diff = diffs[0]
    assert isinstance(diff, FunctionDiff)
    assert "executeCommand" in diff.func_name
    assert diff.diff != ""

    if server_cm:
        await server_cm.__aexit__(None, None, None)


@pytest.fixture
def diff_analyzer():
    analyzer = DiffAnalyzer("", "", "")
    yield analyzer


@pytest.fixture
def one_function_multiple_hunks():
    dummy_header = r"""diff --git a/file_path b/file_path
--- a/file_path
+++ b/file_path
"""
    diff1 = r"""@@ -6,3 +6,3 @@
     public void methodB() {
-        System.out.println("Hello 1 previous");
+        System.out.println("Hello 1);
         System.out.println("Hello 2");
"""
    diff2 = r"""@@ -11,3 +11,3 @@
         System.out.println("Hello 5");
-        System.out.println("Hello 6 previous");
+        System.out.println("Hello 6");
         System.out.println("Hello 7");
"""
    patchset = PatchSet(dummy_header + diff1 + diff2)
    _hunks = patchset[0]
    hunk1 = next(hunk for hunk in _hunks if "Hello 1" in str(hunk))
    hunk2 = next(hunk for hunk in _hunks if "Hello 5" in str(hunk))
    function_diffs: Dict[str, List[FunctionDiff]] = {
        "file_path": [
            FunctionDiff(
                file_path="file_path",
                func_name="methodB",
                hunk_start_line=6,
                hunk_end_line=8,
                diff=diff1,
                hunk=[hunk1],
            ),
            FunctionDiff(
                file_path="file_path",
                func_name="methodB",
                hunk_start_line=11,
                hunk_end_line=13,
                diff=diff2,
                hunk=[hunk2],
            ),
        ]
    }
    yield function_diffs


def test_accumulate_diffs(
    diff_analyzer: DiffAnalyzer,
    one_function_multiple_hunks: Dict[str, List[FunctionDiff]],
):
    function_diffs = one_function_multiple_hunks
    file_path = list(function_diffs.keys())[0]
    accumulated_diff, target_start, target_end, interest_hunks = (
        diff_analyzer.accumulate_diffs(function_diffs[file_path], 1, 15)
    )

    answer = r"""@@ -6,3 +6,3 @@
     public void methodB() {
-        System.out.println("Hello 1 previous");
+        System.out.println("Hello 1);
         System.out.println("Hello 2");
@@ -11,3 +11,3 @@
         System.out.println("Hello 5");
-        System.out.println("Hello 6 previous");
+        System.out.println("Hello 6");
         System.out.println("Hello 7");
"""
    answer_target_start = 6
    answer_target_end = 13

    assert accumulated_diff == answer
    assert target_start == answer_target_start
    assert target_end == answer_target_end
    first_hunks = function_diffs[file_path][0].hunk
    second_hunks = function_diffs[file_path][1].hunk
    hunk1 = first_hunks[0] if first_hunks else None
    hunk2 = second_hunks[0] if second_hunks else None
    assert hunk1 is not None
    assert hunk2 is not None
    assert hunk1 in interest_hunks
    assert hunk2 in interest_hunks


@pytest.fixture
def mockc_diff(diff_analyzer: DiffAnalyzer, cp_mockc_path: Path):
    diff_path = cp_mockc_path / ".aixcc" / "ref.diff"
    _function_diffs = diff_analyzer.parse_diff(diff_path)
    logger.debug(diff_path.read_text())
    return _function_diffs


@pytest.fixture
def mockc_symbols() -> List[UnifiedSymbolInformation]:
    symbols = [
        UnifiedSymbolInformation(
            detail="void (const int *, size_t)",
            kind=SymbolKind.Function,
            name="target_1",
            range={
                "end": {"character": 1, "line": 11},
                "start": {"character": 0, "line": 7},
            },
            selectionRange={
                "end": {"character": 13, "line": 7},
                "start": {"character": 5, "line": 7},
            },
        ),
        UnifiedSymbolInformation(
            detail="void (const int *, size_t)",
            kind=SymbolKind.Function,
            name="target_2",
            range={
                "end": {"character": 1, "line": 22},
                "start": {"character": 0, "line": 13},
            },
            selectionRange={
                "end": {"character": 13, "line": 13},
                "start": {"character": 5, "line": 13},
            },
        ),
    ]
    return symbols


@pytest.fixture
def mockc_symbols_minimal():
    symbols = [
        UnifiedSymbolInformation(
            kind=SymbolKind.Function,
            name="target_1",
        ),
        UnifiedSymbolInformation(
            kind=SymbolKind.Function,
            name="target_2",
        ),
    ]
    return symbols


def test_accumulate_diffs_mockc(
    diff_analyzer: DiffAnalyzer,
    mockc_diff: Dict[str, List[FunctionDiff]],
    mockc_symbols: List[UnifiedSymbolInformation],
):
    function_diffs = mockc_diff
    file_path = "mock.c"

    target_1_start_line = mockc_symbols[0]["range"]["start"]["line"] + 1  # 8
    target_1_end_line = mockc_symbols[0]["range"]["end"]["line"] + 1  # 12

    accumulated_diff, target_start, target_end, interest_hunks = (
        diff_analyzer.accumulate_diffs(
            function_diffs[file_path], target_1_start_line, target_1_end_line
        )
    )

    # 공백을 명시적으로 처리
    expected_diff = (
        "@@ -7,2 +7,6 @@\n"
        " \n"
        "-void target_1(const uint8_t *data, size_t size) {}\n"
        "+void process_input_header(const uint8_t *data, size_t size) {\n"
        "+  char buf[0x40];\n"
        "+  if (size > 0 && data[0] == 'A')\n"
        "+      memcpy(buf, data, size);\n"
        "+}\n"
    )
    assert accumulated_diff == expected_diff
    assert target_start == 7
    assert target_end == 12
    # Range in hunk is different from that of the cropped diff
    assert interest_hunks[0].target_start != 7


def test_divide_diff_by_symbols(
    diff_analyzer: DiffAnalyzer,
    mockc_diff: Dict[str, List[FunctionDiff]],
    mockc_symbols: List[UnifiedSymbolInformation],
):
    _function_diffs = mockc_diff

    function_diffs = {}
    for file_path, diffs in _function_diffs.items():
        function_diffs[file_path] = diff_analyzer.divide_diff_by_symbols(
            diffs, mockc_symbols
        )

    assert "mock.c" in function_diffs
    assert len(function_diffs["mock.c"]) == 2
    target_1 = next(
        diff for diff in function_diffs["mock.c"] if "target_1" in diff.func_name
    )
    assert target_1 is not None
    target_2 = next(
        diff for diff in function_diffs["mock.c"] if "target_2" in diff.func_name
    )
    assert target_2 is not None
    assert target_1 != target_2


def test_divide_diff_by_symbols_minimal(
    diff_analyzer: DiffAnalyzer,
    mockc_diff: Dict[str, List[FunctionDiff]],
    mockc_symbols_minimal: List[UnifiedSymbolInformation],
):
    _function_diffs = mockc_diff
    function_diffs = {}
    for file_path, diffs in _function_diffs.items():
        function_diffs[file_path] = diff_analyzer.divide_diff_by_symbols(
            diffs, mockc_symbols_minimal
        )
