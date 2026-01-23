import os
import shutil
import unittest
from pathlib import Path
from typing import Dict, List

import clang.cindex

clang.cindex.Config.set_library_file("/usr/lib/llvm-18/lib/libclang.so")

from symbolizer.utils import (
    get_new_file_path,
    is_running_under_pytest,
    map_lines_to_functions,
)


class TestUtils(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(__file__).parent.as_posix()
        self.test_directory_root = os.path.join(self.test_dir, "renamed")
        self.directory_structure = [
            "module_a/submodule_a/source.c",
            "module_b/submodule_b/source.c",
            "module_c/source.c",
            "source.c",
            "README.md",
        ]
        self.test_cases: List[Dict[str, str]] = []
        for file in self.directory_structure:
            for prefix in ["/src/project", "/src", "/src/hello/world"]:
                self.test_cases.append(
                    {
                        "old_file_path": os.path.join(prefix, file),
                        "expected": os.path.join(self.test_directory_root, file),
                    }
                )
        self.create_test_files()

    def create_test_files(self):
        for relative_path in self.directory_structure:
            file_path = os.path.join(self.test_directory_root, relative_path)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            with open(file_path, "w") as f:
                f.write("")

    def tearDown(self):
        shutil.rmtree(self.test_directory_root)

    def test_get_new_file_path(self):
        for test_case in self.test_cases:
            result = get_new_file_path(
                test_case["old_file_path"], self.test_directory_root
            )
            self.assertEqual(result, test_case["expected"])

    def test_is_running_under_pytest(self):
        self.assertTrue(is_running_under_pytest())

    def test_map_lines_to_functions(self):
        test_file = os.path.join(self.test_dir, "test_cases", "test_sample.cpp")
        results = map_lines_to_functions(test_file)

        self.assertEqual(18, len(results))

        answers = [
            ((1, 2, 3), "global_function"),
            ((6, 7, 8), "namespaced_function"),
            ((13,), "TestClass::TestClass"),
            ((14,), "TestClass::~~TestClass"),
            ((16, 17, 18), "TestClass::method"),
            ((20, 21, 22), "TestClass::static_method"),
            ((25, 26, 27, 28), "template_function"),
        ]

        for line_nums, func_name in answers:
            for line_num in line_nums:
                self.assertEqual(func_name, results[line_num])


if __name__ == "__main__":
    unittest.main()
