"""Helpers for normalizing ComfyUI multimedia reference inputs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable, List, Optional, Sequence, Tuple

import torch


H3_OUTPUT_STYLE = "MiniMax H3 Ref2VA"
H3_MAX_IMAGES = 9
H3_MAX_VIDEOS = 3
H3_MAX_AUDIOS = 3
H3_MAX_TOTAL_FILES = 12
H3_MIN_MEDIA_DURATION = 2.0
H3_MAX_MEDIA_DURATION = 15.0


@dataclass
class VideoReference:
    frames: torch.Tensor
    duration: Optional[float]


@dataclass
class AudioReference:
    value: Any
    duration: Optional[float]


def unwrap_scalar(value: Any, default: Any = None) -> Any:
    """Unwrap scalar widgets when a V3 node uses ``is_input_list=True``."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else default
    return default if value is None else value


def iter_input_values(value: Any) -> Iterable[Any]:
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_input_values(item)
    elif value is not None:
        yield value


def _tensor_frames(value: Any) -> List[torch.Tensor]:
    if not isinstance(value, torch.Tensor) or value.numel() == 0:
        return []
    tensor = value
    if tensor.ndim == 3:
        tensor = tensor.unsqueeze(0)
    if tensor.ndim != 4:
        raise ValueError(f"Unsupported image tensor shape: {tuple(tensor.shape)}")
    return [tensor[index : index + 1] for index in range(tensor.shape[0])]


def collect_image_frames(images: Any) -> List[torch.Tensor]:
    """Collect an IMAGE batch or ComfyUI list output while preserving order."""
    frames: List[torch.Tensor] = []
    for value in iter_input_values(images):
        frames.extend(_tensor_frames(value))
    return frames


def normalize_frames(frames: Sequence[torch.Tensor]) -> torch.Tensor:
    if not frames:
        raise ValueError("No visual references were provided.")
    base = frames[0]
    _, base_h, base_w, _ = base.shape
    normalized: List[torch.Tensor] = []
    for frame in frames:
        if frame.shape[1] == base_h and frame.shape[2] == base_w:
            normalized.append(frame)
            continue
        nchw = frame.permute(0, 3, 1, 2).float()
        resized = torch.nn.functional.interpolate(
            nchw, size=(base_h, base_w), mode="bilinear", align_corners=False
        )
        normalized.append(resized.permute(0, 2, 3, 1).to(frame.dtype))
    return torch.cat(normalized, dim=0)


def _mapping_tensor(mapping: dict) -> Optional[torch.Tensor]:
    for key in ("frames", "video", "images"):
        value = mapping.get(key)
        if isinstance(value, torch.Tensor) and value.numel() > 0:
            return value
    return None


def extract_video_frames(video: Any) -> Optional[torch.Tensor]:
    if isinstance(video, torch.Tensor):
        return video if video.numel() > 0 else None
    if isinstance(video, dict):
        return _mapping_tensor(video)

    try:
        components = video.get_components()
        images = getattr(components, "images", None)
        if isinstance(images, torch.Tensor) and images.numel() > 0:
            return images
    except Exception:
        pass

    for attr in ("frames", "video", "images"):
        value = getattr(video, attr, None)
        if isinstance(value, torch.Tensor) and value.numel() > 0:
            return value
    return None


def _positive_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def video_duration(video: Any, frames: torch.Tensor) -> Optional[float]:
    try:
        duration = _positive_float(video.get_duration())
        if duration is not None:
            return duration
    except Exception:
        pass

    if isinstance(video, dict):
        duration = _positive_float(video.get("duration"))
        fps = _positive_float(video.get("fps") or video.get("frame_rate"))
    else:
        duration = _positive_float(getattr(video, "duration", None))
        fps = None
        try:
            fps = _positive_float(video.get_frame_rate())
        except Exception:
            fps = _positive_float(
                getattr(video, "fps", None) or getattr(video, "frame_rate", None)
            )
    if duration is not None:
        return duration
    if fps is not None:
        return float(frames.shape[0]) / fps
    return None


def collect_video_references(*values: Any) -> List[VideoReference]:
    references: List[VideoReference] = []
    for input_value in values:
        for video in iter_input_values(input_value):
            frames = extract_video_frames(video)
            if frames is None:
                raise ValueError("Unable to extract frames from a VIDEO reference.")
            if frames.ndim == 3:
                frames = frames.unsqueeze(0)
            if frames.ndim != 4 or frames.shape[0] == 0:
                raise ValueError(f"Unsupported video frame tensor shape: {tuple(frames.shape)}")
            references.append(VideoReference(frames=frames, duration=video_duration(video, frames)))
    return references


def audio_duration(audio: Any) -> Optional[float]:
    if not isinstance(audio, dict):
        return _positive_float(getattr(audio, "duration", None))
    waveform = audio.get("waveform")
    sample_rate = _positive_float(audio.get("sample_rate") or audio.get("sampler_rate"))
    if isinstance(waveform, torch.Tensor) and waveform.numel() > 0 and sample_rate:
        return float(waveform.shape[-1]) / sample_rate
    return _positive_float(audio.get("duration"))


def collect_audio_references(*values: Any) -> List[AudioReference]:
    references: List[AudioReference] = []
    for input_value in values:
        for audio in iter_input_values(input_value):
            waveform = audio.get("waveform") if isinstance(audio, dict) else None
            if isinstance(waveform, torch.Tensor) and waveform.ndim >= 3 and waveform.shape[0] > 1:
                for index in range(waveform.shape[0]):
                    item = dict(audio)
                    item["waveform"] = waveform[index : index + 1]
                    references.append(
                        AudioReference(value=item, duration=audio_duration(item))
                    )
                continue
            references.append(AudioReference(value=audio, duration=audio_duration(audio)))
    return references


def validate_h3_references(
    image_count: int,
    videos: Sequence[VideoReference],
    audios: Sequence[AudioReference],
) -> None:
    if image_count > H3_MAX_IMAGES:
        raise ValueError(f"MiniMax H3 supports at most {H3_MAX_IMAGES} reference images.")
    if len(videos) > H3_MAX_VIDEOS:
        raise ValueError(f"MiniMax H3 supports at most {H3_MAX_VIDEOS} reference videos.")
    if len(audios) > H3_MAX_AUDIOS:
        raise ValueError(f"MiniMax H3 supports at most {H3_MAX_AUDIOS} reference audios.")
    if image_count == 0 and not videos:
        raise ValueError("MiniMax H3 Ref2VA needs at least one reference image or video.")

    total_files = image_count + len(videos) + len(audios)
    if total_files > H3_MAX_TOTAL_FILES:
        raise ValueError(
            f"MiniMax H3 supports at most {H3_MAX_TOTAL_FILES} mixed reference files."
        )

    for kind, references in (("video", videos), ("audio", audios)):
        known_durations = [ref.duration for ref in references if ref.duration is not None]
        for index, duration in enumerate((ref.duration for ref in references), 1):
            if duration is None:
                continue
            if duration < H3_MIN_MEDIA_DURATION or duration > H3_MAX_MEDIA_DURATION:
                raise ValueError(
                    f"MiniMax H3 reference {kind} {index} must be 2-15 seconds; "
                    f"received {duration:.2f}s."
                )
        if sum(known_durations) > H3_MAX_MEDIA_DURATION + 1e-6:
            raise ValueError(
                f"MiniMax H3 total reference {kind} duration must not exceed 15 seconds."
            )


def _uniform_indices(total: int, count: int) -> List[int]:
    if count >= total:
        return list(range(total))
    if count <= 1:
        return [total // 2]
    return [round(index * (total - 1) / (count - 1)) for index in range(count)]


def build_visual_payload(
    image_frames: Sequence[torch.Tensor],
    videos: Sequence[VideoReference],
    max_payload_images: int,
    frames_per_video: int,
) -> Tuple[torch.Tensor, List[str]]:
    minimum = len(image_frames) + len(videos)
    if minimum > max_payload_images:
        raise ValueError(
            "The selected VLM cannot inspect every H3 visual reference in one request: "
            f"needs at least {minimum} images, model limit is {max_payload_images}."
        )

    allocations = [1 for _ in videos]
    remaining = max_payload_images - minimum
    desired_extra = max(0, frames_per_video - 1)
    for _ in range(desired_extra):
        for index, video in enumerate(videos):
            if remaining <= 0:
                break
            if allocations[index] < video.frames.shape[0]:
                allocations[index] += 1
                remaining -= 1

    payload_frames: List[torch.Tensor] = list(image_frames)
    labels = [f"VLM Image {i} => <Picture {i}>" for i in range(1, len(image_frames) + 1)]
    payload_index = len(payload_frames)
    for video_index, (video, count) in enumerate(zip(videos, allocations), 1):
        indices = _uniform_indices(video.frames.shape[0], count)
        for sample_index, frame_index in enumerate(indices, 1):
            payload_frames.append(video.frames[frame_index : frame_index + 1])
            payload_index += 1
            labels.append(
                f"VLM Image {payload_index} => <Video {video_index}> sampled frame "
                f"{sample_index}/{len(indices)}"
            )
    return normalize_frames(payload_frames), labels


def build_h3_reference_manifest(
    image_count: int,
    videos: Sequence[VideoReference],
    audios: Sequence[AudioReference],
    payload_labels: Sequence[str],
    user_intent: str,
) -> str:
    lines = ["MiniMax H3 Ref2VA reference order:"]
    lines.extend(f"<Picture {i}>: reference image {i}" for i in range(1, image_count + 1))
    for index, video in enumerate(videos, 1):
        duration = f"{video.duration:.2f}s" if video.duration is not None else "duration unknown"
        lines.append(f"<Video {index}>: reference video {index} ({duration})")
    for index, audio in enumerate(audios, 1):
        duration = f"{audio.duration:.2f}s" if audio.duration is not None else "duration unknown"
        lines.append(f"<Audio {index}>: reference audio {index} ({duration})")
    lines.append("")
    lines.append("VLM visual payload mapping:")
    lines.extend(payload_labels)
    lines.append("")
    lines.append(f"User intent: {user_intent or '(empty; infer a coherent target video)'}")
    return "\n".join(lines)


def sanitize_h3_prompt(
    prompt: str, image_count: int, video_count: int, audio_count: int
) -> str:
    """Validate the H3 six-section contract and its media reference labels."""
    text = (prompt or "").strip()
    text = re.sub(r"^```(?:text|markdown)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    required = (
        "subject_definitions:",
        "summary:",
        "retention_analysis:",
        "detailed_description:",
        "overall_soundscape:",
        "non_diegetic_music:",
    )
    lowered = text.lower()
    missing = [section for section in required if section not in lowered]
    if missing:
        raise ValueError(
            "MiniMax H3 Ref2VA output is missing required sections: "
            + ", ".join(missing)
        )

    missing_labels: List[str] = []
    invented_labels: List[str] = []
    for label, count in (
        ("Picture", image_count),
        ("Video", video_count),
        ("Audio", audio_count),
    ):
        found = {
            int(value)
            for value in re.findall(rf"<{label}\s+(\d+)>", text, flags=re.IGNORECASE)
        }
        expected = set(range(1, count + 1))
        missing_labels.extend(f"<{label} {value}>" for value in sorted(expected - found))
        invented_labels.extend(f"<{label} {value}>" for value in sorted(found - expected))
    if missing_labels or invented_labels:
        details = []
        if missing_labels:
            details.append("missing " + ", ".join(missing_labels))
        if invented_labels:
            details.append("invented " + ", ".join(invented_labels))
        raise ValueError("Invalid MiniMax H3 reference labels: " + "; ".join(details))
    return text.strip()


def reference_hash(*values: Any) -> str:
    parts: List[str] = []
    for input_value in values:
        for value in iter_input_values(input_value):
            tensor = value if isinstance(value, torch.Tensor) else None
            if isinstance(value, dict):
                tensor = _mapping_tensor(value)
                if tensor is None:
                    waveform = value.get("waveform")
                    tensor = waveform if isinstance(waveform, torch.Tensor) else None
            if tensor is None:
                tensor = extract_video_frames(value)
            if isinstance(tensor, torch.Tensor) and tensor.numel() > 0:
                flat = tensor.detach().float().reshape(-1)
                positions = (0, flat.numel() // 2, flat.numel() - 1)
                sample = ",".join(f"{float(flat[pos].cpu()):.4f}" for pos in positions)
                parts.append(f"{tuple(tensor.shape)}:{sample}")
            else:
                parts.append(type(value).__name__)
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()
