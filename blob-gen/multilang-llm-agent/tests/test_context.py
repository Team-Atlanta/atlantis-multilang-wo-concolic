import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mlla.utils.context import GlobalContext, get_common_paths
from mlla.utils.cp import CP
from mlla.utils.diff_analyzer import FunctionDiff


@pytest.mark.parametrize(
    "proj_path,src_path,expected",
    [
        # Case 1: Paths share a common parent
        (
            Path("/home/user/project/src"),
            Path("/home/user/project/tests"),
            [Path("/home/user/project")],
        ),
        # Case 2: Paths don't share a common parent
        (
            Path("/home/user/project"),
            Path("/opt/external/lib"),
            [Path("/home/user/project"), Path("/opt/external/lib")],
        ),
        # Case 3: One path is parent of another
        (
            Path("/home/user/project"),
            Path("/home/user/project/src"),
            [Path("/home/user/project")],
        ),
        # Case 4: Paths share multiple levels
        (
            Path("/home/user/project/backend/src"),
            Path("/home/user/project/backend/tests"),
            [Path("/home/user/project/backend")],
        ),
        # Case 5: Root level paths
        (
            Path("/project1"),
            Path("/project2"),
            [Path("/project1"), Path("/project2")],
        ),
    ],
)
def test_get_common_paths(proj_path, src_path, expected):
    """Test get_common_paths function with various path combinations."""
    result = get_common_paths(proj_path, src_path)
    assert result == expected, f"Expected {expected}, but got {result}"


@pytest.mark.asyncio
async def test_diff_analyzer():
    with tempfile.TemporaryDirectory() as tmpdir:
        cp_dir = Path(tmpdir)
        cp_src_path = cp_dir / "repo"
        cp_src_path.mkdir()

        file_path = cp_src_path / "sample.c"
        file_path.write_text("int main() {\n    int a = 2;\n    return 1;\n}\n")
        another_file_path = cp_src_path / "another.c"
        another_file_path.write_text(
            'void test() {\n    int x = 10;\n    int y = 20;\n    printf("%d\\n", x);\n'
            "    return;\n}\n"
        )

        diff_path = cp_dir / "sample.diff"
        diff_text = r"""\
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
diff --git a/another.c b/another.c
index 1234567..89abcde 100644
--- a/another.c
+++ b/another.c
@@ -1,5 +1,6 @@
 void test() {
     int x = 10;
+    int y = 20;
     printf("%d\n", x);
     return;
 }
 """
        diff_path.write_text(diff_text)

        context = MagicMock(spec=GlobalContext)
        context._cp = MagicMock(spec=CP)
        context._cp.diff_path = diff_path
        context._cp.cp_src_path = cp_src_path
        context._init_diff = lambda: GlobalContext._init_diff(context)
        context.lsp_server = None

        await context._init_diff()

        assert hasattr(context, "function_diffs")
        assert context.function_diffs is not None
        function_diffs = context.function_diffs[str(file_path)]
        assert len(function_diffs) == 1
        assert isinstance(function_diffs[0], FunctionDiff)
        assert function_diffs[0].file_path.endswith("sample.c")
        function_diffs = context.function_diffs[str(another_file_path)]
        assert len(function_diffs) == 1
        assert isinstance(function_diffs[0], FunctionDiff)
        assert function_diffs[0].file_path.endswith("another.c")


@patch.dict(os.environ, {"LITELLM_KEY": "test_key", "LITELLM_URL": "test_url"})
def test_init_llm_success():
    """Test _init_llm with valid environment variables."""
    context = GlobalContext.__new__(GlobalContext)
    context._init_env_vars()
    context._init_llm(no_llm=False)

    assert context.api_key == "test_key"
    assert context.base_url == "test_url"
    assert context.no_llm is False


@patch.dict(os.environ, {}, clear=True)
def test_init_llm_missing_key():
    """Test _init_llm raises ValueError when LITELLM_KEY is missing."""
    context = GlobalContext.__new__(GlobalContext)

    with pytest.raises(ValueError, match="No LITELLM_KEY is defined"):
        context._init_llm(no_llm=False)


@patch.dict(os.environ, {"LITELLM_KEY": "test_key"}, clear=True)
def test_init_llm_missing_url():
    """Test _init_llm raises ValueError when LITELLM_URL is missing."""
    context = GlobalContext.__new__(GlobalContext)

    with pytest.raises(ValueError, match="No LITELLM_URL is defined"):
        context._init_llm(no_llm=False)
