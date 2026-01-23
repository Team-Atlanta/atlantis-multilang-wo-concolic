from typing import List

import pytest

from mlla.utils.cg import FuncInfo, InterestInfo
from mlla.utils.expanded_path import ExpandedPath
from tests.test_bcda import gen_path_list


@pytest.fixture
def path_list():
    return gen_path_list()


@pytest.fixture
def expanded_path_with_diff(path_list: List[List[FuncInfo]]):
    path_list[1][0].interest_info = InterestInfo(
        is_interesting=True,
        diff=r"""@@ -2,2, +2,1, @@
-def funcB():
-  print("hello diff")
+def funcB(): pass""",
    )
    return ExpandedPath(path_list)


@pytest.fixture
def expanded_path(path_list: List[List[FuncInfo]]):
    return ExpandedPath(path_list)


def test_code_with_path_with_diff(expanded_path_with_diff):
    """
    The diff is added to the corresponding function's instrumented code.
    """
    answer = r"""<function>
  <func_name>funcA</func_name>
  <file_path>fileA.py</file_path>
  <func_prototype_and_func_body>
  [1]: def funcA(): pass
  </func_prototype_and_func_body>
</function>

<function>
  <func_name>helper1</func_name>
  <file_path>helper.py</file_path>
  <func_prototype_and_func_body>
  [1]: def helper1(): pass
  </func_prototype_and_func_body>
</function>

<function>
  <func_name>helper2</func_name>
  <file_path>helper.py</file_path>
  <func_prototype_and_func_body>
  [2]: def helper2(): pass
  </func_prototype_and_func_body>
</function>

<function>
  <func_name>funcB</func_name>
  <file_path>fileB.py</file_path>
  <func_prototype_and_func_body>
  [2]: def funcB(): pass
  </func_prototype_and_func_body>
  <diff>
  @@ -2,2, +2,1, @@
  -def funcB():
  -  print("hello diff")
  +def funcB(): pass
  </diff>
</function>

<function>
  <func_name>util1</func_name>
  <file_path>util.py</file_path>
  <func_prototype_and_func_body>
  [1]: def util1(): pass
  </func_prototype_and_func_body>
</function>

<function>
  <func_name>funcC</func_name>
  <file_path>fileC.py</file_path>
  <func_prototype_and_func_body>
  [3]: def funcC(): pass
  </func_prototype_and_func_body>
</function>"""
    assert expanded_path_with_diff.code_with_path() == answer


def test_code_with_path_exclude_diff(expanded_path_with_diff):
    """
    The diff is not added to the instrumented code.
    """
    code_with_path = expanded_path_with_diff.code_with_path()
    assert "<diff>" in code_with_path

    code_with_path_exclude_diff = expanded_path_with_diff.code_with_path(
        include_diff=False
    )
    assert "<diff>" not in code_with_path_exclude_diff


def test_code_with_path(expanded_path):
    """
    The diff is not added to the instrumented code.
    """
    answer = r"""<function>
  <func_name>funcA</func_name>
  <file_path>fileA.py</file_path>
  <func_prototype_and_func_body>
  [1]: def funcA(): pass
  </func_prototype_and_func_body>
</function>

<function>
  <func_name>helper1</func_name>
  <file_path>helper.py</file_path>
  <func_prototype_and_func_body>
  [1]: def helper1(): pass
  </func_prototype_and_func_body>
</function>

<function>
  <func_name>helper2</func_name>
  <file_path>helper.py</file_path>
  <func_prototype_and_func_body>
  [2]: def helper2(): pass
  </func_prototype_and_func_body>
</function>

<function>
  <func_name>funcB</func_name>
  <file_path>fileB.py</file_path>
  <func_prototype_and_func_body>
  [2]: def funcB(): pass
  </func_prototype_and_func_body>
</function>

<function>
  <func_name>util1</func_name>
  <file_path>util.py</file_path>
  <func_prototype_and_func_body>
  [1]: def util1(): pass
  </func_prototype_and_func_body>
</function>

<function>
  <func_name>funcC</func_name>
  <file_path>fileC.py</file_path>
  <func_prototype_and_func_body>
  [3]: def funcC(): pass
  </func_prototype_and_func_body>
</function>"""
    assert expanded_path.code_with_path() == answer


def test_contain_interesting_node(expanded_path):
    """
    The path does not contain any interesting node.
    """
    assert expanded_path.contain_interesting_node() is False


def test_contain_interesting_node_with_diff(expanded_path_with_diff):
    """
    The path contains an interesting node.
    """
    assert expanded_path_with_diff.contain_interesting_node() is True


@pytest.fixture
def simple_expanded_path(path_list: List[List[FuncInfo]]):
    return ExpandedPath([[path_list[0][0]]])


@pytest.fixture
def simple_expanded_path_with_diff(simple_expanded_path: ExpandedPath):
    simple_expanded_path.path_list[0][0].interest_info = InterestInfo(
        is_interesting=True,
        diff=r"""@@ -1,1 +1,1, @@
-def funcABC(): pass
+def funcA(): pass""",
    )
    return simple_expanded_path


def test_instrument_code(expanded_path: ExpandedPath):
    """
    Test instrumented code.
    """
    keys = [
        "funcA:fileA.py:1:def funcA(): pass",
        "helper1:helper.py:1:def helper1(): pass",
        "helper2:helper.py:2:def helper2(): pass",
        "funcB:fileB.py:2:def funcB(): pass",
        "util1:util.py:1:def util1(): pass",
        "funcC:fileC.py:3:def funcC(): pass",
    ]
    codes = [
        r"""<func_name>funcA</func_name>
<file_path>fileA.py</file_path>
<func_prototype_and_func_body>
[1]: def funcA(): pass
</func_prototype_and_func_body>""",
        r"""<func_name>helper1</func_name>
<file_path>helper.py</file_path>
<func_prototype_and_func_body>
[1]: def helper1(): pass
</func_prototype_and_func_body>""",
        r"""<func_name>helper2</func_name>
<file_path>helper.py</file_path>
<func_prototype_and_func_body>
[2]: def helper2(): pass
</func_prototype_and_func_body>""",
        r"""<func_name>funcB</func_name>
<file_path>fileB.py</file_path>
<func_prototype_and_func_body>
[2]: def funcB(): pass
</func_prototype_and_func_body>""",
        r"""<func_name>util1</func_name>
<file_path>util.py</file_path>
<func_prototype_and_func_body>
[1]: def util1(): pass
</func_prototype_and_func_body>""",
        r"""<func_name>funcC</func_name>
<file_path>fileC.py</file_path>
<func_prototype_and_func_body>
[3]: def funcC(): pass
</func_prototype_and_func_body>""",
    ]
    answer = dict(zip(keys, codes))
    assert expanded_path.instrumented_code == answer


def test_instrument_code_with_diff(simple_expanded_path_with_diff: ExpandedPath):
    """
    Test instrumented code does not contain diff even if
    the path contains an interesting node.
    """
    assert simple_expanded_path_with_diff.instrumented_code == {
        "funcA:fileA.py:1:def funcA(): pass": (
            "<func_name>funcA</func_name>\n"
            "<file_path>fileA.py</file_path>\n"
            "<func_prototype_and_func_body>\n"
            "[1]: def funcA(): pass\n"
            "</func_prototype_and_func_body>"
        ),
    }


# def test_unidiff():
#     from unidiff import PatchSet
#     """
#     Test unidiff parsing.
#     """
#     diff = r"""@@ -7,1 +7,5 @@
# -void target_1(const uint8_t *data, size_t size) {}
# +void target_1(const uint8_t *data, size_t size) {
# +  char buf[0x40];
# +  if (size > 0 && data[0] == 'A')
# +      memcpy(buf, data, size);
# +}
# @@ -17,1 +17,5 @@
# -void target_2(const uint8_t *data, size_t size) {}
# +void target_2(const uint8_t *data, size_t size) {
# +  char buf[0x40];
# +  if (size > 0 && data[0] == 'A')
# +      memcpy(buf, data, size);
# +}
# @@ -27,1 +27,5 @@
# -void target_3(const uint8_t *data, size_t size) {}
# +void target_3(const uint8_t *data, size_t size) {
# +  char buf[0x40];
# +  if (size > 0 && data[0] == 'A')
# +      memcpy(buf, data, size);
# +}"""
#     dummy_header = r"""diff --git a/dummy_file b/dummy_file
# --- a/dummy_file
# +++ b/dummy_file
# """
#     print(dummy_header + diff)
#     results = PatchSet(dummy_header + diff)
#     print(results)
#     print(len(results[0]))
#     assert False


# def test_keyconditionreport_schema():
#     from mlla.agents.bcda_experimental import KeyConditionReport

#     print(KeyConditionReport.model_json_schema())
#     assert False
