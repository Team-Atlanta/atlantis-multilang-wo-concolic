import pytest

from mlla.utils.attribute_cg import AnnotationOptions, AttributeCG
from mlla.utils.bit import AnalysisMessages, BugInducingThing, LocationInfo
from mlla.utils.cg import CG, FuncInfo


@pytest.fixture
def cpp_test_file(tmp_path):
    """Create a C++ test file with multiple functions."""
    test_file = tmp_path / "test.cpp"
    test_file.write_text(
        """#include <iostream>

void helper_function() {
    std::cout << "Helper function" << std::endl;
    // Potential bug here
}

void main_function() {
    std::cout << "Main function" << std::endl;
    helper_function();
}
"""
    )
    return test_file


@pytest.fixture
def python_test_file(tmp_path):
    """Create a Python test file with multiple functions."""
    test_file = tmp_path / "test.py"
    test_file.write_text(
        """def helper_function():
    print("Helper function")
    # Potential bug here

def main_function():
    print("Main function")
    helper_function()
"""
    )
    return test_file


@pytest.fixture
def cpp_cg(cpp_test_file):
    """Create a call graph for C++ code."""
    # Create FuncInfo objects for each function
    main_func = FuncInfo(
        func_location=LocationInfo(
            func_name="void main_function()",
            file_path=str(cpp_test_file),
            start_line=7,
            end_line=10,
        ),
        func_body=(
            'void main_function() {\n    std::cout << "Main function" << std::endl;\n  '
            "  helper_function();\n}"
        ),
    )

    helper_func = FuncInfo(
        func_location=LocationInfo(
            func_name="void helper_function()",
            file_path=str(cpp_test_file),
            start_line=3,
            end_line=6,
        ),
        func_body=(
            'void helper_function() {\n    std::cout << "Helper function" <<'
            " std::endl;\n    // Potential bug here\n}"
        ),
    )

    # Build the call graph structure
    main_func.children = [helper_func]

    return CG(name="cpp_test", path=str(cpp_test_file), root_node=main_func)


@pytest.fixture
def python_cg(python_test_file):
    """Create a call graph for Python code."""
    # Create FuncInfo objects for each function
    main_func = FuncInfo(
        func_location=LocationInfo(
            func_name="main_function",
            file_path=str(python_test_file),
            start_line=5,
            end_line=7,
        ),
        func_body=(
            'def main_function():\n    print("Main function")\n    helper_function()'
        ),
    )

    helper_func = FuncInfo(
        func_location=LocationInfo(
            func_name="helper_function",
            file_path=str(python_test_file),
            start_line=1,
            end_line=3,
        ),
        func_body=(
            'def helper_function():\n    print("Helper function")\n    # Potential bug'
            " here"
        ),
    )

    # Build the call graph structure
    main_func.children = [helper_func]

    return CG(name="python_test", path=str(python_test_file), root_node=main_func)


@pytest.fixture
def cpp_coverage_info(cpp_test_file):
    """Create coverage information for C++ code."""
    return {
        "main_function": {
            "src": str(cpp_test_file),
            "lines": [8, 9],
        },
        "helper_function": {
            "src": str(cpp_test_file),
            "lines": [4],
        },
    }


@pytest.fixture
def python_coverage_info(python_test_file):
    """Create coverage information for Python code."""
    return {
        "main_function": {
            "src": str(python_test_file),
            "lines": [6, 7],
        },
        "helper_function": {
            "src": str(python_test_file),
            "lines": [2],
        },
    }


@pytest.fixture
def cpp_bit(cpp_test_file):
    """Create a BIT for C++ code."""
    return BugInducingThing(
        harness_name="cpp_test",
        func_location=LocationInfo(
            func_name="void helper_function()",
            file_path=str(cpp_test_file),
            start_line=3,
            end_line=6,
        ),
        key_conditions=[
            LocationInfo(
                func_name="void main_function()",
                file_path=str(cpp_test_file),
                start_line=9,
                end_line=9,
            )
        ],
        should_be_taken_lines=[],
        analysis_message=[
            AnalysisMessages(
                sink_detection="Test sink in helper_function",
                vulnerability_classification="Test vulnerability",
                sanitizer_type="Test sanitizer",
                key_conditions_report="Need to call helper_function",
            )
        ],
        analyzed_functions=[],
    )


@pytest.fixture
def python_bit(python_test_file):
    """Create a BIT for Python code."""
    return BugInducingThing(
        harness_name="python_test",
        func_location=LocationInfo(
            func_name="helper_function",
            file_path=str(python_test_file),
            start_line=1,
            end_line=3,
        ),
        key_conditions=[
            LocationInfo(
                func_name="main_function",
                file_path=str(python_test_file),
                start_line=7,
                end_line=7,
            )
        ],
        should_be_taken_lines=[],
        analysis_message=[
            AnalysisMessages(
                sink_detection="Test sink in helper_function",
                vulnerability_classification="Test vulnerability",
                sanitizer_type="Test sanitizer",
                key_conditions_report="Need to call helper_function",
            )
        ],
        analyzed_functions=[],
    )


def test_cpp_attribute_cg(cpp_cg, cpp_coverage_info, cpp_bit):
    """Test AttributeCG with C++ code."""
    attr_cg = AttributeCG.from_cg(
        cpp_cg, None, coverage_info=cpp_coverage_info, bit=cpp_bit, language="cpp"
    )

    # Verify structure
    assert attr_cg.root_node.func_location.func_name == "void main_function()"
    assert len(attr_cg.root_node.children) == 1
    assert (
        attr_cg.root_node.children[0].func_location.func_name
        == "void helper_function()"
    )

    # Verify coverage
    assert attr_cg.root_node.visited_lines == [8, 9]
    assert attr_cg.root_node.children[0].visited_lines == [4]

    # Verify BIT
    assert attr_cg.bit_node == attr_cg.root_node.children[0]
    assert attr_cg.bit_node.bit_info is not None

    # Verify key conditions
    assert len(attr_cg.root_node.key_conditions) == 1
    assert attr_cg.root_node.key_conditions[0].start_line == 9

    # Get annotated function bodies
    options = AnnotationOptions(
        show_coverage=True,
        show_bug_location=True,
        show_key_conditions=True,
    )
    bodies = attr_cg.get_annotated_function_bodies(options)

    # Check that annotations are included
    assert "@VISITED" in bodies
    assert "@BUG_HERE" in bodies
    assert "@KEY_CONDITION" in bodies

    # Check that C++ syntax is preserved
    assert "std::cout" in bodies
    assert "void main_function" in bodies
    assert "void helper_function" in bodies


def test_python_attribute_cg(python_cg, python_coverage_info, python_bit):
    """Test AttributeCG with Python code."""
    attr_cg = AttributeCG.from_cg(
        python_cg,
        None,
        coverage_info=python_coverage_info,
        bit=python_bit,
        language="python",
    )

    # Verify structure
    assert attr_cg.root_node.func_location.func_name == "main_function"
    assert len(attr_cg.root_node.children) == 1
    assert attr_cg.root_node.children[0].func_location.func_name == "helper_function"

    # Verify coverage
    assert attr_cg.root_node.visited_lines == [6, 7]
    assert attr_cg.root_node.children[0].visited_lines == [2]

    # Verify BIT
    assert attr_cg.bit_node == attr_cg.root_node.children[0]
    assert attr_cg.bit_node.bit_info is not None

    # Verify key conditions
    assert len(attr_cg.root_node.key_conditions) == 1
    assert attr_cg.root_node.key_conditions[0].start_line == 7

    # Get annotated function bodies
    options = AnnotationOptions(
        show_coverage=True,
        show_bug_location=True,
        show_key_conditions=True,
    )
    bodies = attr_cg.get_annotated_function_bodies(options)

    # Check that annotations are included
    assert "@VISITED" in bodies
    assert "@BUG_HERE" in bodies
    assert "@KEY_CONDITION" in bodies

    # Check that Python syntax is preserved
    assert "def main_function" in bodies
    assert "def helper_function" in bodies


def test_language_specific_normalization(
    cpp_cg, python_cg, cpp_coverage_info, python_coverage_info
):
    """Test that function name normalization works correctly for different languages."""
    # C++ AttributeCG
    cpp_attr_cg = AttributeCG.from_cg(
        cpp_cg, None, coverage_info=cpp_coverage_info, language="cpp"
    )

    # Python AttributeCG
    python_attr_cg = AttributeCG.from_cg(
        python_cg, None, coverage_info=python_coverage_info, language="python"
    )

    # Verify coverage is correctly associated despite different function name formats
    assert cpp_attr_cg.root_node.visited_lines == [8, 9]
    assert cpp_attr_cg.root_node.children[0].visited_lines == [4]

    assert python_attr_cg.root_node.visited_lines == [6, 7]
    assert python_attr_cg.root_node.children[0].visited_lines == [2]

    # Get function lists
    cpp_funcs = cpp_attr_cg.get_func_list()
    python_funcs = python_attr_cg.get_func_list()

    # Check that function names are correctly normalized for each language
    assert len(cpp_funcs) == 2
    assert len(python_funcs) == 2

    # C++ function names should include return type and parentheses
    assert any("main_function" in func for func in cpp_funcs)
    assert any("helper_function" in func for func in cpp_funcs)

    # Python function names should be simple identifiers
    assert any("main_function" in func for func in python_funcs)
    assert any("helper_function" in func for func in python_funcs)


def test_cross_language_compatibility():
    """Test that AttributeCG can handle mixed language call graphs."""
    # Create a mixed language call graph (Python calling C++)
    python_func = FuncInfo(
        func_location=LocationInfo(
            func_name="python_function",
            file_path="/path/to/python_file.py",
            start_line=1,
            end_line=3,
        ),
        func_body="def python_function():\n    # Call C++ function\n    cpp_function()",
    )

    cpp_func = FuncInfo(
        func_location=LocationInfo(
            func_name="void cpp_function()",
            file_path="/path/to/cpp_file.cpp",
            start_line=1,
            end_line=3,
        ),
        func_body="void cpp_function() {\n    // C++ implementation\n}",
    )

    # Build the call graph structure
    python_func.children = [cpp_func]

    mixed_cg = CG(
        name="mixed_test", path="/path/to/python_file.py", root_node=python_func
    )

    # Create AttributeCG
    attr_cg = AttributeCG.from_cg(mixed_cg, None)

    # Verify structure
    assert attr_cg.root_node.func_location.func_name == "python_function"
    assert len(attr_cg.root_node.children) == 1
    assert (
        attr_cg.root_node.children[0].func_location.func_name == "void cpp_function()"
    )

    # Get function bodies
    bodies = attr_cg.get_function_bodies()

    # Check that both languages are preserved
    assert any("def python_function" in body for body in bodies)
    assert any("void cpp_function" in body for body in bodies)

    # Get function list
    func_list = attr_cg.get_func_list()

    # Check that both functions are included
    assert len(func_list) == 2
    assert any("python_function" in func for func in func_list)
    assert any("cpp_function" in func for func in func_list)
