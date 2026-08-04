from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).parents[1]
PACKAGE = "prompt_assistant_google_test"


class DummyProgressBar:
    def __init__(self, **kwargs):
        pass

    def cancel(self, message):
        pass

    def error(self, message):
        pass

    def done(self, **kwargs):
        pass


class DummyBaiduService:
    @staticmethod
    def split_text_by_paragraphs(text, max_length=4000):
        return [text]


class DummyHTTPClientPool:
    client = None

    @classmethod
    def get_client(cls, **kwargs):
        return cls.client


def load_google_module():
    for name in (PACKAGE, f"{PACKAGE}.services", f"{PACKAGE}.utils"):
        module = types.ModuleType(name)
        module.__path__ = []
        sys.modules[name] = module

    common = types.ModuleType(f"{PACKAGE}.utils.common")
    common.ERROR_PREFIX = "ERROR"
    common.ProgressBar = DummyProgressBar
    common.TASK_TRANSLATE = "translate"
    common.WARN_PREFIX = "WARN"
    sys.modules[common.__name__] = common

    baidu = types.ModuleType(f"{PACKAGE}.services.baidu")
    baidu.BaiduTranslateService = DummyBaiduService
    sys.modules[baidu.__name__] = baidu

    core = types.ModuleType(f"{PACKAGE}.services.core")
    core.HTTPClientPool = DummyHTTPClientPool
    sys.modules[core.__name__] = core

    path = ROOT / "services" / "google_translate.py"
    name = f"{PACKAGE}.services.google_translate"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class GoogleTranslateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_google_module()

    def set_config(self, api_key):
        config_module = types.ModuleType(f"{PACKAGE}.config_manager")
        config_module.config_manager = types.SimpleNamespace(
            get_google_translate_config=lambda: {"api_key": api_key}
        )
        sys.modules[config_module.__name__] = config_module

    def test_missing_api_key_returns_configuration_error(self):
        self.set_config("")
        result = asyncio.run(self.module.GoogleTranslateService.translate("hello"))
        self.assertFalse(result["success"])
        self.assertIn("API Key", result["error"])

    def test_translates_with_official_v2_payload_and_unescapes_text(self):
        self.set_config("test-key")

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"data": {"translations": [{"translatedText": "Tom &amp; Jerry"}]}}

        class Client:
            def __init__(self):
                self.calls = []

            async def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()

        client = Client()
        DummyHTTPClientPool.client = client
        result = asyncio.run(
            self.module.GoogleTranslateService.translate("汤姆和杰瑞", "auto", "en")
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["translated"], "Tom & Jerry")
        _, kwargs = client.calls[0]
        self.assertEqual(kwargs["params"], {"key": "test-key"})
        self.assertEqual(kwargs["json"]["target"], "en")
        self.assertNotIn("source", kwargs["json"])


if __name__ == "__main__":
    unittest.main()
