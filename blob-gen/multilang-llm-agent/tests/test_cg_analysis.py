"""Tests for cg_analysis.py."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mlla.utils.bit import BugInducingThing, LocationInfo
from mlla.utils.cg import CG, FuncInfo
from mlla.utils.cg_analysis import (
    find_nodes_in_path,
    format_call_flow,
    format_source_codes,
    init_cg_analysis_prompts,
    init_single_cg_analysis_prompts,
)

pytestmark = pytest.mark.skip(reason="This file is deprecated.")


def create_empty_cg():
    """Create an empty call graph."""
    root = FuncInfo(
        func_location=LocationInfo(
            func_name="", file_path="", start_line=1, end_line=1
        ),
        func_body="",
        children=[],
    )
    return CG(name="empty_cg", path="empty_path", root_node=root)


def create_test_cg():
    """Create a test call graph."""
    # Create nodes
    root = FuncInfo(
        func_location=LocationInfo(
            func_name="main", file_path="main.py", start_line=1, end_line=1
        ),
        children=[],
        func_body="",
    )
    auth = FuncInfo(
        func_location=LocationInfo(
            func_name="authenticate", file_path="auth.py", start_line=1, end_line=1
        ),
        children=[],
        func_body="",
    )
    check = FuncInfo(
        func_location=LocationInfo(
            func_name="check_password", file_path="auth.py", start_line=1, end_line=1
        ),
        children=[],
        func_body="",
    )
    hash_pwd = FuncInfo(
        func_location=LocationInfo(
            func_name="hash_password", file_path="auth.py", start_line=1, end_line=1
        ),
        children=[],
        func_body="",
    )

    # Set up hierarchy
    root.children = [auth]
    auth.children = [check]
    check.children = [hash_pwd]

    # Create CG with root node
    return CG(name="test_cg", path="test_path", root_node=root)


def create_conditional_cg():
    """Create a call graph with conditional calls."""
    # Create nodes
    root = FuncInfo(
        func_location=LocationInfo(
            func_name="main", file_path="main.py", start_line=1, end_line=1
        ),
        children=[],
        func_body="",
    )
    validate = FuncInfo(
        func_location=LocationInfo(
            func_name="validate_input",
            file_path="validate.py",
            start_line=1,
            end_line=1,
        ),
        children=[],
        func_body="",
    )
    sanitize = FuncInfo(
        func_location=LocationInfo(
            func_name="sanitize_input",
            file_path="validate.py",
            start_line=1,
            end_line=1,
        ),
        children=[],
        func_body="",
    )
    log = FuncInfo(
        func_location=LocationInfo(
            func_name="log_error", file_path="validate.py", start_line=1, end_line=1
        ),
        children=[],
        func_body="",
    )

    # Set up hierarchy - validate has conditional calls to sanitize and log
    root.children = [validate, sanitize, log]  # Make all functions reachable from root
    validate.children = [sanitize, log]

    # Create CG with root node
    return CG(name="conditional_cg", path="conditional_path", root_node=root)


def create_branching_cg():
    """Create a call graph with multiple branches."""
    # Create nodes
    root = FuncInfo(
        func_location=LocationInfo(
            func_name="root", file_path="root.py", start_line=1, end_line=1
        ),
        children=[],
        func_body="",
    )
    left1 = FuncInfo(
        func_location=LocationInfo(
            func_name="left1", file_path="left1.py", start_line=1, end_line=1
        ),
        children=[],
        func_body="",
    )
    left2 = FuncInfo(
        func_location=LocationInfo(
            func_name="left2", file_path="left2.py", start_line=1, end_line=1
        ),
        children=[],
        func_body="",
    )
    right1 = FuncInfo(
        func_location=LocationInfo(
            func_name="right1", file_path="right1.py", start_line=1, end_line=1
        ),
        children=[],
        func_body="",
    )
    right2 = FuncInfo(
        func_location=LocationInfo(
            func_name="right2", file_path="right2.py", start_line=1, end_line=1
        ),
        children=[],
        func_body="",
    )

    # Set up hierarchy
    root.children = [left1, right1]
    left1.children = [left2]
    right1.children = [right2]

    # Create CG with root node
    return CG(name="branching_cg", path="branching_path", root_node=root)


def test_find_nodes_in_path():
    """Test finding nodes in path to buggy function."""
    cg = create_test_cg()

    # Test finding leaf node
    bit = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="hash_password",
            file_path=str(Path("auth.py")),
            start_line=1,
            end_line=2,
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )

    nodes = find_nodes_in_path(cg, bit)
    node_names = {node.func_location.func_name for node in nodes}
    assert node_names == {"main", "authenticate", "check_password", "hash_password"}

    # Test finding middle node
    bit = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="authenticate",
            file_path=str(Path("auth.py")),
            start_line=1,
            end_line=2,
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )

    nodes = find_nodes_in_path(cg, bit)
    node_names = {node.func_location.func_name for node in nodes}
    assert node_names == {"main", "authenticate", "check_password", "hash_password"}

    # Test non-existent node
    bit = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="nonexistent",
            file_path=str(Path("nonexistent.py")),
            start_line=1,
            end_line=2,
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )

    nodes = find_nodes_in_path(cg, bit)
    assert not nodes


def test_find_nodes_in_path_edge_cases():
    """Test edge cases for finding nodes in path."""
    # Test with empty CG
    empty_cg = create_empty_cg()
    bit = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="any", file_path=str(Path("any.py")), start_line=1, end_line=2
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )
    assert not find_nodes_in_path(empty_cg, bit)

    # Test with multiple bugs in same function
    cg = create_test_cg()
    bit1 = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="hash_password",
            file_path=str(Path("auth.py")),
            start_line=1,
            end_line=2,
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )
    bit2 = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="hash_password",
            file_path=str(Path("auth.py")),
            start_line=3,
            end_line=4,
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )
    nodes1 = find_nodes_in_path(cg, bit1)
    nodes2 = find_nodes_in_path(cg, bit2)
    assert nodes1 == nodes2

    # Test with bugs in different branches
    branching_cg = create_branching_cg()
    left_bit = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="left2", file_path=str(Path("left2.py")), start_line=1, end_line=2
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )
    right_bit = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="right2",
            file_path=str(Path("right2.py")),
            start_line=1,
            end_line=2,
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )
    left_nodes = find_nodes_in_path(branching_cg, left_bit)
    right_nodes = find_nodes_in_path(branching_cg, right_bit)
    assert left_nodes != right_nodes
    assert "root" in {n.func_location.func_name for n in left_nodes}
    assert "root" in {n.func_location.func_name for n in right_nodes}


@pytest.mark.asyncio
async def test_format_call_flow():
    """Test formatting control flow information."""
    cg = create_test_cg()

    # Mock GlobalContext and code_indexer
    gc = MagicMock()
    gc.code_indexer = AsyncMock()

    # Mock search_function to return full names
    async def mock_search_function(name):
        full_names = {
            "main": "void com.aixcc.mock_java.App.main(String[] args)",
            "authenticate": "boolean com.aixcc.mock_java.Auth.authenticate(User user)",
            "check_password": (
                "boolean com.aixcc.mock_java.Auth.check_password(String pwd)"
            ),
            "hash_password": (
                "String com.aixcc.mock_java.Auth.hash_password(String pwd)"
            ),
        }
        # Each node has its own file path
        file_paths = {
            "main": "main.py",
            "authenticate": "auth.py",
            "check_password": "auth.py",
            "hash_password": "auth.py",
        }
        return [
            MagicMock(
                name=name,
                func_name=full_names.get(name, name),
                file_path=file_paths.get(name, ""),
            )
        ]

    gc.code_indexer.search_function = mock_search_function

    # Test with path to leaf
    nodes = [
        cg.root_node,
        cg.root_node.children[0],  # authenticate
        cg.root_node.children[0].children[0],  # check_password
        cg.root_node.children[0].children[0].children[0],  # hash_password
    ]

    flow = await format_call_flow(gc, cg, nodes)
    expected = """<CALL_FLOW>
↳ void com.aixcc.mock_java.App.main(String[] args)
  ↳ boolean com.aixcc.mock_java.Auth.authenticate(User user)
    ↳ boolean com.aixcc.mock_java.Auth.check_password(String pwd)
      ↳ String com.aixcc.mock_java.Auth.hash_password(String pwd)
</CALL_FLOW>"""
    assert flow == expected

    # Test with empty path
    flow = await format_call_flow(gc, cg, [])
    assert flow == ""


HARNESS_CODE = """def test_auth():
    user = get_user()
    if authenticate(user):  # Calls check_password which calls hash_password
        return "Authenticated"
    return "Failed"
"""

VALIDATE_CODE = """def validate_input(data):
    if len(data) > 100:  # Bug: missing length check
        sanitize_input(data)  # Called in if branch
    if has_error(data):
        log_error(data)      # Called in another if branch
    return data
"""


@pytest.mark.asyncio
async def test_format_source_codes_with_harness():
    """Test formatting source codes with real harness code."""
    cg = create_test_cg()

    # Mock GlobalContext and code_indexer with real function bodies
    gc = MagicMock()
    gc.code_indexer = AsyncMock()

    # Mock different function bodies for each function
    async def mock_search_function(name):
        bodies = {
            "authenticate": (
                "def authenticate(user):\n    return check_password(user.pwd)\n"
            ),
            "check_password": (
                "def check_password(pwd):\n    return hash_password(pwd) =="
                " stored_hash\n"
            ),
            "hash_password": (
                "def hash_password(pwd):\n    return pwd + 'salt'  # Bug: weak"
                " hashing\n"
            ),
        }
        full_names = {
            "authenticate": "boolean com.aixcc.mock_java.Auth.authenticate(User user)",
            "check_password": (
                "boolean com.aixcc.mock_java.Auth.check_password(String pwd)"
            ),
            "hash_password": (
                "String com.aixcc.mock_java.Auth.hash_password(String pwd)"
            ),
        }
        return [
            MagicMock(
                func_body=bodies.get(name, ""),
                name=full_names.get(name, name),
                func_name=full_names.get(name, name),
                file_path="auth.py",  # All functions are in auth.py
            )
        ]

    gc.code_indexer.search_function = mock_search_function

    # Test with BIT in hash_password
    bit = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="hash_password",
            file_path=str(Path("auth.py")),
            start_line=2,
            end_line=2,
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )

    with patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = HARNESS_CODE

        result = await format_source_codes(
            gc,
            Path("test.py"),
            cg,
            [bit],
        )

    # Check that result contains all function bodies
    assert "def authenticate" in result
    assert "def check_password" in result
    assert "def hash_password" in result
    assert "/*BUG_HERE*/" in result
    assert HARNESS_CODE.strip() in result


@pytest.mark.asyncio
async def test_format_source_codes_with_conditional_calls():
    """Test formatting source codes with conditional function calls."""
    cg = create_conditional_cg()

    # Mock GlobalContext and code_indexer
    gc = MagicMock()
    gc.code_indexer = AsyncMock()

    # Mock validate_input with conditional calls
    async def mock_search_function(name):
        full_names = {
            "validate_input": (
                "void com.aixcc.mock_java.Validator.validate_input(String data)"
            ),
            "sanitize_input": (
                "void com.aixcc.mock_java.Validator.sanitize_input(String data)"
            ),
            "log_error": "void com.aixcc.mock_java.Logger.log_error(String data)",
        }
        if name == "validate_input":
            return [
                MagicMock(
                    func_body=VALIDATE_CODE,
                    name=full_names[name],
                    func_name=full_names[name],
                    file_path="validate.py",  # Match node's file_path
                )
            ]
        return [
            MagicMock(
                func_body=f"def {name}(data):\n    pass\n",
                name=full_names.get(name, name),
                func_name=full_names.get(name, name),
                file_path="validate.py",  # All functions are in validate.py
            )
        ]

    gc.code_indexer.search_function = mock_search_function

    # Test with BIT in validate_input
    bit = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="validate_input",
            file_path=str(Path("validate.py")),
            start_line=2,
            end_line=2,
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )

    with patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = "harness code"

        result = await format_source_codes(
            gc,
            Path("test.py"),
            cg,
            [bit],
        )

    # Check that result contains all conditionally called functions
    assert "def validate_input" in result
    assert "def sanitize_input" in result
    assert "def log_error" in result
    assert "/*BUG_HERE*/" in result


@pytest.mark.asyncio
async def test_format_source_codes():
    """Test formatting source codes with BITs."""
    cg = create_test_cg()

    # Mock GlobalContext and code_indexer
    gc = MagicMock()
    gc.code_indexer = AsyncMock()

    async def mock_search_function(name):
        if name == "hash_password":
            return [
                MagicMock(
                    func_body=(
                        "def hash_password(pwd):\n    x = 1\n    y = 2\n    z = 3\n"
                    ),
                    name="String com.aixcc.mock_java.Auth.hash_password(String pwd)",
                    func_name=(
                        "String com.aixcc.mock_java.Auth.hash_password(String pwd)"
                    ),
                    file_path="auth.py",  # Match node's file_path
                    start_line=1,  # Line numbers start at 1
                )
            ]
        return [
            MagicMock(
                func_body=f"def {name}():\n    pass\n",
                name=f"void com.aixcc.mock_java.Auth.{name}()",
                func_name=f"void com.aixcc.mock_java.Auth.{name}()",
                file_path="auth.py",  # Match node's file_path
            )
        ]

    gc.code_indexer.search_function = mock_search_function

    # Test with single BIT
    bit = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="hash_password",
            file_path=str(Path("auth.py")),
            start_line=1,  # Line with x = 1
            end_line=2,  # Line with y = 2
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )

    with patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = "harness code"

        result = await format_source_codes(
            gc,
            Path("test.py"),
            cg,
            [bit],
        )

    # Check that result contains expected sections
    assert "<CALL_FLOW>" in result
    assert "<HARNESS>" in result
    assert "harness code" in result

    # Check that bug markers are present
    lines = result.split("\n")
    bug_lines = [line for line in lines if line.strip() == "/*BUG_HERE*/"]
    assert (
        len(bug_lines) == 2
    ), result  # One for each line in the range start_line to end_line

    # Test with multiple BITs in same function
    bit2 = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="hash_password",
            file_path=str(Path("auth.py")),
            start_line=3,  # Line with z = 3
            end_line=3,
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )

    with patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = "harness code"

        result = await format_source_codes(
            gc,
            Path("test.py"),
            cg,
            [bit, bit2],
        )

        # Check all bug markers are present in source code section
        lines = result.split("\n")
        source_start = -1
        source_end = -1
        for i, line in enumerate(lines):
            if "<SOURCE>" in line:
                source_start = i + 1  # Skip the <SOURCE> line
            elif "</SOURCE>" in line:
                source_end = i
                break

        source_lines = lines[source_start:source_end]
        bug_lines = [line for line in source_lines if line.strip() == "/*BUG_HERE*/"]
        assert len(bug_lines) == 3  # Two for first BIT, one for second BIT


def test_init_cg_analysis_prompts():
    """Test initializing CG analysis prompts."""
    # Mock dependencies
    gc = MagicMock()
    gc.code_indexer = AsyncMock()
    gc.code_indexer.search_function.return_value = [
        MagicMock(
            func_body="def test():\n    pass\n",
            name="void com.aixcc.mock_java.Test.test()",
            func_name="void com.aixcc.mock_java.Test.test()",
        )
    ]
    gc.cp = MagicMock()
    harness = MagicMock(src_path=Path("test.py"))
    harness.name = "test"  # This is what's used in the code
    gc.cp.harnesses = {"test": harness}

    cgs = {"test": [create_test_cg()]}

    bit = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="hash_password",
            file_path=str(Path("auth.py")),
            start_line=1,
            end_line=1,
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )

    with patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = "harness code"

        result = init_cg_analysis_prompts(gc, cgs, [bit])

    assert "test" in result
    assert "test_cg" in result["test"]
    assert "<CALL_FLOW>" in result["test"]["test_cg"]["cg_code"]


def test_init_single_cg_analysis_prompts():
    """Test analyzing a single CG using BITs."""
    # Mock dependencies
    gc = MagicMock()
    gc.code_indexer = AsyncMock()
    gc.code_indexer.search_function.return_value = [
        MagicMock(
            func_body="def test():\n    pass\n",
            name="void com.aixcc.mock_java.Test.test()",
            func_name="void com.aixcc.mock_java.Test.test()",
        )
    ]

    # Create test CG and harness
    cg = create_test_cg()
    harness = MagicMock(src_path=Path("test_path"))
    harness.name = "test"

    # Create BITs
    bit1 = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="hash_password",
            file_path=str(Path("auth.py")),
            start_line=1,
            end_line=1,
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )
    bit2 = BugInducingThing(
        harness_name="wrong_harness",  # This BIT should be filtered out
        func_location=LocationInfo(
            func_name="hash_password",
            file_path=str(Path("auth.py")),
            start_line=2,
            end_line=2,
        ),
        should_be_taken_lines=[],
        key_conditions=[],
        analysis_message=[],
        analyzed_functions=[],
    )

    with patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = "harness code"

        result = init_single_cg_analysis_prompts(gc, harness, cg, [bit1, bit2])

    # Check that result contains expected sections
    assert "<CALL_FLOW>" in result
    assert "<HARNESS>" in result
    assert "harness code" in result
    assert result.count("/*BUG_HERE*/") == 1  # Only bit1 should be included
