from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


PROJECT_ROOT = Path(__file__).parents[1]


def load_retry_symbols():
    source_path = PROJECT_ROOT / "node" / "multi_image_fusion_node.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names = {
        "FUSION_MAX_RETRIES",
        "FUSION_RETRY_DELAY_SECONDS",
        "_MODEL_AUDIT_REJECTION_PATTERNS",
        "MultiImageFusionNode",
    }
    definitions = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in names
            for target in node.targets
        ):
            definitions.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "MultiImageFusionNode":
            methods = [
                item
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.ClassDef))
                and item.name
                in {
                    "_is_model_audit_rejection",
                    "_wait_for_fusion_retry",
                    "_run_fusion_with_retry",
                }
            ]
            definitions.append(
                ast.ClassDef(
                    name=node.name,
                    bases=[],
                    keywords=[],
                    decorator_list=[],
                    body=methods,
                )
            )
    module = ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[]))
    namespace = {
        "InterruptProcessingException": type("InterruptProcessingException", (Exception,), {}),
        "nodes": SimpleNamespace(before_node_execution=lambda: None),
        "re": __import__("re"),
        "time": __import__("time"),
    }
    exec(compile(module, str(source_path), "exec"), namespace)
    return namespace


class FusionRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.symbols = load_retry_symbols()
        cls.node = cls.symbols["MultiImageFusionNode"]

    def setUp(self):
        self.waits = []
        self.original_wait = self.node._wait_for_fusion_retry
        self.node._wait_for_fusion_retry = self.waits.append

    def tearDown(self):
        self.node._wait_for_fusion_retry = self.original_wait

    def test_log_confirmed_sensitive_image_rejection_is_not_retryable(self):
        message = (
            "input new_sensitive, messages[1]'s content[4] image is sensitive, "
            "please check your input (1026)"
        )

        self.assertTrue(self.node._is_model_audit_rejection(message))

    def test_timeout_is_retryable(self):
        self.assertFalse(self.node._is_model_audit_rejection("HTTP 503 upstream timeout"))

    def test_audit_rejection_stops_after_one_request(self):
        calls = []

        result = self.node._run_fusion_with_retry(
            "fusion_test",
            lambda: calls.append(1) or {"success": False, "error": "input new_sensitive (1026)"},
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(self.waits, [])
        self.assertFalse(result["success"])

    def test_transient_error_retries_then_returns_success(self):
        responses = iter((
            {"success": False, "error": "HTTP 503 upstream timeout"},
            {"success": False, "error": "connection reset"},
            {"success": True, "data": {"description": "done"}},
        ))

        result = self.node._run_fusion_with_retry("fusion_test", lambda: next(responses))

        self.assertTrue(result["success"])
        self.assertEqual(self.waits, [10.0, 10.0])

    def test_transient_error_is_limited_to_five_retries(self):
        calls = []

        result = self.node._run_fusion_with_retry(
            "fusion_test",
            lambda: calls.append(1) or {"success": False, "error": "network timeout"},
        )

        self.assertFalse(result["success"])
        self.assertEqual(len(calls), 6)
        self.assertEqual(self.waits, [10.0] * 5)


if __name__ == "__main__":
    unittest.main()
