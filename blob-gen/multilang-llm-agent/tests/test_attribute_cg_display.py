from typing import List

import pytest
import pytest_asyncio

from mlla.codeindexer.main import CodeIndexer
from mlla.utils.attribute_cg import AnnotationOptions, AttributeCG
from mlla.utils.bit import AnalysisMessages, BugInducingThing, LocationInfo
from mlla.utils.cg import CG, FuncInfo


def split_function_bodies(content: str) -> List[str]:
    """Split content into function bodies based on function tags.

    Handles cases where the same function might appear with different tags
    (e.g. both ENTRY_FUNCTION and VULNERABLE_FUNCTION tags).
    Returns only unique function bodies based on the function content.
    """
    bodies = []
    current_body = []
    in_function = False
    seen_contents = set()

    for line in content.split("\n"):
        if any(
            tag in line
            for tag in ["<FUNCTION>", "<ENTRY_FUNCTION>", "<VULNERABLE_FUNCTION>"]
        ):
            in_function = True
            current_body = [line]
        elif any(
            tag in line
            for tag in ["</FUNCTION>", "</ENTRY_FUNCTION>", "</VULNERABLE_FUNCTION>"]
        ):
            in_function = False
            current_body.append(line)
            body = "\n".join(current_body)

            # Extract function content without tags to check for duplicates
            content_lines = body.split("\n")[1:-1]  # Skip first and last lines (tags)
            content_key = "\n".join(content_lines)

            if content_key not in seen_contents:
                seen_contents.add(content_key)
                bodies.append(body)
        elif in_function:
            current_body.append(line)

    return bodies


@pytest.fixture
def test_file(tmp_path):
    test_file = tmp_path / "Test.java"
    test_file.write_text(
        """public class TestAttributeCG {
    public void testMain() {
        System.out.println("test main");
        testHelper();
    }

    public void testHelper() {
        System.out.println("test helper");
    }
}
"""
    )
    return test_file


@pytest.fixture
def sample_coverage_info(test_file):
    return {
        "TestAttributeCG.testMain()V": {  # void return type, no params
            "src": str(test_file),
            "lines": [3, 4],
        },
        "TestAttributeCG.testHelper()V": {  # void return type, no params
            "src": str(test_file),
            "lines": [8],
        },
    }


@pytest.fixture
def sample_bits(test_file):
    return [
        BugInducingThing(
            harness_name="test",
            func_location=LocationInfo(
                func_name="void TestAttributeCG.testMain()",
                file_path=str(test_file),
                start_line=2,
                end_line=4,
            ),
            key_conditions=[
                LocationInfo(
                    func_name="void TestAttributeCG.testMain()",
                    file_path=str(test_file),
                    start_line=2,
                    end_line=2,
                )
            ],
            should_be_taken_lines=[
                LocationInfo(
                    func_name="void TestAttributeCG.testMain()",
                    file_path=str(test_file),
                    start_line=3,
                    end_line=3,
                )
            ],
            analysis_message=[
                AnalysisMessages(
                    sink_detection="test sink",
                    vulnerability_classification="test vuln",
                    sanitizer_type="test sanitizer",
                    key_conditions_report="",
                )
            ],
            analyzed_functions=[],
        )
    ]


@pytest_asyncio.fixture
async def sample_cg(test_file, random_project_name, redis_client):
    # Clean any existing test data
    # for key in redis_client.keys("test-code-index*"):
    #     redis_client.delete(key)

    try:
        code_indexer = CodeIndexer(redis_client)
        await code_indexer.index_project(
            random_project_name, [test_file.parent], "jvm", overwrite=True
        )

        # Get the CG for the main function
        main_results = await code_indexer.search_function("testMain")
        assert len(main_results) == 1
        main_info = main_results[0]

        # Get the CG for the helper function
        helper_results = await code_indexer.search_function("testHelper")
        assert len(helper_results) == 1
        helper_info = helper_results[0]

        # Create CG with parent-child relationship
        root = FuncInfo(
            func_location=LocationInfo(
                func_name=main_info.func_name,
                file_path=main_info.file_path,
                start_line=main_info.start_line,
                end_line=main_info.end_line,
            ),
            func_body=main_info.func_body,
            children=[
                FuncInfo(
                    func_location=LocationInfo(
                        func_name=helper_info.func_name,
                        file_path=helper_info.file_path,
                        start_line=helper_info.start_line,
                        end_line=helper_info.end_line,
                    ),
                    func_body=helper_info.func_body,
                    children=[],
                )
            ],
        )
        return CG(name="test", path=str(test_file), root_node=root)
    finally:
        # Clean up Redis data
        # for key in redis_client.keys("test-code-index*"):
        #     redis_client.delete(key)
        pass


def test_attribute_cg_creation(sample_cg, sample_coverage_info, sample_bits):
    cg = sample_cg
    attr_cg = AttributeCG.from_cg(
        cg, None, coverage_info=sample_coverage_info, bit=sample_bits[0]
    )

    # Check root node
    assert (
        attr_cg.root_node.func_location.func_name == "void TestAttributeCG.testMain()"
    )
    assert attr_cg.root_node.visited_lines == [3, 4]  # Absolute line numbers
    assert attr_cg.root_node.bit_info is not None
    assert len(attr_cg.root_node.should_be_taken_lines) == 1
    assert attr_cg.root_node.should_be_taken_lines[0].start_line == 3
    assert attr_cg.root_node.should_be_taken_lines[0].end_line == 3

    # Check child node
    helper_node = attr_cg.root_node.children[0]
    assert helper_node.func_location.func_name == "void TestAttributeCG.testHelper()"
    assert helper_node.visited_lines == [8]  # Absolute line number
    assert helper_node.bit_info is None
    assert len(helper_node.should_be_taken_lines) == 0


def test_get_annotated_function_bodies(sample_cg, sample_coverage_info, sample_bits):
    # Test with both coverage and bug info
    cg = sample_cg
    attr_cg = AttributeCG.from_cg(
        cg, None, coverage_info=sample_coverage_info, bit=sample_bits[0]
    )

    options = AnnotationOptions(
        show_coverage=True,
        show_bug_location=True,
        show_key_conditions=True,
        show_should_be_taken_lines=True,
        # show_metadata=True,
    )
    bodies = attr_cg.get_annotated_function_bodies(options)
    bodies = bodies.replace("<SOURCE>\n", "").replace("</SOURCE>\n", "")
    bodies = split_function_bodies(bodies)
    assert len(bodies) == 2

    # Check annotations in main function
    main_body = bodies[0]
    assert "@VISITED" in main_body
    assert "@BUG_HERE" in main_body
    assert "@KEY_CONDITION" in main_body
    # assert "BUG: test vuln" in main_body

    # Check annotations in helper function
    helper_body = bodies[1]
    assert "@VISITED" in helper_body, bodies
    assert "@BUG_HERE" not in helper_body
    assert "@KEY_CONDITION" not in helper_body


def test_coverage_only(sample_cg, sample_coverage_info):
    # Test with only coverage info
    cg = sample_cg
    attr_cg = AttributeCG.from_cg(cg, None, coverage_info=sample_coverage_info)

    options = AnnotationOptions(
        show_coverage=True,
        show_bug_location=False,
        show_key_conditions=False,
        show_should_be_taken_lines=False,
        show_metadata=False,
    )
    bodies = attr_cg.get_annotated_function_bodies(options)
    bodies = bodies.replace("<SOURCE>\n", "").replace("</SOURCE>\n", "")
    bodies = split_function_bodies(bodies)
    assert len(bodies) == 2

    # Check main function has only coverage annotations
    main_body = bodies[0]
    assert "@VISITED" in main_body
    assert "@BUG_HERE" not in main_body
    assert "@KEY_CONDITION" not in main_body
    assert "<VULNERABLE_FUNCTION>" not in main_body
    assert "<ENTRY_FUNCTION>" in main_body


@pytest.fixture
def updated_coverage_info(test_file):
    return {
        "TestAttributeCG.testMain()V": {  # void return type, no params
            "src": str(test_file),
            "lines": [3],  # Only line 3 is visited now
        },
        "TestAttributeCG.testHelper()V": {  # void return type, no params
            "src": str(test_file),
            "lines": [8],  # Line within testHelper() function
        },
    }


def test_update_coverage(sample_cg, sample_coverage_info, updated_coverage_info):
    # Create AttributeCG with initial coverage
    cg = sample_cg
    attr_cg = AttributeCG.from_cg(cg, None, coverage_info=sample_coverage_info)

    # Verify initial coverage
    assert attr_cg.root_node.visited_lines == [3, 4]  # Absolute line numbers
    assert attr_cg.root_node.children[0].visited_lines == [8]  # Absolute line number

    # Update coverage
    attr_cg.update_coverage(updated_coverage_info)

    # Verify updated coverage
    assert attr_cg.root_node.visited_lines == [3]  # Only line 3 is visited now
    assert attr_cg.root_node.children[0].visited_lines == [
        8
    ]  # Line within testHelper() function

    # Check that annotations are updated
    options = AnnotationOptions(
        show_coverage=True,
        show_bug_location=False,
        show_key_conditions=False,
        show_should_be_taken_lines=False,
        show_metadata=False,
    )
    bodies = attr_cg.get_annotated_function_bodies(options)
    bodies = bodies.replace("<SOURCE>\n", "").replace("</SOURCE>\n", "")
    bodies = split_function_bodies(bodies)
    main_body = bodies[0]
    helper_body = bodies[1]

    # Count VISITED annotations
    assert main_body.count("@VISITED") == 1  # Should have 1 visited line (line 3)
    assert helper_body.count("@VISITED") == 1  # Should have 1 visited line (line 8)


def test_get_raw_function_bodies(sample_cg, sample_coverage_info, sample_bits):
    # Create AttributeCG with both coverage and bug info
    cg = sample_cg
    attr_cg = AttributeCG.from_cg(
        cg, None, coverage_info=sample_coverage_info, bit=sample_bits[0]
    )

    # Get raw function bodies
    bodies = attr_cg.get_function_bodies()
    assert len(bodies) == 2

    # Check that bodies have no annotations
    main_body = bodies[0]
    assert "@VISITED" not in main_body
    assert "@BUG_HERE" not in main_body
    assert "@KEY_CONDITION" not in main_body
    assert "<VULNERABLE_FUNCTION>" not in main_body
    assert "<ENTRY_FUNCTION>" not in main_body

    helper_body = bodies[1]
    assert "@VISITED" not in helper_body
    assert "@BUG_HERE" not in helper_body
    assert "@KEY_CONDITION" not in helper_body
    assert "BUG: test vuln" not in helper_body


def test_selective_annotations(sample_cg, sample_coverage_info, sample_bits):
    cg = sample_cg
    attr_cg = AttributeCG.from_cg(
        cg, None, coverage_info=sample_coverage_info, bit=sample_bits[0]
    )

    # Test with only coverage annotations
    options = AnnotationOptions(
        show_coverage=True,
        show_bug_location=False,
        show_key_conditions=False,
        show_metadata=False,
    )
    bodies = attr_cg.get_annotated_function_bodies(options)
    bodies = split_function_bodies(bodies)
    main_body = bodies[0]
    assert "@VISITED" in main_body
    assert "@BUG_HERE" not in main_body
    assert "@KEY_CONDITION" not in main_body

    # Test with only bug location and metadata
    options = AnnotationOptions(
        show_coverage=False,
        show_bug_location=True,
        show_key_conditions=False,
        show_should_be_taken_lines=False,
        show_metadata=True,
    )
    bodies = attr_cg.get_annotated_function_bodies(options)
    bodies = split_function_bodies(bodies)
    main_body = bodies[0]
    assert "@VISITED" not in main_body
    assert "@BUG_HERE" in main_body
    assert "@KEY_CONDITION" not in main_body
    assert "@SHOULD_BE_TAKEN" not in main_body

    # Test with only should be taken lines
    options = AnnotationOptions(
        show_coverage=False,
        show_bug_location=False,
        show_key_conditions=False,
        show_should_be_taken_lines=True,
        show_metadata=False,
    )
    bodies = attr_cg.get_annotated_function_bodies(options)
    bodies = split_function_bodies(bodies)
    main_body = bodies[0]
    assert "@VISITED" not in main_body
    assert "@BUG_HERE" not in main_body
    assert "@KEY_CONDITION" not in main_body
    assert "@SHOULD_BE_TAKEN" in main_body


def test_bug_only(sample_cg, sample_bits):
    # Test with only bug info
    cg = sample_cg
    attr_cg = AttributeCG.from_cg(cg, None, bit=sample_bits[0])

    options = AnnotationOptions(
        show_bug_location=False,  # Don't show bug location
        show_key_conditions=True,
        show_should_be_taken_lines=True,
        show_metadata=True,
        show_coverage=False,
    )
    bodies = attr_cg.get_annotated_function_bodies(options)
    bodies = split_function_bodies(bodies)

    # Get main and helper function bodies
    main_body = next(body for body in bodies if "testMain" in body)
    helper_body = next(body for body in bodies if "testHelper" in body)

    # Check main function has only key condition and should be taken annotations
    assert "@VISITED" not in main_body
    assert "@BUG_HERE" not in main_body  # Bug location should not be shown
    assert "@KEY_CONDITION" in main_body
    assert "@SHOULD_BE_TAKEN" in main_body

    # Check helper function has no annotations
    assert "@VISITED" not in helper_body
    assert "@BUG_HERE" not in helper_body
    assert "@KEY_CONDITION" in helper_body
    assert "@SHOULD_BE_TAKEN" in helper_body


def test_line_number_conversion(sample_cg, sample_coverage_info, sample_bits):
    """Test that absolute line numbers are correctly converted."""
    cg = sample_cg
    attr_cg = AttributeCG.from_cg(
        cg, None, coverage_info=sample_coverage_info, bit=sample_bits[0]
    )

    # Get annotated body
    options = AnnotationOptions(
        show_coverage=True,
        show_bug_location=True,
        show_key_conditions=True,
        show_should_be_taken_lines=True,
        show_metadata=True,
    )
    bodies = attr_cg.get_annotated_function_bodies(options)
    bodies = bodies.replace("<SOURCE>\n", "").replace("</SOURCE>\n", "")
    bodies = split_function_bodies(bodies)

    # Get the main function body
    main_body = next(body for body in bodies if "testMain" in body)
    lines = main_body.split("\n")

    # Find lines containing specific annotations
    visited_lines = [i for i, line in enumerate(lines) if "@VISITED" in line]
    bug_lines = [i for i, line in enumerate(lines) if "@BUG_HERE" in line]
    should_be_taken_lines = [
        i for i, line in enumerate(lines) if "@SHOULD_BE_TAKEN" in line
    ]

    # Check that annotations are on the correct lines
    assert len(visited_lines) == 2  # Should have 2 visited lines
    assert len(bug_lines) == 3  # Should have 3 bug lines
    assert len(should_be_taken_lines) == 1  # Should have 1 should be taken line

    # Check that annotations are in the correct order
    assert visited_lines[0] < visited_lines[1]  # First visited line before second
    assert bug_lines == sorted(bug_lines)  # Bug lines in order
    assert (
        should_be_taken_lines[0] in visited_lines
    )  # Should be taken line is also visited


def test_annotations_different_functions(sample_cg):
    # Create a BIT with key_conditions and should_be_taken_lines"
    bit = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="void TestAttributeCG.testMain()",
            file_path=str(sample_cg.path),
            start_line=2,
            end_line=4,
        ),
        key_conditions=[
            LocationInfo(
                func_name="void TestAttributeCG.testHelper()",
                file_path=str(sample_cg.path),
                start_line=8,
                end_line=8,
            )
        ],
        should_be_taken_lines=[
            LocationInfo(
                func_name="void TestAttributeCG.testHelper()",
                file_path=str(sample_cg.path),
                start_line=8,
                end_line=8,
            )
        ],
        analysis_message=[
            AnalysisMessages(
                sink_detection="test sink",
                vulnerability_classification="test vuln",
                sanitizer_type="test sanitizer",
                key_conditions_report="",
            )
        ],
        analyzed_functions=[],
    )

    attr_cg = AttributeCG.from_cg(sample_cg, None, bit=bit)

    # Bug should be in main function
    assert attr_cg.root_node.bit_info is not None
    assert len(attr_cg.root_node.key_conditions) == 0
    assert len(attr_cg.root_node.should_be_taken_lines) == 0

    # Key conditions and should be taken lines should be in helper function
    helper_node = attr_cg.root_node.children[0]
    assert helper_node.bit_info is None
    assert len(helper_node.key_conditions) == 1
    assert helper_node.key_conditions[0].start_line == 8
    assert helper_node.key_conditions[0].end_line == 8
    assert len(helper_node.should_be_taken_lines) == 1
    assert helper_node.should_be_taken_lines[0].start_line == 8
    assert helper_node.should_be_taken_lines[0].end_line == 8

    # Check annotations in function bodies
    options = AnnotationOptions(
        show_coverage=False,
        show_bug_location=True,
        show_key_conditions=True,
        show_should_be_taken_lines=True,
        show_metadata=True,
    )
    bodies = attr_cg.get_annotated_function_bodies(options)
    bodies = bodies.replace("<SOURCE>\n", "").replace("</SOURCE>\n", "")
    bodies = split_function_bodies(bodies)
    main_body = bodies[0]
    helper_body = bodies[1]

    # Main function should have bug but no other annotations
    assert "@BUG_HERE" in main_body
    assert "@KEY_CONDITION" not in main_body
    assert "@SHOULD_BE_TAKEN" not in main_body

    # Helper function should have key conditions and should be taken lines but no bug
    assert "@BUG_HERE" not in helper_body
    assert "@KEY_CONDITION" in helper_body
    assert "@SHOULD_BE_TAKEN" in helper_body


def test_should_be_taken_lines_different_function(sample_cg):
    # Create a BIT where should_be_taken_lines are in the helper function
    bit = BugInducingThing(
        harness_name="test",
        func_location=LocationInfo(
            func_name="void TestAttributeCG.testMain()",
            file_path=str(sample_cg.path),
            start_line=2,
            end_line=4,
        ),
        key_conditions=[],
        should_be_taken_lines=[
            LocationInfo(
                func_name="void TestAttributeCG.testHelper()",
                file_path=str(sample_cg.path),
                start_line=8,
                end_line=8,
            )
        ],
        analysis_message=[
            AnalysisMessages(
                sink_detection="test sink",
                vulnerability_classification="test vuln",
                sanitizer_type="test sanitizer",
                key_conditions_report="",
            )
        ],
        analyzed_functions=[],
    )

    attr_cg = AttributeCG.from_cg(sample_cg, None, bit=bit)

    # Bug should be in main function
    assert attr_cg.root_node.bit_info is not None
    assert len(attr_cg.root_node.should_be_taken_lines) == 0

    # Should be taken lines should be in helper function
    helper_node = attr_cg.root_node.children[0]
    assert helper_node.bit_info is None
    assert len(helper_node.should_be_taken_lines) == 1
    assert helper_node.should_be_taken_lines[0].start_line == 8
    assert helper_node.should_be_taken_lines[0].end_line == 8

    # Check annotations in function bodies
    options = AnnotationOptions(
        show_coverage=False,
        show_bug_location=True,
        show_key_conditions=True,
        show_should_be_taken_lines=True,
        show_metadata=True,
    )
    bodies = attr_cg.get_annotated_function_bodies(options)
    bodies = bodies.replace("<SOURCE>\n", "").replace("</SOURCE>\n", "")
    bodies = split_function_bodies(bodies)
    main_body = bodies[0]
    helper_body = bodies[1]

    # Main function should have bug but no should be taken lines
    assert "@BUG_HERE" in main_body
    assert "@SHOULD_BE_TAKEN" not in main_body

    # Helper function should have should be taken lines but no bug
    assert "@BUG_HERE" not in helper_body
    assert "@SHOULD_BE_TAKEN" in helper_body


def test_from_leaf_option(sample_cg, sample_coverage_info, sample_bits):
    # Test with from_leaf option
    cg = sample_cg
    attr_cg = AttributeCG.from_cg(
        cg, None, coverage_info=sample_coverage_info, bit=sample_bits[0]
    )

    options = AnnotationOptions(
        show_coverage=True,
        show_bug_location=True,
        show_key_conditions=True,
        show_should_be_taken_lines=True,
        show_metadata=True,
        from_leaf=True,
    )
    bodies = attr_cg.get_annotated_function_bodies(options)
    bodies = bodies.replace("<SOURCE>\n", "").replace("</SOURCE>\n", "")
    bodies = split_function_bodies(bodies)

    # Get main and helper function bodies
    helper_body = next(body for body in bodies if "testHelper" in body)
    main_body = next(body for body in bodies if "testMain" in body)

    # Check helper function
    assert "@VISITED" in helper_body
    assert "@BUG_HERE" not in helper_body
    assert "@KEY_CONDITION" not in helper_body
    assert "<FUNCTION>" in helper_body

    # Check main function
    assert "@VISITED" in main_body
    assert "@BUG_HERE" in main_body
    assert "@KEY_CONDITION" in main_body
    assert any(
        tag in main_body for tag in ["<VULNERABLE_FUNCTION>", "<ENTRY_FUNCTION>"]
    )


def test_get_call_flow(sample_cg, sample_bits):
    # Test with bug info
    cg = sample_cg
    attr_cg = AttributeCG.from_cg(cg, None, bit=sample_bits[0])

    # Get call flow
    call_flow = attr_cg.get_call_flow()

    # Check call flow format
    assert "<FUNCTION_CALL_FLOW>" in call_flow
    assert "↳ void TestAttributeCG.testMain" in call_flow
    assert "</FUNCTION_CALL_FLOW>" in call_flow

    # Only buggy function should be included
    assert "  ↳ void TestAttributeCG.testHelper" in call_flow
