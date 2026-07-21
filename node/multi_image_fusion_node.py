"""
多图融合提示词节点 - V3

不是像素级融图，而是：
- 输入多张参考图（IMAGE batch 或 最多 4 路单图）
- 输入用户希望“整合到一张画面里”的描述
- 调用 VLM 一次看多图，输出可直接用于生图的融合提示词
"""

from __future__ import annotations

import hashlib
from typing import Dict, List, Tuple

import torch
from comfy.model_management import InterruptProcessingException
from comfy_api.latest import io

from ..services.thinking_control import build_thinking_suppression
from ..services.vlm import VisionService
from ..utils.common import (
    SOURCE_NODE,
    TASK_MULTI_IMAGE_FUSION,
    format_api_error,
    format_model_with_thinking,
    generate_request_id,
    get_model_max_images,
    log_error,
    log_prepare,
)
from .base import VLMNodeBase


DEFAULT_FUSION_RULE = """Role
你是一位多图融合构图导演，也是高精度提示词工程师。
你的任务不是分别描述每张图，也不是做像素级融图，而是：
根据用户提供的多张参考图，以及用户给出的“希望整合到一张图内的画面描述”，
生成一段可直接用于文生图/图生图模型的融合提示词。

最高指令
1. 用户意图优先：用户文字描述决定最终画面如何整合；参考图只提供可复用的视觉素材。
2. 必须整合为一张图：输出只能描述一个统一画面，禁止输出多张独立图片的分别说明。
3. 明确素材归属：写清图1/图2/图3/图4分别贡献了什么（主体、服装、场景、光影、风格、道具、姿态等）。
4. 解决冲突：当参考图互相矛盾时，以用户描述为准，并自然统一透视、光源、色温和空间关系。
5. 输出纯净：只输出最终提示词正文，不要解释过程，不要标题，不要编号列表。
6. 语言自适应：用户用中文描述则输出中文；用户用英文描述则输出英文。

输出要求
- 先交代统一场景与整体构图
- 再写清各参考图元素如何进入同一画面
- 补足光影、材质、空间层次、风格一致性
- 适合直接喂给绘图模型
"""


class MultiImageFusionNode(VLMNodeBase, io.ComfyNode):
    """多图融合提示词节点：多张参考图 + 融合描述 -> 一张图的提示词"""

    @classmethod
    def define_schema(cls):
        service_options = cls.get_vlm_service_options()
        default_service = service_options[0] if service_options else "智谱"

        from ..config_manager import config_manager

        system_prompts = config_manager.get_system_prompts() or {}
        fusion_prompts = system_prompts.get("fusion_prompts", {}) or {}
        active_fusion_id = (system_prompts.get("active_prompts", {}) or {}).get("fusion")

        prompt_template_options: List[str] = []
        id_to_display_name: Dict[str, str] = {}
        for key, value in fusion_prompts.items():
            show_in = value.get("showIn", ["frontend", "node"])
            if "node" not in show_in:
                continue
            name = value.get("name", key)
            category = value.get("category", "")
            display_name = f"{category}/{name}" if category else name
            id_to_display_name[key] = display_name
            prompt_template_options.append(display_name)

        if not prompt_template_options:
            prompt_template_options = ["多图融合-统一构图"]

        default_template_name = prompt_template_options[0]
        if active_fusion_id and active_fusion_id in id_to_display_name:
            default_template_name = id_to_display_name[active_fusion_id]

        return io.Schema(
            node_id="MultiImageFusionNode",
            display_name="✨Multi-Image Fusion Prompt",
            category="✨Prompt Assistant",
            description=(
                "Generate one composition prompt from multiple reference images "
                "and a user description of how to integrate them into a single scene"
            ),
            inputs=[
                io.Image.Input(
                    "images",
                    optional=True,
                    tooltip="Preferred input: IMAGE batch. Image 1..N follow batch order.",
                ),
                io.Image.Input(
                    "image_1",
                    optional=True,
                    tooltip="Optional single image as Image 1. Used when images batch is empty.",
                ),
                io.Image.Input(
                    "image_2",
                    optional=True,
                    tooltip="Optional single image as Image 2.",
                ),
                io.Image.Input(
                    "image_3",
                    optional=True,
                    tooltip="Optional single image as Image 3.",
                ),
                io.Image.Input(
                    "image_4",
                    optional=True,
                    tooltip="Optional single image as Image 4.",
                ),
                io.String.Input(
                    "fusion_description",
                    multiline=True,
                    default="",
                    placeholder="Describe how to integrate these images into one scene",
                    tooltip=(
                        "Required. Describe the final single-image composition, e.g. "
                        "'Put the person from image 1 into the room from image 2, wearing outfit from image 3'."
                    ),
                ),
                io.Combo.Input(
                    "rule",
                    options=prompt_template_options,
                    default=default_template_name,
                    tooltip="Preset fusion rule",
                ),
                io.Boolean.Input(
                    "custom_rule",
                    default=False,
                    label_on="Enable",
                    label_off="Disable",
                    tooltip="Enable custom rule content",
                ),
                io.String.Input(
                    "custom_rule_content",
                    multiline=True,
                    default="",
                    tooltip="Custom rule content, only used when Custom Rule is enabled",
                ),
                io.Combo.Input(
                    "output_style",
                    options=["Auto", "Natural Language", "Tags", "Edit Instruction"],
                    default="Auto",
                    tooltip="Preferred output style for the fused prompt",
                ),
                io.Combo.Input(
                    "vlm_service",
                    options=service_options,
                    default=default_service,
                    tooltip="Select VLM service and model",
                ),
                io.Boolean.Input(
                    "ollama_auto_unload",
                    default=True,
                    label_on="Enable",
                    label_off="Disable",
                    tooltip="Auto unload Ollama model after generation",
                ),
                io.Int.Input(
                    "seed",
                    default=0,
                    min=0,
                    max=0xFFFFFFFFFFFFFFFF,
                    control_after_generate=True,
                    tooltip="Forces re-execution when changed. Sampling seed support depends on provider.",
                ),
            ],
            outputs=[
                io.String.Output("fusion_prompt"),
                io.String.Output("image_roles"),
                io.Image.Output("preview_images"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(
        cls,
        images=None,
        image_1=None,
        image_2=None,
        image_3=None,
        image_4=None,
        fusion_description=None,
        rule=None,
        custom_rule=None,
        custom_rule_content=None,
        output_style=None,
        vlm_service=None,
        ollama_auto_unload=None,
        seed=None,
    ):
        desc_hash = hashlib.md5((fusion_description or "").encode("utf-8")).hexdigest()
        rule_hash = hashlib.md5((custom_rule_content or "").encode("utf-8")).hexdigest()
        img_hash = cls._hash_image_inputs(images, image_1, image_2, image_3, image_4)
        return hash(
            (
                img_hash,
                desc_hash,
                rule,
                bool(custom_rule),
                rule_hash,
                output_style,
                vlm_service,
                bool(ollama_auto_unload),
                seed,
            )
        )

    @classmethod
    def _hash_image_inputs(cls, *image_values) -> str:
        parts: List[str] = []
        for value in image_values:
            if value is None:
                parts.append("none")
                continue
            try:
                if hasattr(value, "shape"):
                    shape = tuple(value.shape)
                    flat = value.detach().float().cpu().reshape(-1)
                    sample_idx = [0, len(flat) // 2, len(flat) - 1] if flat.numel() else []
                    sample = ",".join(f"{float(flat[i]):.4f}" for i in sample_idx)
                    parts.append(f"{shape}:{sample}")
                else:
                    parts.append(str(type(value)))
            except Exception:
                parts.append("hash_error")
        return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()

    @classmethod
    def _collect_images(
        cls,
        images=None,
        image_1=None,
        image_2=None,
        image_3=None,
        image_4=None,
    ) -> torch.Tensor:
        """收集输入图像为 [N,H,W,C] batch。优先 images batch，否则拼接 image_1..4。"""
        tensors: List[torch.Tensor] = []

        if images is not None and hasattr(images, "numel") and images.numel() > 0:
            batch = images
            if len(batch.shape) == 3:
                batch = batch.unsqueeze(0)
            if len(batch.shape) != 4:
                raise ValueError(f"Unsupported images tensor shape: {tuple(batch.shape)}")
            for i in range(batch.shape[0]):
                tensors.append(batch[i : i + 1])
        else:
            for img in (image_1, image_2, image_3, image_4):
                if img is None or not hasattr(img, "numel") or img.numel() == 0:
                    continue
                tensor = img
                if len(tensor.shape) == 3:
                    tensor = tensor.unsqueeze(0)
                if len(tensor.shape) != 4:
                    raise ValueError(f"Unsupported image tensor shape: {tuple(tensor.shape)}")
                for i in range(tensor.shape[0]):
                    tensors.append(tensor[i : i + 1])

        if not tensors:
            raise ValueError(
                "No images provided. Connect an IMAGE batch to 'images', "
                "or provide image_1/image_2/..."
            )

        base = tensors[0]
        _, base_h, base_w, _ = base.shape
        normalized: List[torch.Tensor] = [base]
        for tensor in tensors[1:]:
            if tensor.shape[1] == base_h and tensor.shape[2] == base_w:
                normalized.append(tensor)
                continue
            nchw = tensor.permute(0, 3, 1, 2).float()
            resized = torch.nn.functional.interpolate(
                nchw, size=(base_h, base_w), mode="bilinear", align_corners=False
            )
            normalized.append(resized.permute(0, 2, 3, 1).to(tensor.dtype))

        return torch.cat(normalized, dim=0)

    @classmethod
    def _tensor_to_data_urls(cls, batch: torch.Tensor, max_images: int) -> Tuple[List[str], torch.Tensor]:
        if batch.shape[0] > max_images:
            batch = batch[:max_images]

        count = batch.shape[0]
        if count <= 2:
            max_edge, quality = 1280, 90
        elif count <= 4:
            max_edge, quality = 1024, 85
        else:
            max_edge, quality = 896, 80

        data_urls: List[str] = []
        preview_frames: List[torch.Tensor] = []
        for i in range(batch.shape[0]):
            frame = batch[i : i + 1]
            preview_frames.append(frame)
            h, w = int(frame.shape[1]), int(frame.shape[2])
            longest = max(h, w)
            encode_frame = frame
            if longest > max_edge:
                scale = max_edge / float(longest)
                new_h = max(1, int(h * scale))
                new_w = max(1, int(w * scale))
                nchw = frame.permute(0, 3, 1, 2).float()
                resized = torch.nn.functional.interpolate(
                    nchw, size=(new_h, new_w), mode="bilinear", align_corners=False
                )
                encode_frame = resized.permute(0, 2, 3, 1)
            data_urls.append(cls._image_to_base64(encode_frame, quality=quality))

        return data_urls, torch.cat(preview_frames, dim=0)

    @classmethod
    def _resolve_rule_content(
        cls,
        rule: str,
        custom_rule: bool,
        custom_rule_content: str,
    ) -> Tuple[str, str]:
        if custom_rule and custom_rule_content and custom_rule_content.strip():
            return custom_rule_content.strip(), "Custom Rule"

        from ..config_manager import config_manager

        system_prompts = config_manager.get_system_prompts() or {}
        fusion_prompts = system_prompts.get("fusion_prompts", {}) or {}

        for key, value in fusion_prompts.items():
            name = value.get("name", key)
            category = value.get("category", "")
            display_name = f"{category}/{name}" if category else name
            if display_name == rule or name == rule or key == rule:
                content = (value.get("content") or "").strip()
                if content:
                    return content, name

        return DEFAULT_FUSION_RULE, "Default Fusion Rule"

    @classmethod
    def _build_prompt(
        cls,
        rule_content: str,
        fusion_description: str,
        image_count: int,
        output_style: str,
    ) -> str:
        style = (output_style or "Auto").strip()
        style_hint = {
            "Auto": "根据用户描述与参考图自动选择最适合的提示词风格。",
            "Natural Language": "输出自然语言长描述，适合 Flux / 通用文生图。",
            "Tags": "输出逗号分隔的标签流，可带适度权重，适合 SD/SDXL。",
            "Edit Instruction": "输出可执行的图像编辑/合成指令，适合 Kontext / Qwen-Image-Edit 等编辑模型。",
        }.get(style, "根据用户描述与参考图自动选择最适合的提示词风格。")

        role_lines = "\n".join(
            [f"- 图{i}: 参考图 {i}（Image {i}）" for i in range(1, image_count + 1)]
        )

        return (
            f"{rule_content.strip()}\n\n"
            f"[参考图数量]\n共 {image_count} 张，顺序如下：\n{role_lines}\n\n"
            f"[输出风格偏好]\n{style_hint}\n\n"
            f"[用户希望整合到一张图内的画面描述]\n{fusion_description.strip()}\n\n"
            "请综合以上参考图与用户描述，输出最终融合提示词。"
        )

    @classmethod
    def _build_image_roles_text(cls, image_count: int, fusion_description: str) -> str:
        lines = [f"Image {i}: reference image {i}" for i in range(1, image_count + 1)]
        lines.append(f"User intent: {fusion_description.strip()}")
        return "\n".join(lines)

    @classmethod
    def execute(
        cls,
        images=None,
        image_1=None,
        image_2=None,
        image_3=None,
        image_4=None,
        fusion_description=None,
        rule=None,
        custom_rule=None,
        custom_rule_content=None,
        output_style=None,
        vlm_service=None,
        ollama_auto_unload=None,
        seed=None,
    ):
        unique_id = cls.hidden.unique_id
        request_id = None

        try:
            fusion_description = (fusion_description or "").strip()
            if not fusion_description:
                raise ValueError(
                    "fusion_description is required. "
                    "Describe how to integrate the reference images into one scene."
                )

            batch = cls._collect_images(images, image_1, image_2, image_3, image_4)
            if batch.shape[0] < 2:
                raise ValueError("Multi-image fusion needs at least 2 images.")

            rule_content, rule_name = cls._resolve_rule_content(
                rule or "",
                bool(custom_rule),
                custom_rule_content or "",
            )

            service_id, model_name = cls.parse_service_model(vlm_service or "")
            if not service_id:
                raise ValueError(f"Invalid service selection: {vlm_service}")

            from ..config_manager import config_manager

            service = config_manager.get_service(service_id)
            if not service:
                raise ValueError(f"Service config not found: {vlm_service}")

            vlm_models = service.get("vlm_models", [])
            target_model = None
            if model_name:
                target_model = next((m for m in vlm_models if m.get("name") == model_name), None)
            if not target_model:
                target_model = next(
                    (m for m in vlm_models if m.get("is_default")),
                    vlm_models[0] if vlm_models else None,
                )
            if not target_model:
                raise ValueError(f"Service {vlm_service} has no available vision models")

            provider_config = {
                "provider": service_id,
                "model": target_model.get("name", ""),
                "base_url": service.get("base_url", ""),
                "api_key": service.get("api_key", ""),
                "temperature": target_model.get("temperature", 0.7),
                "max_tokens": max(int(target_model.get("max_tokens", 1000) or 1000), 1200),
                "top_p": target_model.get("top_p", 0.9),
            }
            if service.get("type") == "ollama":
                provider_config["auto_unload"] = bool(ollama_auto_unload)

            if not provider_config.get("model"):
                raise ValueError(f"Please configure model for {vlm_service}")
            if cls._service_requires_api_key(service) and not provider_config.get("api_key"):
                raise ValueError(f"Please configure API key and model for {vlm_service}")

            max_images = get_model_max_images(provider_config.get("model"))
            max_images = max(2, min(max_images, 8))
            images_data, preview_tensor = cls._tensor_to_data_urls(batch, max_images=max_images)
            image_count = len(images_data)

            prompt_to_send = cls._build_prompt(
                rule_content=rule_content,
                fusion_description=fusion_description,
                image_count=image_count,
                output_style=output_style or "Auto",
            )

            request_id = generate_request_id("fusion", None, unique_id)
            model_full_name = provider_config.get("model")
            disable_thinking_enabled = service.get("disable_thinking", True)
            thinking_extra = (
                build_thinking_suppression(service_id, model_full_name)
                if disable_thinking_enabled
                else None
            )
            model_display = format_model_with_thinking(model_full_name, bool(thinking_extra))
            service_display_name = service.get("name", service_id)

            log_prepare(
                TASK_MULTI_IMAGE_FUSION,
                request_id,
                SOURCE_NODE,
                service_display_name,
                model_display,
                rule_name,
                {"图像数": image_count, "输出风格": output_style or "Auto"},
            )

            result = cls._run_vision_task(
                VisionService.analyze_images,
                service_id,
                images_data=images_data,
                prompt_content=prompt_to_send,
                request_id=request_id,
                custom_provider=service_id,
                custom_provider_config=provider_config,
                task_type=TASK_MULTI_IMAGE_FUSION,
                source=SOURCE_NODE,
            )

            if result and result.get("success"):
                data = result.get("data", {}) or {}
                fusion_prompt = (
                    data.get("description")
                    or data.get("caption")
                    or data.get("result")
                    or ""
                ).strip()
                if not fusion_prompt:
                    error_msg = "API returned empty result"
                    log_error(TASK_MULTI_IMAGE_FUSION, request_id, error_msg, source=SOURCE_NODE)
                    raise RuntimeError(f"Fusion failed: {error_msg}")

                image_roles = cls._build_image_roles_text(image_count, fusion_description)
                return io.NodeOutput(fusion_prompt, image_roles, preview_tensor)

            error_msg = (
                result.get("error", "Unknown error") if result else "No result returned"
            )
            if error_msg == "任务被中断":
                raise InterruptProcessingException()
            log_error(TASK_MULTI_IMAGE_FUSION, request_id, error_msg, source=SOURCE_NODE)
            raise RuntimeError(f"Fusion failed: {error_msg}")

        except InterruptProcessingException:
            raise
        except Exception as e:
            error_msg = format_api_error(e, vlm_service)
            log_error(TASK_MULTI_IMAGE_FUSION, request_id, error_msg, source=SOURCE_NODE)
            raise RuntimeError(f"Fusion error: {error_msg}")
