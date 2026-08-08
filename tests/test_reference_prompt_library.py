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
        "_format_image_role_directives",
        "_format_user_intent_block",
        "_build_prompt",
        "_build_h3_prompt",
        "_sanitize_fusion_prompt",
        "_sanitize_storyboard_prompt",
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
        "H3_T2V_RULE": "BASE H3 T2V RULE",
        "STORYBOARD_OUTPUT_STYLE": "Storyboard Images",
        "List": list,
        "re": __import__("re"),
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
            rule_content="SELECTED H3 RULE",
            fusion_description="USER INTENT",
            image_count=1,
            video_count=0,
            audio_count=0,
            payload_labels=["<Picture 1>: payload image 1"],
            additional_rule="ADDITIONAL RULE",
            reference_prompt_content="SELECTED DETAIL",
        )
        self.assertIn("SELECTED H3 RULE", prompt)
        self.assertIn("BASE H3 RULE", prompt)
        self.assertIn("ADDITIONAL RULE", prompt)
        self.assertIn("SELECTED DETAIL", prompt)
        self.assertIn("later file has higher priority", prompt)

    def test_h3_without_media_builds_text_to_video_prompt(self):
        prompt = self.builder._build_h3_prompt(
            rule_content="SELECTED H3 RULE",
            fusion_description="USER T2V INTENT",
            image_count=0,
            video_count=0,
            audio_count=0,
            payload_labels=[],
            reference_prompt_content="SELECTED DETAIL",
        )
        self.assertIn("BASE H3 T2V RULE", prompt)
        self.assertIn("SELECTED H3 RULE", prompt)
        self.assertIn("USER T2V INTENT", prompt)
        self.assertIn("SELECTED DETAIL", prompt)
        self.assertNotIn("BASE H3 RULE", prompt)

    def test_storyboard_prompt_uses_independent_next_scene_contract(self):
        prompt = self.builder._build_prompt(
            rule_content="BASE SINGLE IMAGE RULE",
            fusion_description="Create a spring portrait sequence",
            image_count=0,
            output_style="Storyboard Images",
            reference_prompt_content="Keep the same hairstyle and white outfit",
        )
        self.assertIn("Next Scene:", prompt)
        self.assertIn("除非用户明确指定数量，否则输出5个分镜", prompt)
        self.assertIn("每个分镜都是一条完整、自包含", prompt)
        self.assertIn("完整重述主体身份", prompt)
        self.assertIn("优先于上方规则中任何‘单张图’", prompt)
        self.assertIn("`Next Scene: 正文`", prompt)
        self.assertIn("与正文之间绝对禁止换行", prompt)
        self.assertNotIn("最终正文必须是流畅完整的单画面描述", prompt)

    def test_storyboard_prompt_promotes_cross_image_element_assignments(self):
        prompt = self.builder._build_prompt(
            rule_content="BASE RULE",
            fusion_description=(
                "生成3张分镜\n"
                "人物描写: 识别图1中的人物描写和装扮\n"
                "环境: 识别图2中的环境描写\n"
                "景别: 随机"
            ),
            image_count=2,
            output_style="Storyboard Images",
        )
        self.assertIn("用户指定的参考图元素分工 - 最高优先级", prompt)
        self.assertIn("人物描写: 识别图1中的人物描写和装扮", prompt)
        self.assertIn("环境: 识别图2中的环境描写", prompt)
        self.assertNotIn("- 生成3张分镜\n", prompt)
        self.assertIn("禁止按输入图片轮流复刻整张图", prompt)
        self.assertIn("必须排除图1环境以及图2人物和装扮", prompt)
        self.assertIn("动作默认只改变姿态与肢体关系", prompt)
        self.assertIn("‘随机’只允许改变用户标为随机的维度", prompt)

    def test_standard_prompt_supports_zero_image_text_input(self):
        prompt = self.builder._build_prompt(
            rule_content="BASE RULE",
            fusion_description="TEXT ONLY STORY",
            image_count=0,
            output_style="Natural Language",
        )
        self.assertIn("共 0 张", prompt)
        self.assertIn("TEXT ONLY STORY", prompt)

    def test_sanitizer_preserves_storyboard_line_breaks(self):
        result = self.builder._sanitize_fusion_prompt(
            "Next Scene: first  scene\n\nNext Scene: second  scene"
        )
        self.assertEqual(
            result,
            "Next Scene: first scene\n\nNext Scene: second scene",
        )

    def test_storyboard_sanitizer_joins_marker_and_body_on_same_line(self):
        result = self.builder._sanitize_storyboard_prompt(
            "Intro that must be removed\n\n"
            "Next Scene:\n\nfirst scene\nwith more detail\n\n"
            "Next Scene：\nsecond scene"
        )
        self.assertEqual(
            result,
            "Next Scene: first scene with more detail\n\nNext Scene: second scene",
        )
        for line in result.splitlines():
            if line:
                self.assertRegex(line, r"^Next Scene: \S")


class ReferencePromptNodeContractTests(unittest.TestCase):
    def test_reference_content_uses_dependency_only_connection_type(self):
        library_source = (
            PROJECT_ROOT / "node" / "reference_prompt_library_node.py"
        ).read_text(encoding="utf-8")
        fusion_source = (
            PROJECT_ROOT / "node" / "multi_image_fusion_node.py"
        ).read_text(encoding="utf-8")
        io_types_source = (PROJECT_ROOT / "node" / "io_types.py").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'io.Custom("PROMPT_ASSISTANT_REFERENCE_PROMPT")', io_types_source
        )
        self.assertIn(
            'ReferencePromptContent.Output("reference_content")', library_source
        )
        self.assertIn("ReferencePromptContent.Input(", fusion_source)
        self.assertIn('"reference_prompt_content"', fusion_source)
        self.assertNotIn('io.String.Output("reference_content")', library_source)

    def test_storyboard_style_allows_text_only_and_uses_larger_output_budget(self):
        fusion_source = (
            PROJECT_ROOT / "node" / "multi_image_fusion_node.py"
        ).read_text(encoding="utf-8")
        self.assertIn('STORYBOARD_OUTPUT_STYLE = "Storyboard Images"', fusion_source)
        self.assertNotIn("Multi-image fusion needs at least 2 images.", fusion_source)
        self.assertIn("5000 if is_storyboard", fusion_source)
        self.assertIn("images_data = []", fusion_source)

    def test_vision_payload_does_not_force_one_scene_or_one_output_per_image(self):
        service_source = (PROJECT_ROOT / "services" / "vlm.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "Do not assume that each reference image maps to a separate output",
            service_source,
        )
        self.assertNotIn(
            "Use these labels when combining them into one final scene/prompt",
            service_source,
        )


if __name__ == "__main__":
    unittest.main()
