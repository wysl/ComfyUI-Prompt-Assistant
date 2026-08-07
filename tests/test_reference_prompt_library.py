from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).parents[1]
MODULE_PATH = PROJECT_ROOT / "utils" / "reference_prompt_library.py"
SPEC = importlib.util.spec_from_file_location("reference_prompt_library", MODULE_PATH)
reference_prompt_library = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = reference_prompt_library
SPEC.loader.exec_module(reference_prompt_library)


def load_prompt_builder_class():
    source_path = PROJECT_ROOT / "node" / "multi_image_fusion_node.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    source_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MultiImageFusionNode"
    )
    method_names = {
        "_format_reference_prompt_block",
        "_format_user_intent_block",
        "_build_prompt",
        "_build_h3_prompt",
    }
    methods = [
        node
        for node in source_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in method_names
    ]
    test_class = ast.ClassDef(
        name="PromptBuilder",
        bases=[],
        keywords=[],
        body=methods,
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[test_class], type_ignores=[]))
    namespace = {
        "H3_REF2VA_RULE": "BASE H3 RULE",
        "List": list,
    }
    exec(compile(module, str(source_path), "exec"), namespace)
    return namespace["PromptBuilder"]


class ReferencePromptLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_nested_listing_returns_only_immediate_children(self):
        (self.root / "A" / "nested").mkdir(parents=True)
        (self.root / "B").mkdir()
        (self.root / "A" / "one.txt").write_text("one", encoding="utf-8")
        (self.root / "A" / "ignore.md").write_text("ignore", encoding="utf-8")
        (self.root / "A" / "nested" / "deep.txt").write_text("deep", encoding="utf-8")

        root_listing = reference_prompt_library.list_reference_directory("", root=self.root)
        self.assertEqual([item["path"] for item in root_listing["directories"]], ["A", "B"])

        listing = reference_prompt_library.list_reference_directory("A", root=self.root)
        self.assertEqual([item["path"] for item in listing["directories"]], ["A/nested"])
        self.assertEqual([item["path"] for item in listing["files"]], ["A/one.txt"])

    def test_rejects_traversal_and_absolute_paths(self):
        with self.assertRaises(reference_prompt_library.ReferencePromptError):
            reference_prompt_library.resolve_library_path("../outside.txt", root=self.root)
        with self.assertRaises(reference_prompt_library.ReferencePromptError):
            reference_prompt_library.resolve_library_path("C:/outside.txt", root=self.root)

    def test_ordered_merge_supports_utf8_bom_and_gb18030(self):
        (self.root / "A").mkdir()
        (self.root / "B").mkdir()
        (self.root / "A" / "one.txt").write_bytes("第一条".encode("utf-8-sig"))
        (self.root / "B" / "two.txt").write_bytes("第二条".encode("gb18030"))

        content, manifest = reference_prompt_library.compose_reference_prompts(
            '["A/one.txt", "B/two.txt"]', root=self.root
        )

        self.assertLess(content.index("第一条"), content.index("第二条"))
        self.assertIn("[Reference file 1: A/one.txt]", content)
        self.assertIn("[Reference file 2: B/two.txt]", content)
        self.assertEqual(manifest, '[\n  "A/one.txt",\n  "B/two.txt"\n]')

    def test_digest_changes_when_selected_file_content_changes(self):
        path = self.root / "prompt.txt"
        path.write_text("before", encoding="utf-8")
        before = reference_prompt_library.selection_content_digest(
            '["prompt.txt"]', root=self.root
        )
        path.write_text("after", encoding="utf-8")
        after = reference_prompt_library.selection_content_digest(
            '["prompt.txt"]', root=self.root
        )
        self.assertNotEqual(before, after)


class ReferencePromptBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = load_prompt_builder_class()

    def test_standard_prompt_keeps_base_rule_and_adds_reference(self):
        prompt = self.builder._build_prompt(
            rule_content="BASE RULE",
            fusion_description="USER INTENT",
            image_count=2,
            output_style="Auto",
            reference_prompt_content="SELECTED DETAIL",
        )
        self.assertIn("BASE RULE", prompt)
        self.assertIn("USER INTENT", prompt)
        self.assertIn("SELECTED DETAIL", prompt)
        self.assertIn("Explicit user requirements take priority", prompt)

    def test_h3_prompt_keeps_h3_rule_and_adds_reference(self):
        prompt = self.builder._build_h3_prompt(
            fusion_description="USER INTENT",
            image_count=1,
            video_count=0,
            audio_count=0,
            payload_labels=["<Picture 1>: payload image 1"],
            additional_rule="ADDITIONAL RULE",
            reference_prompt_content="SELECTED DETAIL",
        )
        self.assertIn("BASE H3 RULE", prompt)
        self.assertIn("ADDITIONAL RULE", prompt)
        self.assertIn("SELECTED DETAIL", prompt)
        self.assertIn("later file has higher priority", prompt)


if __name__ == "__main__":
    unittest.main()
