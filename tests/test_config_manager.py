from __future__ import annotations

from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).parents[1]


def load_config_manager_class():
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_user_directory = lambda: None
    sys.modules.setdefault("folder_paths", folder_paths)

    source = (ROOT / "config_manager.py").read_text(encoding="utf-8")
    source = source.rsplit("# 创建全局配置管理器实例", 1)[0]
    namespace = {"__file__": str(ROOT / "config_manager.py")}
    exec(compile(source, namespace["__file__"], "exec"), namespace)
    return namespace["ConfigManager"]


class ConfigManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config_manager_class = load_config_manager_class()

    def test_selecting_google_keeps_google_as_current_translate_service(self):
        manager = object.__new__(self.config_manager_class)
        config = {
            "version": "2.0",
            "model_services": [],
            "current_services": {},
        }
        saved = []
        manager.load_config = lambda: config
        manager._is_v2_config = lambda value: True
        manager.save_config = lambda value: saved.append(value) or True
        manager._log = lambda message: None

        result = manager.set_current_service("translate", "google")

        self.assertTrue(result)
        self.assertEqual(
            saved[0]["current_services"]["translate"],
            {"service": "google", "model": ""},
        )

    def test_selecting_google_web_keeps_keyless_provider(self):
        manager = object.__new__(self.config_manager_class)
        config = {
            "version": "2.0",
            "model_services": [],
            "current_services": {},
        }
        saved = []
        manager.load_config = lambda: config
        manager._is_v2_config = lambda value: True
        manager.save_config = lambda value: saved.append(value) or True
        manager._log = lambda message: None

        result = manager.set_current_service("translate", "google_web")

        self.assertTrue(result)
        self.assertEqual(
            saved[0]["current_services"]["translate"],
            {"service": "google_web", "model": ""},
        )

    def test_minimax_h3_preset_loads_external_rule_source(self):
        manager = object.__new__(self.config_manager_class)
        manager.templates_dir = str(ROOT / "config")
        manager._template_versions = {}
        manager._log = lambda message: None

        data = manager._load_template("system_prompts", {})
        preset = data["fusion_prompts"]["fusion_minimax_h3"]

        self.assertNotIn("content_file", preset)
        self.assertIn("MiniMax-H3 视频提示词优化规则（最终版）", preset["content"])
        self.assertIn("禁止在切换运镜/剪切之后复读", preset["content"])
        self.assertIn("全片锁定主体运动方向与朝向", preset["content"])


if __name__ == "__main__":
    unittest.main()
