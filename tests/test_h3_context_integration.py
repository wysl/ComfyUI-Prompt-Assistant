from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


INSERTED_TORCH_STUB = "torch" not in sys.modules
if INSERTED_TORCH_STUB:
    torch_stub = types.ModuleType("torch")
    torch_stub.Tensor = type("Tensor", (), {})
    sys.modules["torch"] = torch_stub

MODULE_PATH = Path(__file__).parents[1] / "utils" / "multimedia_reference.py"
SPEC = importlib.util.spec_from_file_location("h3_context_multimedia_reference", MODULE_PATH)
multimedia_reference = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = multimedia_reference
SPEC.loader.exec_module(multimedia_reference)
if INSERTED_TORCH_STUB:
    del sys.modules["torch"]


class FakeH3Context:
    def __init__(self, payload):
        self.payload = payload

    def prompt_assistant_payload(self):
        return self.payload


class H3ContextIntegrationTests(unittest.TestCase):
    def test_reference_context_preserves_prompt_and_ordered_media(self):
        image = object()
        video = object()
        audio = object()
        context = FakeH3Context(
            {
                "mode": "reference",
                "prompt": "original prompt",
                "images": [image],
                "videos": [video],
                "audios": [audio],
                "keyframe_roles": [],
            }
        )

        result = multimedia_reference.extract_h3_context_inputs([context])

        self.assertEqual(result.mode, "reference")
        self.assertEqual(result.prompt, "original prompt")
        self.assertEqual(result.images, (image,))
        self.assertEqual(result.videos, (video,))
        self.assertEqual(result.audios, (audio,))
        self.assertEqual(result.synchronized_audio_count, 0)
        self.assertEqual(result.synchronized_audio_video_indices, ())

    def test_context_preserves_synchronized_audio_video_mapping(self):
        context = FakeH3Context(
            {
                "mode": "reference",
                "videos": [object(), object()],
                "audios": [object()],
                "synchronized_audio_count": 1,
                "synchronized_audio_video_indices": [2],
            }
        )

        result = multimedia_reference.extract_h3_context_inputs(context)

        self.assertEqual(result.synchronized_audio_count, 1)
        self.assertEqual(result.synchronized_audio_video_indices, (2,))

    def test_synchronized_video_audio_does_not_consume_standalone_audio_limit(self):
        audios = [
            multimedia_reference.AudioReference(value=object(), duration=None)
            for _ in range(4)
        ]

        multimedia_reference.validate_h3_references(
            1,
            [],
            audios,
            synchronized_audio_count=1,
        )
        with self.assertRaisesRegex(ValueError, "at most 3 reference audios"):
            multimedia_reference.validate_h3_references(1, [], audios)

    def test_base_context_validates_keyframe_roles(self):
        context = FakeH3Context(
            {
                "mode": "image",
                "prompt": "move naturally",
                "images": [object(), object()],
                "keyframe_roles": ["first"],
            }
        )

        with self.assertRaisesRegex(ValueError, "keyframe metadata"):
            multimedia_reference.extract_h3_context_inputs(context)

    def test_old_context_reports_upgrade_requirement(self):
        with self.assertRaisesRegex(ValueError, "Update ComfyUI-MiniMaxH3-Easy"):
            multimedia_reference.extract_h3_context_inputs(object())


if __name__ == "__main__":
    unittest.main()
