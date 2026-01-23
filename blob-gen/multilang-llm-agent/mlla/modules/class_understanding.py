from loguru import logger

from ..codeindexer.tree_sitter_languages import get_parser
from ..utils.cg import CG, FuncInfo

# fmt: off
# Define primitive types at module level for better performance
JAVA_PRIMITIVES = frozenset(
    {"int", "long", "boolean", "float", "double", "byte", "short", "char", "void"}
)

C_PRIMITIVES = frozenset({
    "int", "float", "char", "double", "void", "long", "short", "unsigned", "signed",
    "bool", "_Bool", "size_t", "ptrdiff_t", "wchar_t",
    "int8_t", "int16_t", "int32_t", "int64_t",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t"
})

JAVA_STANDARD_CLASSES = frozenset({
    # Core types
    "String", "Object", "Class", "System", "Math", "Arrays", "Collections",

    # Wrappers
    "Integer", "Long", "Boolean", "Float", "Double", "Character", "Byte", "Short",

    # Collections
    "List", "ArrayList", "LinkedList", "Vector",
    "Set", "HashSet", "TreeSet", "LinkedHashSet",
    "Map", "HashMap", "TreeMap", "LinkedHashMap", "Hashtable",
    "Queue", "Deque", "ArrayDeque", "PriorityQueue",

    # Common utilities
    "StringBuilder", "StringBuffer", "ByteBuffer", "Date", "Calendar",
    "File", "Path", "Paths", "Files",
    "Thread", "Runnable", "Callable", "Future",

    # Exceptions (usually not interesting for external method analysis)
    "Exception", "RuntimeException", "Error", "Throwable"
})

JAVA_STANDARD_PACKAGES = frozenset({
    "java.lang.", "java.util.", "java.io.", "java.time.", "java.math.",
    "java.text.", "java.util.concurrent.", "java.util.stream."
})
# fmt: on


def is_interesting_java_class(class_name: str) -> bool:
    """Filter out Java standard library classes, keep custom/third-party classes"""
    # Skip standard library classes
    if class_name in JAVA_STANDARD_CLASSES:
        return False

    # Check package prefixes
    for pkg in JAVA_STANDARD_PACKAGES:
        if class_name.startswith(pkg):
            return False

    # Keep everything else (third-party, custom classes)
    return True


def extract_used_classes(cg: CG, language: str) -> list[str]:
    if language == "jvm":
        parser = get_parser("java")
        primitives = JAVA_PRIMITIVES
    elif language == "c":
        parser = get_parser("c")
        primitives = C_PRIMITIVES
    else:
        logger.warning(f"extract_used_classes: language '{language}' not supported")
        return []

    def _traverse(fi: FuncInfo):
        if not (
            fi and fi.func_body and fi.func_location and fi.func_location.file_path
        ):
            return []

        func_code = fi.func_body.encode()

        tree = parser.parse(func_code)
        root_node = tree.root_node

        referenced_classes = set()

        def walk(node):
            if node.type == "type_identifier":
                text = func_code[node.start_byte : node.end_byte].decode()
                if text not in primitives:
                    referenced_classes.add(text)
            elif node.type == "scoped_type_identifier":
                parts = []
                for child in node.children:
                    if child.type == "identifier":
                        parts.append(
                            func_code[child.start_byte : child.end_byte].decode()
                        )
                if parts:
                    referenced_classes.add(".".join(parts))

            for child in node.children:
                walk(child)

        walk(root_node)

        for node in fi.children:
            referenced_classes.update(_traverse(node))

        return referenced_classes

    ret = _traverse(cg.root_node)

    return list(ret)


def extract_external_methods(
    cg: CG,
    called_functions: list[FuncInfo],
    language: str,
):
    if not cg:
        return []

    if language != "jvm":  # and language != "c":
        logger.warning("extract_used_classes: language not supported (only jvm)")
        return []

    try:
        used_classes = extract_used_classes(cg, language)

        # Add filtering for JVM to remove standard library classes
        if language == "jvm":
            used_classes = [
                class_name
                for class_name in used_classes
                if is_interesting_java_class(class_name)
            ]

        used_class_methods = []

        for f in called_functions:
            for c in used_classes:
                if c in f.func_location.func_name:
                    used_class_methods.append(f)

        return used_class_methods

    except Exception as e:
        logger.error(f"Error in extract_external_methods: {e}")
        return []
