from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import torch


MODULE_PATH = Path(__file__).parents[1] / "utils" / "multimedia_reference.py"
SPEC = importlib.util.spec_from_file_location("multimedia_reference", MODULE_PATH)
multimedia_reference = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = multimedia_reference
SPEC.loader.exec_module(multimedia_reference)


class MultimediaReferenceTests(unittest.TestCase):
    def test_image_tensor_batch_produces_multiple_references(self):
        batch = torch.zeros((3, 8, 8, 3))
        frames = multimedia_reference.collect_image_frames([batch])
        self.assertEqual(len(frames), 3)

    def test_image_list_output_produces_multiple_references(self):
        list_output = [torch.zeros((1, 8, 8, 3)) for _ in range(3)]
        frames = multimedia_reference.collect_image_frames(list_output)
        self.assertEqual(len(frames), 3)
        self.assertEqual(tuple(multimedia_reference.normalize_frames(frames).shape), (3, 8, 8, 3))

    def test_video_list_produces_multiple_references(self):
        videos = [
            {"frames": torch.zeros((4, 8, 8, 3)), "duration": 4.0},
            {"frames": torch.ones((6, 8, 8, 3)), "duration": 6.0},
        ]
        references = multimedia_reference.collect_video_references(videos)
        self.assertEqual(len(references), 2)
        self.assertEqual([reference.duration for reference in references], [4.0, 6.0])

    def test_audio_list_produces_multiple_references(self):
        audios = [
            {"waveform": torch.zeros((1, 2, 32000)), "sample_rate": 16000},
            {"waveform": torch.ones((1, 1, 48000)), "sample_rate": 24000},
        ]
        references = multimedia_reference.collect_audio_references(audios)
        self.assertEqual(len(references), 2)
        self.assertEqual([reference.duration for reference in references], [2.0, 2.0])

    def test_audio_waveform_batch_is_split_on_batch_dimension(self):
        audio = {
            "waveform": torch.zeros((3, 2, 32000)),
            "sample_rate": 16000,
            "source": "batch",
        }
        references = multimedia_reference.collect_audio_references([audio])
        self.assertEqual(len(references), 3)
        self.assertTrue(all(tuple(reference.value["waveform"].shape) == (1, 2, 32000) for reference in references))
        self.assertTrue(all(reference.value["source"] == "batch" for reference in references))
        self.assertTrue(all(reference.duration == 2.0 for reference in references))

    def test_video_payload_keeps_every_asset_within_model_budget(self):
        images = [torch.zeros((1, 8, 8, 3)), torch.ones((1, 8, 8, 3))]
        video = multimedia_reference.VideoReference(
            frames=torch.zeros((8, 12, 12, 3)), duration=4.0
        )
        payload, labels = multimedia_reference.build_visual_payload(
            images, [video], max_payload_images=4, frames_per_video=4
        )
        self.assertEqual(tuple(payload.shape), (4, 8, 8, 3))
        self.assertEqual(len(labels), 4)
        self.assertIn("<Picture 2>", labels[1])
        self.assertIn("<Video 1>", labels[2])

    def test_h3_rejects_audio_only_and_out_of_range_duration(self):
        audio = multimedia_reference.AudioReference(value={}, duration=4.0)
        with self.assertRaisesRegex(ValueError, "image or video"):
            multimedia_reference.validate_h3_references(0, [], [audio])

        video = multimedia_reference.VideoReference(
            frames=torch.zeros((8, 8, 8, 3)), duration=16.0
        )
        with self.assertRaisesRegex(ValueError, "2-15 seconds"):
            multimedia_reference.validate_h3_references(0, [video], [])

    def test_h3_limits_mixed_file_count(self):
        videos = [
            multimedia_reference.VideoReference(
                frames=torch.zeros((8, 8, 8, 3)), duration=4.0
            )
            for _ in range(3)
        ]
        audio = multimedia_reference.AudioReference(value={}, duration=4.0)
        with self.assertRaisesRegex(ValueError, "12 mixed reference files"):
            multimedia_reference.validate_h3_references(9, videos, [audio])

    def test_audio_duration_and_reference_hash(self):
        audio = {"waveform": torch.zeros((1, 2, 16000)), "sample_rate": 16000}
        self.assertEqual(multimedia_reference.audio_duration(audio), 1.0)
        digest = multimedia_reference.reference_hash([audio])
        self.assertEqual(len(digest), 32)

    def test_h3_prompt_validation_preserves_and_checks_media_labels(self):
        prompt = """subject_definitions: <Subject 1> comes from <Picture 1> and <Video 1>.
summary: [reference generation] Use <Audio 1> as a voice reference.
retention_analysis: All references are retained.
detailed_description: [Shot 1] The target video begins.
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        result = multimedia_reference.sanitize_h3_prompt(prompt, 1, 1, 1)
        self.assertIn("<Picture 1>", result)

        with self.assertRaisesRegex(ValueError, "missing <Audio 1>"):
            multimedia_reference.sanitize_h3_prompt(
                prompt.replace("<Audio 1>", "the audio"), 1, 1, 1
            )

        with self.assertRaisesRegex(ValueError, "invented <Video 1>"):
            multimedia_reference.sanitize_h3_prompt(prompt, 1, 0, 1)


if __name__ == "__main__":
    unittest.main()
