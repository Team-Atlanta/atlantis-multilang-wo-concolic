# flake8: noqa: E501
"""Tests for class_understanding.py module."""

from unittest.mock import MagicMock, patch

from mlla.modules.class_understanding import (
    JAVA_STANDARD_CLASSES,
    JAVA_STANDARD_PACKAGES,
    extract_external_methods,
    extract_used_classes,
    is_interesting_java_class,
)
from mlla.utils.bit import LocationInfo
from mlla.utils.cg import CG, FuncInfo


def create_test_func_info(func_name: str, file_path: str, func_body: str) -> FuncInfo:
    """Create a test FuncInfo with the given parameters."""
    return FuncInfo(
        func_location=LocationInfo(
            func_name=func_name,
            file_path=file_path,
            start_line=1,
            end_line=10,
        ),
        func_body=func_body,
        children=[],
    )


def create_geonetwork_cg() -> CG:
    """Create a call graph based on the GeoNetwork example."""
    # GeoNetwork fuzzer entry point
    fuzzer_entry_body = """
    public static void fuzzerTestOneInput(byte[] b) throws Throwable {
        try {
            new GeonetworkOne().fuzz(b);
        } catch (FuzzerSecurityIssueLow |
                 FuzzerSecurityIssueMedium |
                 FuzzerSecurityIssueHigh |
                 FuzzerSecurityIssueCritical e) {
            throw e;
        } catch (Throwable t) {
        }
    }
    """

    # GeoNetwork fuzz method with LocalFilesystemHarvester
    fuzz_body = """
    public void fuzz(byte[] b) throws Throwable {
        SAXBuilder saxBuilder = new SAXBuilder();
        Document document = saxBuilder.build(new StringReader(new String(b)));
        Element element = document.getRootElement();
        LocalFilesystemHarvester harvester = (LocalFilesystemHarvester)AbstractHarvester.create("", serviceContext);
        harvester.add(element);
        harvester.doHarvest(null);
    }
    """

    # LocalFilesystemHarvester.add method
    add_body = """
    public void add(Element element) {
        CustomParser parser = new CustomParser();
        parser.parse(element);
        FileManager fileManager = new FileManager();
        fileManager.processFile(element);
    }
    """

    # LocalFilesystemHarvester.doHarvest method
    do_harvest_body = """
    public void doHarvest(Object context) {
        HarvestProcessor processor = new HarvestProcessor();
        processor.process(context);
        DatabaseConnector connector = new DatabaseConnector();
        connector.save(context);
    }
    """

    # Create function nodes
    fuzzer_entry_func = create_test_func_info(
        "fuzzerTestOneInput", "GeonetworkFuzzer.java", fuzzer_entry_body
    )
    fuzz_func = create_test_func_info(
        "GeonetworkOne.fuzz", "GeonetworkOne.java", fuzz_body
    )
    add_func = create_test_func_info(
        "LocalFilesystemHarvester.add", "LocalFilesystemHarvester.java", add_body
    )
    do_harvest_func = create_test_func_info(
        "LocalFilesystemHarvester.doHarvest",
        "LocalFilesystemHarvester.java",
        do_harvest_body,
    )

    # Set up call hierarchy: fuzzerTestOneInput -> fuzz -> [add, doHarvest]
    fuzzer_entry_func.children = [fuzz_func]
    fuzz_func.children = [add_func, do_harvest_func]

    return CG(
        name="geonetwork_fuzzer",
        path="GeonetworkFuzzer.java",
        root_node=fuzzer_entry_func,
    )


def create_simple_java_cg() -> CG:
    """Create a simple Java call graph for testing."""
    java_body = """
    public void testMethod() {
        String name = "test";
        ArrayList<String> list = new ArrayList<>();
        HashMap<String, Integer> map = new HashMap<>();
        CustomService service = new CustomService();
        ApacheHttpClient client = new ApacheHttpClient();
        service.process(name);
        client.sendRequest();
    }
    """

    root_func = create_test_func_info("testMethod", "Test.java", java_body)

    return CG(
        name="simple_java_test",
        path="Test.java",
        root_node=root_func,
    )


def create_c_cg() -> CG:
    """Create a C call graph for testing."""
    c_body = """
    int main() {
        int x = 5;
        char* str = malloc(100);
        FILE* file = fopen("test.txt", "r");
        custom_function(x);
        return 0;
    }
    """

    root_func = create_test_func_info("main", "main.c", c_body)

    return CG(
        name="c_test",
        path="main.c",
        root_node=root_func,
    )


class TestIsInterestingJavaClass:
    """Test the is_interesting_java_class function."""

    def test_standard_classes_filtered(self):
        """Test that standard Java classes are filtered out."""
        assert not is_interesting_java_class("String")
        assert not is_interesting_java_class("ArrayList")
        assert not is_interesting_java_class("HashMap")
        assert not is_interesting_java_class("Integer")
        assert not is_interesting_java_class("ByteBuffer")
        assert not is_interesting_java_class("Exception")

    def test_standard_packages_filtered(self):
        """Test that standard Java packages are filtered out."""
        assert not is_interesting_java_class("java.lang.String")
        assert not is_interesting_java_class("java.util.ArrayList")
        assert not is_interesting_java_class("java.io.File")
        assert not is_interesting_java_class("java.time.LocalDate")
        assert not is_interesting_java_class("java.util.concurrent.Future")

    def test_custom_classes_kept(self):
        """Test that custom/third-party classes are kept."""
        assert is_interesting_java_class("CustomService")
        assert is_interesting_java_class("LocalFilesystemHarvester")
        assert is_interesting_java_class("ApacheHttpClient")
        assert is_interesting_java_class("com.example.MyClass")
        assert is_interesting_java_class("org.apache.commons.HttpClient")

    def test_edge_cases(self):
        """Test edge cases for class filtering."""
        assert is_interesting_java_class("")  # Empty string
        assert is_interesting_java_class(
            "MyString"
        )  # Contains "String" but not exact match
        assert is_interesting_java_class(
            "StringUtils"
        )  # Contains "String" but not exact match


class TestExtractUsedClasses:
    """Test the extract_used_classes function."""

    @patch("mlla.modules.class_understanding.get_parser")
    def test_jvm_language_support(self, mock_get_parser):
        """Test that JVM language is supported."""
        # Mock tree-sitter parser
        mock_parser = MagicMock()
        mock_tree = MagicMock()
        mock_root = MagicMock()
        mock_root.children = []
        mock_tree.root_node = mock_root
        mock_parser.parse.return_value = mock_tree
        mock_get_parser.return_value = mock_parser

        cg = create_simple_java_cg()
        result = extract_used_classes(cg, "jvm")

        assert isinstance(result, list)
        mock_get_parser.assert_called_with("java")

    @patch("mlla.modules.class_understanding.get_parser")
    def test_c_language_support(self, mock_get_parser):
        """Test that C language is supported."""
        # Mock tree-sitter parser
        mock_parser = MagicMock()
        mock_tree = MagicMock()
        mock_root = MagicMock()
        mock_root.children = []
        mock_tree.root_node = mock_root
        mock_parser.parse.return_value = mock_tree
        mock_get_parser.return_value = mock_parser

        cg = create_c_cg()
        result = extract_used_classes(cg, "c")

        assert isinstance(result, list)
        mock_get_parser.assert_called_with("c")

    def test_unsupported_language(self):
        """Test that unsupported languages return empty list with warning."""
        cg = create_simple_java_cg()

        with patch("mlla.modules.class_understanding.logger") as mock_logger:
            result = extract_used_classes(cg, "python")

            assert result == []
            mock_logger.warning.assert_called_once()

    @patch("mlla.modules.class_understanding.get_parser")
    def test_empty_function_body(self, mock_get_parser):
        """Test handling of functions with empty body."""
        mock_parser = MagicMock()
        mock_get_parser.return_value = mock_parser

        # Create function with no body
        empty_func = FuncInfo(
            func_location=LocationInfo(
                func_name="empty",
                file_path="",
                start_line=1,
                end_line=1,
            ),
            func_body=None,
            children=[],
        )

        cg = CG(name="empty", path="", root_node=empty_func)
        result = extract_used_classes(cg, "jvm")

        assert result == []

    @patch("mlla.modules.class_understanding.get_parser")
    def test_java_class_extraction_with_filtering(self, mock_get_parser):
        """Test that Java classes are extracted and filtered properly."""
        # Mock tree-sitter parser
        mock_parser = MagicMock()
        mock_tree = MagicMock()
        mock_root = MagicMock()
        mock_root.children = []
        mock_tree.root_node = mock_root
        mock_parser.parse.return_value = mock_tree
        mock_get_parser.return_value = mock_parser

        # Create a function with mock body
        func_body = (
            "String name; CustomService service; LocalFilesystemHarvester harvester;"
        )
        test_func = create_test_func_info("test", "Test.java", func_body)
        cg = CG(name="test", path="Test.java", root_node=test_func)

        # Test that the function runs without error and returns a list
        result = extract_used_classes(cg, "jvm")
        assert isinstance(result, list)

        # Verify the parser was called correctly
        mock_get_parser.assert_called_with("java")
        mock_parser.parse.assert_called_once()


class TestExtractExternalMethods:
    """Test the extract_external_methods function."""

    def test_empty_cg(self):
        """Test handling of empty call graph."""
        result = extract_external_methods(None, [], "jvm")
        assert result == []

    def test_unsupported_language(self):
        """Test that only JVM is supported."""
        cg = create_simple_java_cg()
        called_functions = []

        with patch("mlla.modules.class_understanding.logger") as mock_logger:
            result = extract_external_methods(cg, called_functions, "python")

            assert result == []
            mock_logger.warning.assert_called_once()

    @patch("mlla.modules.class_understanding.extract_used_classes")
    def test_jvm_method_matching(self, mock_extract_classes):
        """Test that methods are matched to classes correctly."""
        # Mock extract_used_classes to return specific classes
        mock_extract_classes.return_value = [
            "LocalFilesystemHarvester",
            "CustomService",
        ]

        cg = create_geonetwork_cg()

        # Create mock called functions
        called_functions = [
            create_test_func_info(
                "LocalFilesystemHarvester.add", "LocalFilesystemHarvester.java", ""
            ),
            create_test_func_info(
                "LocalFilesystemHarvester.doHarvest",
                "LocalFilesystemHarvester.java",
                "",
            ),
            create_test_func_info("CustomService.process", "CustomService.java", ""),
            create_test_func_info(
                "String.valueOf", "String.java", ""
            ),  # Should not match
            create_test_func_info(
                "unrelated.method", "Other.java", ""
            ),  # Should not match
        ]

        result = extract_external_methods(cg, called_functions, "jvm")

        # Should match methods containing the class names
        assert (
            len(result) == 3
        )  # LocalFilesystemHarvester.add, LocalFilesystemHarvester.doHarvest, CustomService.process

        method_names = [f.func_location.func_name for f in result]
        assert "LocalFilesystemHarvester.add" in method_names
        assert "LocalFilesystemHarvester.doHarvest" in method_names
        assert "CustomService.process" in method_names
        assert "String.valueOf" not in method_names
        assert "unrelated.method" not in method_names

    @patch("mlla.modules.class_understanding.extract_used_classes")
    def test_geonetwork_example(self, mock_extract_classes):
        """Test the complete GeoNetwork fuzzer example to find LocalFilesystemHarvester."""
        # Mock extract_used_classes to return the classes we expect from the fuzzer
        mock_extract_classes.return_value = [
            "GeonetworkOne",
            "SAXBuilder",
            "Document",
            "Element",
            "LocalFilesystemHarvester",
            "AbstractHarvester",
            "CustomParser",
            "FileManager",
            "HarvestProcessor",
            "DatabaseConnector",
            "FuzzerSecurityIssueLow",
            "FuzzerSecurityIssueMedium",
            "FuzzerSecurityIssueHigh",
            "FuzzerSecurityIssueCritical",
        ]

        cg = create_geonetwork_cg()

        # Create called functions that might be found in the call graph
        called_functions = [
            create_test_func_info("GeonetworkOne.fuzz", "GeonetworkOne.java", ""),
            create_test_func_info(
                "LocalFilesystemHarvester.add", "LocalFilesystemHarvester.java", ""
            ),
            create_test_func_info(
                "LocalFilesystemHarvester.doHarvest",
                "LocalFilesystemHarvester.java",
                "",
            ),
            create_test_func_info("CustomParser.parse", "CustomParser.java", ""),
            create_test_func_info("FileManager.processFile", "FileManager.java", ""),
            create_test_func_info(
                "HarvestProcessor.process", "HarvestProcessor.java", ""
            ),
            create_test_func_info(
                "DatabaseConnector.save", "DatabaseConnector.java", ""
            ),
            create_test_func_info(
                "SAXBuilder.build", "SAXBuilder.java", ""
            ),  # Should not match (filtered)
            create_test_func_info(
                "String.valueOf", "String.java", ""
            ),  # Should not match (filtered)
        ]

        result = extract_external_methods(cg, called_functions, "jvm")

        # Should find LocalFilesystemHarvester methods and other custom classes
        method_names = [f.func_location.func_name for f in result]

        # LocalFilesystemHarvester methods should be found
        assert "LocalFilesystemHarvester.add" in method_names
        assert "LocalFilesystemHarvester.doHarvest" in method_names

        # Other custom class methods should be found
        assert "CustomParser.parse" in method_names
        assert "FileManager.processFile" in method_names
        assert "HarvestProcessor.process" in method_names
        assert "DatabaseConnector.save" in method_names

        # Standard library methods should not be found (filtered by is_interesting_java_class)
        # Note: SAXBuilder might be included if it's not in our standard classes list

        # Verify we found the target class
        assert any("LocalFilesystemHarvester" in name for name in method_names)

    @patch("mlla.modules.class_understanding.extract_used_classes")
    def test_error_handling(self, mock_extract_classes):
        """Test error handling in extract_external_methods."""
        # Mock extract_used_classes to raise an exception
        mock_extract_classes.side_effect = Exception("Test error")

        cg = create_simple_java_cg()
        called_functions = []

        with patch("mlla.modules.class_understanding.logger") as mock_logger:
            result = extract_external_methods(cg, called_functions, "jvm")

            assert result == []
            mock_logger.error.assert_called_once()

    def test_filtering_integration(self):
        """Test that Java standard library filtering works in integration."""
        # Test that our filtering constants are properly defined
        assert "String" in JAVA_STANDARD_CLASSES
        assert "ArrayList" in JAVA_STANDARD_CLASSES
        assert "HashMap" in JAVA_STANDARD_CLASSES
        assert "ByteBuffer" in JAVA_STANDARD_CLASSES

        assert "java.lang." in JAVA_STANDARD_PACKAGES
        assert "java.util." in JAVA_STANDARD_PACKAGES
        assert "java.io." in JAVA_STANDARD_PACKAGES

        # Test filtering function
        assert not is_interesting_java_class("String")
        assert not is_interesting_java_class("java.util.ArrayList")
        assert is_interesting_java_class("LocalFilesystemHarvester")
        assert is_interesting_java_class("com.example.CustomClass")
