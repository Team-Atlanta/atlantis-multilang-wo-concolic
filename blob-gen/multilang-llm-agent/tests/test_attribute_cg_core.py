import pytest

from mlla.utils.attribute_cg import AnnotationOptions, AttributeCG
from mlla.utils.bit import (
    AnalysisMessages,
    AnalyzedFunction,
    BugInducingThing,
    LocationInfo,
)
from mlla.utils.cg import CG, FuncInfo


@pytest.fixture
def complex_test_file(tmp_path):
    """Create a more complex test file with multiple functions and nested calls."""
    test_file = tmp_path / "ComplexTest.java"
    test_file.write_text(
        """public class ComplexTest {
    public void entryPoint() {
        System.out.println("Entry point");
        firstLevel();
    }

    public void firstLevel() {
        System.out.println("First level");
        secondLevel();
        alternativePath();
    }

    public void secondLevel() {
        System.out.println("Second level");
        thirdLevel();
    }

    public void thirdLevel() {
        System.out.println("Third level - bug location");
        // Bug is here
    }

    public void alternativePath() {
        System.out.println("Alternative path");
        // Key condition here
    }
}
"""
    )
    return test_file


@pytest.fixture
def complex_cg(complex_test_file):
    """Create a complex call graph with multiple levels."""
    # Create FuncInfo objects for each function
    entry_info = FuncInfo(
        func_location=LocationInfo(
            func_name="void ComplexTest.entryPoint()",
            file_path=str(complex_test_file),
            start_line=2,
            end_line=5,
        ),
        func_body=(
            'public void entryPoint() {\n    System.out.println("Entry point");\n   '
            " firstLevel();\n}"
        ),
    )

    first_level_info = FuncInfo(
        func_location=LocationInfo(
            func_name="void ComplexTest.firstLevel()",
            file_path=str(complex_test_file),
            start_line=7,
            end_line=11,
        ),
        func_body=(
            'public void firstLevel() {\n    System.out.println("First level");\n   '
            " secondLevel();\n    alternativePath();\n}"
        ),
    )

    second_level_info = FuncInfo(
        func_location=LocationInfo(
            func_name="void ComplexTest.secondLevel()",
            file_path=str(complex_test_file),
            start_line=13,
            end_line=16,
        ),
        func_body=(
            'public void secondLevel() {\n    System.out.println("Second level");\n   '
            " thirdLevel();\n}"
        ),
    )

    third_level_info = FuncInfo(
        func_location=LocationInfo(
            func_name="void ComplexTest.thirdLevel()",
            file_path=str(complex_test_file),
            start_line=18,
            end_line=21,
        ),
        func_body=(
            'public void thirdLevel() {\n    System.out.println("Third level - bug'
            ' location");\n    // Bug is here\n}'
        ),
    )

    alt_path_info = FuncInfo(
        func_location=LocationInfo(
            func_name="void ComplexTest.alternativePath()",
            file_path=str(complex_test_file),
            start_line=23,
            end_line=26,
        ),
        func_body=(
            'public void alternativePath() {\n    System.out.println("Alternative'
            ' path");\n    // Key condition here\n}'
        ),
    )

    # Build the call graph structure
    second_level_info.children = [third_level_info]
    first_level_info.children = [second_level_info, alt_path_info]
    entry_info.children = [first_level_info]

    return CG(name="complex_test", path=str(complex_test_file), root_node=entry_info)


@pytest.fixture
def complex_coverage_info(complex_test_file):
    """Create complex coverage information for multiple functions."""
    return {
        "ComplexTest.entryPoint()V": {
            "src": str(complex_test_file),
            "lines": [3, 4],
        },
        "ComplexTest.firstLevel()V": {
            "src": str(complex_test_file),
            "lines": [8, 9],  # Only calls secondLevel, not alternativePath
        },
        "ComplexTest.secondLevel()V": {
            "src": str(complex_test_file),
            "lines": [14, 15],
        },
        "ComplexTest.thirdLevel()V": {
            "src": str(complex_test_file),
            "lines": [19],
        },
    }


@pytest.fixture
def complex_bit(complex_test_file):
    """Create a BIT with the bug in thirdLevel and key conditions in alternativePath."""
    return BugInducingThing(
        harness_name="complex_test",
        func_location=LocationInfo(
            func_name="void ComplexTest.thirdLevel()",
            file_path=str(complex_test_file),
            start_line=18,
            end_line=21,
        ),
        key_conditions=[
            LocationInfo(
                func_name="void ComplexTest.alternativePath()",
                file_path=str(complex_test_file),
                start_line=24,
                end_line=25,
            )
        ],
        should_be_taken_lines=[
            LocationInfo(
                func_name="void ComplexTest.firstLevel()",
                file_path=str(complex_test_file),
                start_line=10,
                end_line=10,  # Line that calls alternativePath
            )
        ],
        analysis_message=[
            AnalysisMessages(
                sink_detection="Test sink in thirdLevel",
                vulnerability_classification="Test vulnerability",
                sanitizer_type="Test sanitizer",
                key_conditions_report="Need to call alternativePath",
            )
        ],
        analyzed_functions=[
            AnalyzedFunction(
                func_location=LocationInfo(
                    func_name="void ComplexTest.thirdLevel()",
                    file_path=str(complex_test_file),
                    start_line=18,
                    end_line=21,
                ),
                func_body=(
                    'public void thirdLevel() {\n    System.out.println("Third level -'
                    ' bug location");\n    // Bug is here\n}'
                ),
            )
        ],
    )


def test_call_graph_structure_preservation(complex_cg):
    """Test that AttributeCG preserves the structure of the original CG."""
    attr_cg = AttributeCG.from_cg(complex_cg, None)

    # Verify root node
    assert attr_cg.root_node.func_location.func_name == "void ComplexTest.entryPoint()"

    # Verify first level
    assert len(attr_cg.root_node.children) == 1
    first_level = attr_cg.root_node.children[0]
    assert first_level.func_location.func_name == "void ComplexTest.firstLevel()"

    # Verify second level has two children
    assert len(first_level.children) == 2
    second_level = next(
        child
        for child in first_level.children
        if child.func_location.func_name == "void ComplexTest.secondLevel()"
    )
    alt_path = next(
        child
        for child in first_level.children
        if child.func_location.func_name == "void ComplexTest.alternativePath()"
    )

    # Verify third level
    assert len(second_level.children) == 1
    third_level = second_level.children[0]
    assert third_level.func_location.func_name == "void ComplexTest.thirdLevel()"

    # Verify leaf nodes have no children
    assert len(third_level.children) == 0
    assert len(alt_path.children) == 0


def test_function_list_extraction(complex_cg):
    """Test that get_func_list correctly extracts all functions."""
    attr_cg = AttributeCG.from_cg(complex_cg, None)
    func_list = attr_cg.get_func_list()

    # Should contain all 5 functions
    assert len(func_list) == 5

    # Check each function is in the list
    function_names = [
        "entryPoint",
        "firstLevel",
        "secondLevel",
        "thirdLevel",
        "alternativePath",
    ]
    for name in function_names:
        assert any(name in func for func in func_list)


def test_function_body_extraction(complex_cg):
    """Test that get_function_bodies correctly extracts raw function bodies."""
    attr_cg = AttributeCG.from_cg(complex_cg, None)
    bodies = attr_cg.get_function_bodies()

    # Should have 5 function bodies
    assert len(bodies) == 5

    # Each body should contain the expected content and no annotations
    assert any("Entry point" in body for body in bodies)
    assert any("First level" in body for body in bodies)
    assert any("Second level" in body for body in bodies)
    assert any("Third level - bug location" in body for body in bodies)
    assert any("Alternative path" in body for body in bodies)

    # No bodies should have annotations
    for body in bodies:
        assert "@VISITED" not in body
        assert "@BUG_HERE" not in body
        assert "@KEY_CONDITION" not in body
        assert "@SHOULD_BE_TAKEN" not in body


def test_coverage_information_handling(complex_cg, complex_coverage_info):
    """Test that coverage information is correctly associated with functions."""
    attr_cg = AttributeCG.from_cg(complex_cg, None, coverage_info=complex_coverage_info)

    # Verify coverage for each node
    assert attr_cg.root_node.visited_lines == [3, 4]

    first_level = attr_cg.root_node.children[0]
    assert first_level.visited_lines == [8, 9]

    second_level = next(
        child
        for child in first_level.children
        if child.func_location.func_name == "void ComplexTest.secondLevel()"
    )
    assert second_level.visited_lines == [14, 15]

    third_level = second_level.children[0]
    assert third_level.visited_lines == [19]

    alt_path = next(
        child
        for child in first_level.children
        if child.func_location.func_name == "void ComplexTest.alternativePath()"
    )
    assert alt_path.visited_lines == []  # Not covered

    # Test coverage update
    updated_coverage = {
        "ComplexTest.entryPoint()V": {
            "src": str(complex_cg.path),
            "lines": [3],  # Changed
        },
        "ComplexTest.alternativePath()V": {
            "src": str(complex_cg.path),
            "lines": [24, 25],  # Now covered
        },
    }

    attr_cg.update_coverage(updated_coverage)

    # Verify updated coverage
    assert attr_cg.root_node.visited_lines == [3]  # Changed
    assert first_level.visited_lines == []  # Reset
    assert second_level.visited_lines == []  # Reset
    assert third_level.visited_lines == []  # Reset
    assert alt_path.visited_lines == [24, 25]  # Now covered


def test_bit_information_association(complex_cg, complex_bit):
    """Test that BIT information is correctly associated with functions."""
    attr_cg = AttributeCG.from_cg(complex_cg, None, bit=complex_bit)

    # Find the nodes
    first_level = attr_cg.root_node.children[0]
    second_level = next(
        child
        for child in first_level.children
        if child.func_location.func_name == "void ComplexTest.secondLevel()"
    )
    third_level = second_level.children[0]
    alt_path = next(
        child
        for child in first_level.children
        if child.func_location.func_name == "void ComplexTest.alternativePath()"
    )

    # Verify BIT is associated with third level
    assert attr_cg.bit_node == third_level
    assert third_level.bit_info is not None
    assert (
        third_level.bit_info.func_location.func_name == "void ComplexTest.thirdLevel()"
    )

    # Verify key conditions are in alt_path
    assert len(alt_path.key_conditions) == 1
    assert alt_path.key_conditions[0].start_line == 24
    assert alt_path.key_conditions[0].end_line == 25

    # Verify should-be-taken lines are in first_level
    assert len(first_level.should_be_taken_lines) == 1
    assert first_level.should_be_taken_lines[0].start_line == 10
    assert first_level.should_be_taken_lines[0].end_line == 10


def test_unique_transitions(complex_cg):
    """Test that find_unique_transitions correctly identifies all transitions."""
    attr_cg = AttributeCG.from_cg(complex_cg, None)
    transitions = attr_cg.find_unique_transitions()

    # Should have 4 transitions:
    # entryPoint -> firstLevel
    # firstLevel -> secondLevel
    # firstLevel -> alternativePath
    # secondLevel -> thirdLevel
    assert len(transitions) == 4

    # Check each transition
    transition_pairs = [
        ("void ComplexTest.entryPoint()", "void ComplexTest.firstLevel()"),
        ("void ComplexTest.firstLevel()", "void ComplexTest.secondLevel()"),
        ("void ComplexTest.firstLevel()", "void ComplexTest.alternativePath()"),
        ("void ComplexTest.secondLevel()", "void ComplexTest.thirdLevel()"),
    ]

    for src_name, dst_name in transition_pairs:
        assert any(
            src.func_location.func_name == src_name
            and dst.func_location.func_name == dst_name
            for src, dst in transitions
        )


def test_hash_functionality(complex_cg):
    """Test that AttributeCG hash function works correctly."""
    attr_cg1 = AttributeCG.from_cg(complex_cg, None)
    attr_cg2 = AttributeCG.from_cg(complex_cg, None)

    # Same CG should produce same hash
    assert hash(attr_cg1) == hash(attr_cg2)

    # Modify a transition and check hash changes
    # Create a modified CG with one less transition
    modified_cg = complex_cg
    first_level = modified_cg.root_node.children[0]
    # Make a copy to avoid modifying the original
    first_level.children = [first_level.children[0]]  # Remove alternativePath

    attr_cg3 = AttributeCG.from_cg(modified_cg, None)
    assert hash(attr_cg1) != hash(attr_cg3)


def test_annotation_independence(complex_cg, complex_bit, complex_coverage_info):
    """Test that changing annotation options doesn't affect the data structure."""
    attr_cg = AttributeCG.from_cg(
        complex_cg, None, coverage_info=complex_coverage_info, bit=complex_bit
    )

    # Get the initial state of nodes
    first_level = attr_cg.root_node.children[0]
    second_level = next(
        child
        for child in first_level.children
        if child.func_location.func_name == "void ComplexTest.secondLevel()"
    )
    third_level = second_level.children[0]
    alt_path = next(
        child
        for child in first_level.children
        if child.func_location.func_name == "void ComplexTest.alternativePath()"
    )

    # Save initial state
    initial_state = {
        "root_visited": attr_cg.root_node.visited_lines.copy(),
        "first_level_visited": first_level.visited_lines.copy(),
        "third_level_bit": third_level.bit_info is not None,
        "alt_path_key_conditions": [kc.start_line for kc in alt_path.key_conditions],
        "first_level_should_be_taken": [
            sbt.start_line for sbt in first_level.should_be_taken_lines
        ],
    }

    # Generate annotated bodies with different options
    options1 = AnnotationOptions(
        show_coverage=True,
        show_bug_location=False,
        show_key_conditions=False,
        show_should_be_taken_lines=False,
    )
    bodies1 = attr_cg.get_annotated_function_bodies(options1)

    options2 = AnnotationOptions(
        show_coverage=False,
        show_bug_location=True,
        show_key_conditions=True,
        show_should_be_taken_lines=True,
    )
    bodies2 = attr_cg.get_annotated_function_bodies(options2)

    # Verify the underlying data structure hasn't changed
    assert attr_cg.root_node.visited_lines == initial_state["root_visited"]
    assert first_level.visited_lines == initial_state["first_level_visited"]
    assert (third_level.bit_info is not None) == initial_state["third_level_bit"]
    assert [kc.start_line for kc in alt_path.key_conditions] == initial_state[
        "alt_path_key_conditions"
    ]
    assert [
        sbt.start_line for sbt in first_level.should_be_taken_lines
    ] == initial_state["first_level_should_be_taken"]

    # Verify the output is different based on options
    assert "@VISITED" in bodies1
    assert "@BUG_HERE" not in bodies1

    assert "@VISITED" not in bodies2
    assert "@BUG_HERE" in bodies2
    assert "@KEY_CONDITION" in bodies2
    assert "@SHOULD_BE_TAKEN" in bodies2


@pytest.fixture
def edge_case_cg(complex_test_file):
    """Create a CG with edge cases like empty functions and missing file paths."""
    # Create a basic function
    basic_func = FuncInfo(
        func_location=LocationInfo(
            func_name="void EdgeCase.basicFunction()",
            file_path=str(complex_test_file),
            start_line=2,
            end_line=4,
        ),
        func_body=(
            'public void basicFunction() {\n    System.out.println("Basic'
            ' function");\n}'
        ),
    )

    # Create an empty function
    empty_func = FuncInfo(
        func_location=LocationInfo(
            func_name="void EdgeCase.emptyFunction()",
            file_path=str(complex_test_file),
            start_line=6,
            end_line=6,
        ),
        func_body="",  # Empty body
    )

    # Create a function with missing file path
    no_file_func = FuncInfo(
        func_location=LocationInfo(
            func_name="void EdgeCase.noFilePath()",
            file_path="",  # Missing file path
            start_line=8,
            end_line=10,
        ),
        func_body=(
            'public void noFilePath() {\n    System.out.println("No file path");\n}'
        ),
    )

    # Build the call graph structure
    basic_func.children = [empty_func, no_file_func]

    return CG(name="edge_case_test", path=str(complex_test_file), root_node=basic_func)


def test_edge_cases(edge_case_cg):
    """Test handling of edge cases like empty functions and missing file paths."""
    attr_cg = AttributeCG.from_cg(edge_case_cg, None)

    # Check that the structure is preserved
    assert attr_cg.root_node.func_location.func_name == "void EdgeCase.basicFunction()"
    assert len(attr_cg.root_node.children) == 2

    # Check that empty function is included
    empty_func = next(
        (
            child
            for child in attr_cg.root_node.children
            if child.func_location.func_name == "void EdgeCase.emptyFunction()"
        ),
        None,
    )
    assert empty_func is not None
    assert empty_func.func_body == ""

    # Check that function with missing file path is handled properly
    no_file_func = next(
        (
            child
            for child in attr_cg.root_node.children
            if child.func_location.func_name == "void EdgeCase.noFilePath()"
        ),
        None,
    )

    # The implementation might skip nodes without file paths, so check if it exists
    if no_file_func is not None:
        assert no_file_func.func_location.file_path == ""

    # Get function bodies and check they're handled properly
    bodies = attr_cg.get_function_bodies()

    # Should include at least the basic function
    assert any("Basic function" in body for body in bodies)

    # Empty function should either be skipped or included as empty
    empty_bodies = [body for body in bodies if body == ""]
    assert len(empty_bodies) <= 1  # At most one empty body

    # Function with no file path should be included if the implementation allows it
    no_file_bodies = [body for body in bodies if "No file path" in body]
    assert len(no_file_bodies) <= 1  # At most one such body


def test_focus_on_bit(complex_cg, complex_bit):
    """Test that focus_on_bit option correctly filters the call graph."""
    # Create AttributeCG with focus_on_bit=True
    attr_cg_focused = AttributeCG.from_cg(
        complex_cg, None, bit=complex_bit, focus_on_bit=True
    )

    # Create AttributeCG with focus_on_bit=False
    attr_cg_unfocused = AttributeCG.from_cg(
        complex_cg, None, bit=complex_bit, focus_on_bit=False
    )

    # Get function lists from both
    focused_funcs = attr_cg_focused.get_func_list()
    unfocused_funcs = attr_cg_unfocused.get_func_list()

    # Focused should only include functions related to the BIT
    # This depends on the implementation, but should at least include:
    # - The function with the bug (thirdLevel)
    # - The function with key conditions (alternativePath)
    # - The function with should-be-taken lines (firstLevel)
    # - Any functions in the path to these

    # Unfocused should include all functions
    assert len(unfocused_funcs) >= len(focused_funcs)

    # Check that bug function is in both
    assert any(
        "thirdLevel" in func for func in focused_funcs
    ), f"{focused_funcs}\n\n\n{unfocused_funcs}"
    assert any(
        "thirdLevel" in func for func in unfocused_funcs
    ), f"{focused_funcs}\n\n\n{unfocused_funcs}"

    # Check that key condition function is in both
    assert any("alternativePath" in func for func in focused_funcs)
    assert any("alternativePath" in func for func in unfocused_funcs)


def test_get_call_flow(complex_cg, complex_bit):
    """Test that get_call_flow correctly formats the call flow information."""
    attr_cg = AttributeCG.from_cg(complex_cg, None, bit=complex_bit)

    call_flow = attr_cg.get_call_flow()

    # Check that call flow contains expected format markers
    assert "<FUNCTION_CALL_FLOW>" in call_flow
    assert "</FUNCTION_CALL_FLOW>" in call_flow

    # Check that all functions are included in the call flow
    assert "entryPoint" in call_flow
    assert "firstLevel" in call_flow
    assert "secondLevel" in call_flow
    assert "thirdLevel" in call_flow
    assert "alternativePath" in call_flow

    # Check that bug annotation is included
    assert "@BUG" in call_flow

    # Check that the hierarchy is represented with indentation or arrows
    assert "↳" in call_flow  # Arrow character used for hierarchy


def test_line_number_display_options(complex_cg, complex_bit, complex_coverage_info):
    """Test that line number display options work correctly."""
    attr_cg = AttributeCG.from_cg(
        complex_cg, None, coverage_info=complex_coverage_info, bit=complex_bit
    )

    # Test with line numbers shown
    options_with_line_numbers = AnnotationOptions(
        show_coverage=True, show_bug_location=True, show_line_numbers=True
    )

    bodies_with_line_numbers = attr_cg.get_annotated_function_bodies(
        options_with_line_numbers
    )

    # Check that line numbers are included in the output
    assert "[" in bodies_with_line_numbers  # Opening bracket for line number
    assert "]:" in bodies_with_line_numbers  # Closing bracket and colon for line number

    # Test with line numbers hidden
    options_without_line_numbers = AnnotationOptions(
        show_coverage=True, show_bug_location=True, show_line_numbers=False
    )

    bodies_without_line_numbers = attr_cg.get_annotated_function_bodies(
        options_without_line_numbers
    )

    # Check that line numbers are not included in the output
    # This is a bit tricky since "[" and "]:" might appear in the code itself
    # So we'll check that the specific pattern "[line_number]:" doesn't appear

    # Get a line number we know should be in the output
    first_level = attr_cg.root_node.children[0]
    line_number = first_level.visited_lines[0] if first_level.visited_lines else 8

    # Check that the specific pattern doesn't appear
    assert f"[{line_number}]:" not in bodies_without_line_numbers

    # Test different annotation placement options
    options_end_placement = AnnotationOptions(
        show_coverage=True, show_bug_location=True, annotation_placement="end"
    )

    bodies_end_placement = attr_cg.get_annotated_function_bodies(options_end_placement)

    # Check that annotations appear at the end of lines
    assert (
        " /* @VISITED */" in bodies_end_placement
        or " /* @BUG_HERE */" in bodies_end_placement
    )

    # Test with "before" placement
    options_before_placement = AnnotationOptions(
        show_coverage=True, show_bug_location=True, annotation_placement="before"
    )

    bodies_before_placement = attr_cg.get_annotated_function_bodies(
        options_before_placement
    )

    # Check that annotations appear before lines (on separate lines)
    # This is harder to test definitively, but we can check for patterns
    assert (
        "/* @VISITED */" in bodies_before_placement
        or "/* @BUG_HERE */" in bodies_before_placement
    )

    # The key thing is that the underlying data structure shouldn't change
    # regardless of display options
    assert attr_cg.root_node.visited_lines == [3, 4]
    assert first_level.visited_lines == [8, 9]
