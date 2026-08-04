from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).parents[1]
PACKAGE = "prompt_assistant_google_web_test"


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


def load_google_web_module():
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

    path = ROOT / "services" / "google_web_translate.py"
    name = f"{PACKAGE}.services.google_web_translate"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class GoogleWebTranslateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_google_web_module()

    def test_uses_keyless_web_parameters_and_joins_response_segments(self):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return [[['你好', 'Hello'], ['，世界', ', world']], None, 'en']

        class Client:
            def __init__(self):
                self.calls = []

            async def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()

        client = Client()
        DummyHTTPClientPool.client = client
        result = asyncio.run(
            self.module.GoogleWebTranslateService.translate("Hello, world", "auto", "zh")
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["translated"], "你好，世界")
        _, kwargs = client.calls[0]
        self.assertEqual(kwargs["params"]["client"], "gtx")
        self.assertEqual(kwargs["params"]["sl"], "auto")
        self.assertEqual(kwargs["params"]["tl"], "zh")
        self.assertEqual(kwargs["params"]["q"], "Hello, world")
        self.assertNotIn("key", kwargs["params"])

    def test_rejects_an_invalid_web_response(self):
        with self.assertRaises(RuntimeError):
            self.module.GoogleWebTranslateService._parse_response({"error": "blocked"})

    def test_falls_back_to_lightweight_page_and_maps_chinese_language_code(self):
        class JsonResponse:
            status_code = 429

        class MobileResponse:
            status_code = 200
            text = '<div class="result-container">你好世界</div>'

        class Client:
            def __init__(self):
                self.calls = []

            async def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return JsonResponse() if len(self.calls) == 1 else MobileResponse()

        client = Client()
        DummyHTTPClientPool.client = client
        result = asyncio.run(
            self.module.GoogleWebTranslateService.translate("Hello world", "auto", "zh")
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["translated"], "你好世界")
        fallback_url, fallback_kwargs = client.calls[1]
        self.assertEqual(fallback_url, self.module.GoogleWebTranslateService.MOBILE_URL)
        self.assertEqual(fallback_kwargs["params"]["tl"], "zh-CN")


if __name__ == "__main__":
    unittest.main()
