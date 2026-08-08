"""
多媒体参考融合提示词节点 - V3

不是像素级融图，而是：
- 输入零张、单张或多张参考图，或 MiniMax H3 所需的图片/视频/音频参考
- 输入用户的单图、多分镜图片或目标视频描述
- 调用 VLM 输出可直接用于生图或 MiniMax H3 的提示词
"""

from __future__ import annotations

import hashlib
import re
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
from ..utils.multimedia_reference import (
    H3_OUTPUT_STYLE,
    build_h3_reference_manifest,
    build_visual_payload,
    collect_audio_references,
    collect_image_frames,
    collect_video_references,
    normalize_frames,
    reference_hash,
    sanitize_h3_prompt,
    unwrap_scalar,
    validate_h3_references,
)
from .base import VLMNodeBase
from .io_types import ReferencePromptContent


DEFAULT_FUSION_RULE = """Role
你是一位多图融合构图导演与提示词工程师。
输入会提供多张参考图，以及用户希望整合到一张画面中的意图。
你的任务：把参考图中的有效视觉元素，按用户意图融合成一个完整、流畅、可直接用于生图的单画面提示词。

核心目标
- 不是分别介绍每张参考图
- 不是做像素级融图
- 而是写出最终那一张图里真实发生的画面

最高指令
1. 用户意图优先：若提供了用户描述，则用户描述决定最终画面如何整合；若用户描述为空，则仅根据参考图自动构思一个合理、完整、可生成的单画面。
2. 输出必须是一个统一完整的画面描述，读起来像在描述同一张已完成的图片。
3. 禁止在最终输出中出现任何参考图编号或来源标签，包括但不限于：图1、图2、图3、图4、Image 1、Image 2、参考图1、来自图1、把图1的、图2的。
4. 智能补全：若用户要求 A 参考图中的人物去做 B 参考图中的动作，但 A 本身缺少完成该动作所需的物件/姿态/场景支撑，必须从 B 或其他参考图中自然补上这些配套元素，并写进最终画面。
5. 解决冲突：参考图互相矛盾时，以用户意图为准，统一透视、光源、色温、风格和空间关系。
   若没有用户意图，则以主体完整性和画面合理性为准，优先保留最清晰的主体，并自然吸收其他参考图的动作、道具、场景或光影。
6. 只输出最终提示词正文：不要解释过程，不要标题，不要分点，不要写素材归属清单。
7. 语言自适应：用户中文则中文输出，用户英文则英文输出。
   若用户描述为空，默认中文输出。

写作要求
- 直接描述人物、服装、动作、道具、场景、光影、氛围、构图
- 动作与物件必须配套，不能只写动作不写完成动作所需的东西
- 画面要连贯自然，像一条完整提示词，而不是拼接说明
"""


H3_REF2VA_RULE = """[Execution mode: Full-Reference / Ref2VA]
Media references are present. Follow the Full-Reference six-section structure
from the selected MiniMax-H3 rule. Use every supplied media label exactly once
or more where relevant, never renumber or invent labels, and do not infer exact
audio speech, lyrics, or sound content that the user did not describe."""


H3_T2V_RULE = """[Execution mode: T2VA]
No image, video, or audio reference is present. Follow the T2VA three-core
structure from the selected MiniMax-H3 rule: integrated_multimodal_description,
overall_soundscape, then non_diegetic_music. Do not emit any Picture, Video,
Audio, Subject, summary, retention_analysis, or detailed_description field."""


STORYBOARD_OUTPUT_STYLE = "Storyboard Images"


class MultiImageFusionNode(VLMNodeBase, io.ComfyNode):
    """单图、多分镜图片或 MiniMax H3 多媒体参考提示词节点。"""

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
            display_name="✨Multimedia Reference Fusion Prompt",
            category="✨Prompt Assistant",
            description=(
                "Generate single-image or storyboard-image prompts from images or text, "
                "or a MiniMax H3 prompt from image/video/audio references"
            ),
            inputs=[
                io.Image.Input(
                    "images",
                    optional=True,
                    tooltip="IMAGE batch or ComfyUI IMAGE list. References follow batch/list order.",
                ),
                io.Video.Input(
                    "videos",
                    optional=True,
                    tooltip="ComfyUI VIDEO list/batch containing up to 3 H3 reference videos.",
                ),
                io.Audio.Input(
                    "audios",
                    optional=True,
                    tooltip="ComfyUI AUDIO list/batch containing up to 3 H3 reference audios.",
                ),
                io.String.Input(
                    "fusion_description",
                    multiline=True,
                    default="",
                    placeholder="Describe the target image, storyboard set, or MiniMax H3 video",
                    tooltip=(
                        "Describe how to use the references. In H3 mode, include any required "
                        "audio role, dialogue, editing, continuation, or preservation intent."
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
                ReferencePromptContent.Input(
                    "reference_prompt_content",
                    optional=True,
                    tooltip=(
                        "Connect the Multimedia Reference Prompt Library output. "
                        "This dependency supplements the selected rule and is never "
                        "used as the fusion node's bypass output."
                    ),
                ),
                io.Combo.Input(
                    "output_style",
                    options=[
                        "Auto",
                        "Natural Language",
                        "Tags",
                        "Edit Instruction",
                        STORYBOARD_OUTPUT_STYLE,
                        H3_OUTPUT_STYLE,
                    ],
                    default="Auto",
                    tooltip=(
                        "Preferred output style. Storyboard Images emits independent "
                        "Next Scene blocks. MiniMax H3 Ref2VA preserves Picture/Video/Audio "
                        "reference labels and emits six sections."
                    ),
                ),
                io.Int.Input(
                    "video_frames_per_ref",
                    default=3,
                    min=1,
                    max=8,
                    advanced=True,
                    tooltip="Representative frames sent to the VLM for each reference video in H3 mode.",
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
            is_input_list=True,
        )

    @classmethod
    def fingerprint_inputs(
        cls,
        images=None,
        videos=None,
        audios=None,
        fusion_description=None,
        rule=None,
        custom_rule=None,
        custom_rule_content=None,
        reference_prompt_content=None,
        output_style=None,
        vlm_service=None,
        ollama_auto_unload=None,
        video_frames_per_ref=None,
        seed=None,
    ):
        fusion_description = unwrap_scalar(fusion_description, "") or ""
        rule = unwrap_scalar(rule, "")
        custom_rule = unwrap_scalar(custom_rule, False)
        custom_rule_content = unwrap_scalar(custom_rule_content, "") or ""
        reference_prompt_content = unwrap_scalar(reference_prompt_content, "") or ""
        output_style = unwrap_scalar(output_style, "Auto")
        vlm_service = unwrap_scalar(vlm_service, "")
        ollama_auto_unload = unwrap_scalar(ollama_auto_unload, True)
        video_frames_per_ref = unwrap_scalar(video_frames_per_ref, 3)
        seed = unwrap_scalar(seed, 0)
        desc_hash = hashlib.md5(fusion_description.encode("utf-8")).hexdigest()
        rule_hash = hashlib.md5(custom_rule_content.encode("utf-8")).hexdigest()
        reference_prompt_hash = hashlib.md5(
            reference_prompt_content.encode("utf-8")
        ).hexdigest()
        media_hash = reference_hash(
            images,
            videos,
            audios,
        )
        return hash(
            (
                media_hash,
                desc_hash,
                rule,
                bool(custom_rule),
                rule_hash,
                reference_prompt_hash,
                output_style,
                vlm_service,
                bool(ollama_auto_unload),
                video_frames_per_ref,
                seed,
            )
        )

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
    def _format_reference_prompt_block(cls, content: str) -> str:
        reference = (content or "").strip()
        if not reference:
            return ""
        return (
            "[High-priority selected reference material]\n"
            "Fuse all relevant details from the selected files into the result. "
            "Explicit user requirements take priority. Treat these files as detailed guidance, "
            "not as text to copy mechanically: do not carry over fixed identities, clothing, "
            "scenes, dialogue, duration, or aspect ratio when they conflict with the current task. "
            "When selected files conflict with each other, a later file has higher priority.\n\n"
            f"{reference}\n\n"
        )

    @classmethod
    def _build_prompt(
        cls,
        rule_content: str,
        fusion_description: str,
        image_count: int,
        output_style: str,
        reference_prompt_content: str = "",
    ) -> str:
        style = (output_style or "Auto").strip()
        style_hint = {
            "Auto": "根据用户描述与参考图自动选择最适合的提示词风格。",
            "Natural Language": "输出自然语言长描述，适合 Flux / 通用文生图。",
            "Tags": "输出逗号分隔的标签流，可带适度权重，适合 SD/SDXL。注意标签中也不得出现图1/图2等来源词。",
            "Edit Instruction": "输出可执行的图像编辑/合成指令，适合 Kontext / Qwen-Image-Edit 等编辑模型。指令中描述最终画面本身，不要写 Image 1/图1 这类来源标签。",
            STORYBOARD_OUTPUT_STYLE: (
                "输出一组可一次性提交给多图生图模型的独立静态分镜提示词。"
                "除非用户明确指定数量，否则输出5个分镜。"
            ),
        }.get(style, "根据用户描述与参考图自动选择最适合的提示词风格。")

        # 仅供模型理解输入顺序；明确禁止写进最终输出
        ref_lines = "\n".join(
            [
                f"- 输入参考图{i}：仅供理解素材，禁止在最终提示词中写‘图{i}’或‘Image {i}’"
                for i in range(1, image_count + 1)
            ]
        )

        if style == STORYBOARD_OUTPUT_STYLE:
            intent = (fusion_description or "").strip() or (
                "（空）请根据参考图和所选参考资料设计一组连贯但画面各不相同的静态分镜。"
            )
            return (
                f"{rule_content.strip()}\n\n"
                "[模式覆盖：多分镜图片]\n"
                "本节规则优先于上方规则中任何‘单张图’‘单一画面’或‘只输出一段’要求。\n"
                "任务是生成多张彼此独立、可分别生图的静态分镜提示词，而不是视频脚本。\n\n"
                f"[输入参考图说明]\n共 {image_count} 张，按顺序提供。\n{ref_lines}\n\n"
                f"[输出风格偏好]\n{style_hint}\n\n"
                "[用户的分镜要求]\n"
                f"{intent}\n\n"
                f"{cls._format_reference_prompt_block(reference_prompt_content)}"
                "[强制输出格式]\n"
                "- 每个分镜必须恰好以 `Next Scene:` 开头，分镜之间空一行。\n"
                "- 不要添加标题、序号、项目符号、代码围栏或开场/结尾说明。\n"
                "- 每个分镜都是一条完整、自包含、可单独用于静态生图的提示词。\n"
                "- 每个分镜都要完整重述主体身份、年龄特征、发型、服装、饰品及关键连续性细节；"
                "禁止使用‘同上’‘保持上述衣着’‘相同人物’等依赖其他段落的省略说法。\n"
                "- 在维持人物和视觉连续性的同时，分镜之间应明显改变姿态或动作、景别、拍摄角度、"
                "构图、景深以及合理的场景细节，避免只替换少量词语。\n"
                "- 只写静态画面中可见的瞬间，不写时间码、镜头编号、视频分镜、镜头运动、转场、"
                "对白、音效或音乐。\n"
                "- 严禁出现‘图1/图2/Image 1/Image 2/参考图1’等素材来源标签。\n"
                "- 输出语言跟随用户输入；用户没有提供文字时默认使用中文。"
            )

        return (
            f"{rule_content.strip()}\n\n"
            f"[输入参考图说明]\n共 {image_count} 张，按顺序提供。\n{ref_lines}\n\n"
            f"[输出风格偏好]\n{style_hint}\n\n"
            f"{cls._format_user_intent_block(fusion_description)}\n\n"
            f"{cls._format_reference_prompt_block(reference_prompt_content)}"
            "请综合以上参考图与用户意图，输出最终融合提示词。\n"
            "再次强调：最终正文必须是流畅完整的单画面描述；\n"
            "严禁出现‘图1/图2/Image 1/Image 2/参考图1’等字样；\n"
            "若需要借用某张参考图的动作/道具/场景，请直接写成最终画面里的元素，不要标注来源。"
        )

    @classmethod
    def _build_h3_prompt(
        cls,
        rule_content: str,
        fusion_description: str,
        image_count: int,
        video_count: int,
        audio_count: int,
        payload_labels: List[str],
        additional_rule: str = "",
        reference_prompt_content: str = "",
    ) -> str:
        if image_count == 0 and video_count == 0 and audio_count == 0:
            intent = fusion_description or (
                "(empty; create a coherent target video from the selected text references)"
            )
            additional_rule_block = (
                f"\n\n[Additional user rule]\n{additional_rule.strip()}"
                if additional_rule and additional_rule.strip()
                else ""
            )
            return (
                f"{rule_content.strip()}\n\n"
                f"{H3_T2V_RULE}\n\n"
                "[User intent]\n"
                f"{intent}"
                f"{additional_rule_block}\n\n"
                f"{cls._format_reference_prompt_block(reference_prompt_content)}"
                "Create one coherent text-to-video prompt using only the supplied text."
            )

        reference_lines = [
            f"- <Picture {index}>: reference image {index}"
            for index in range(1, image_count + 1)
        ]
        reference_lines.extend(
            f"- <Video {index}>: reference video {index}"
            for index in range(1, video_count + 1)
        )
        reference_lines.extend(
            f"- <Audio {index}>: reference audio {index}; exact audio content is unknown unless the user describes it"
            for index in range(1, audio_count + 1)
        )
        payload_mapping = "\n".join(f"  {line}" for line in payload_labels)
        intent = fusion_description or "(empty; infer a coherent target audiovisual scene from the references)"
        additional_rule_block = (
            f"\n\n[Additional user rule]\n{additional_rule.strip()}"
            if additional_rule and additional_rule.strip()
            else ""
        )
        return (
            f"{rule_content.strip()}\n\n"
            f"{H3_REF2VA_RULE}\n\n"
            "[Reference inventory]\n"
            f"{chr(10).join(reference_lines)}\n\n"
            "[VLM visual payload mapping]\n"
            f"{payload_mapping}\n\n"
            "[User intent]\n"
            f"{intent}"
            f"{additional_rule_block}\n\n"
            f"{cls._format_reference_prompt_block(reference_prompt_content)}"
            "Use all relevant references in a single coherent target video. Keep the reference labels in the final text; "
            "do not replace them with generic words such as image or source."
        )

    @classmethod
    def _build_image_roles_text(cls, image_count: int, fusion_description: str) -> str:
        lines = [f"Image {i}: reference image {i}" for i in range(1, image_count + 1)]
        intent = (fusion_description or "").strip()
        lines.append(
            f"User intent: {intent}" if intent else "User intent: (empty, auto-compose from references)"
        )
        return "\n".join(lines)

    @classmethod
    def _format_user_intent_block(cls, fusion_description: str) -> str:
        intent = (fusion_description or "").strip()
        if intent:
            return (
                "[用户希望整合到一张图内的画面描述]\n"
                f"{intent}"
            )
        return (
            "[用户希望整合到一张图内的画面描述]\n"
            "（空）用户未提供额外描述。\n"
            "请仅根据参考图自动构思一个合理、完整、可直接用于生图的单画面。\n"
            "优先保留最清晰的主体，并自然吸收其他参考图中的动作、道具、场景或光影，使最终画面自洽。"
        )

    @classmethod
    def _sanitize_fusion_prompt(cls, prompt: str) -> str:
        """Remove source labels that confuse image generators."""
        if not prompt:
            return prompt

        text = prompt.strip()
        patterns = [
            r"图\s*[1-9]\d*\s*的",
            r"图\s*[1-9]\d*",
            r"参考图\s*[1-9]\d*\s*的",
            r"参考图\s*[1-9]\d*",
            r"来自图\s*[1-9]\d*",
            r"把图\s*[1-9]\d*",
            r"Image\s*[1-9]\d*\s*'?s",
            r"Image\s*[1-9]\d*",
            r"reference image\s*[1-9]\d*",
            r"from image\s*[1-9]\d*",
        ]
        for patt in patterns:
            text = re.sub(patt, "", text, flags=re.IGNORECASE)

        text = re.sub(r"[^\S\r\n]{2,}", " ", text)
        text = re.sub(r"[^\S\r\n]+([，。！？、,.;:!?])", r"\1", text)
        text = re.sub(r"([，、]){2,}", r"\1", text)
        text = re.sub(r"[ \t]+\r?\n", "\n", text)
        text = re.sub(r"\r?\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip(" ，,")

    @classmethod
    def execute(
        cls,
        images=None,
        videos=None,
        audios=None,
        fusion_description=None,
        rule=None,
        custom_rule=None,
        custom_rule_content=None,
        reference_prompt_content=None,
        output_style=None,
        video_frames_per_ref=None,
        vlm_service=None,
        ollama_auto_unload=None,
        seed=None,
    ):
        unique_id = cls.hidden.unique_id
        request_id = None

        try:
            fusion_description = (unwrap_scalar(fusion_description, "") or "").strip()
            rule = unwrap_scalar(rule, "") or ""
            custom_rule = bool(unwrap_scalar(custom_rule, False))
            custom_rule_content = unwrap_scalar(custom_rule_content, "") or ""
            reference_prompt_content = unwrap_scalar(reference_prompt_content, "") or ""
            output_style = unwrap_scalar(output_style, "Auto") or "Auto"
            video_frames_per_ref = int(unwrap_scalar(video_frames_per_ref, 3) or 3)
            vlm_service = unwrap_scalar(vlm_service, "") or ""
            ollama_auto_unload = bool(unwrap_scalar(ollama_auto_unload, True))
            seed = unwrap_scalar(seed, 0)

            image_frames = collect_image_frames(images)
            videos = collect_video_references(videos)
            audios = collect_audio_references(audios)
            is_h3 = output_style == H3_OUTPUT_STYLE
            is_storyboard = output_style == STORYBOARD_OUTPUT_STYLE
            is_text_to_video = is_h3 and not image_frames and not videos and not audios

            if is_h3:
                if not is_text_to_video:
                    validate_h3_references(len(image_frames), videos, audios)
                rule_content, selected_rule_name = cls._resolve_rule_content(
                    rule,
                    False,
                    "",
                )
                mode_name = "T2VA" if is_text_to_video else "Ref2VA"
                rule_name = f"{selected_rule_name} / {mode_name}"
            else:
                if videos or audios:
                    raise ValueError(
                        f"Video/audio references require output style '{H3_OUTPUT_STYLE}'."
                    )
                if (
                    not image_frames
                    and not fusion_description
                    and not reference_prompt_content.strip()
                ):
                    raise ValueError(
                        "Standard output styles need at least one image, a fusion description, "
                        "or connected reference prompt content."
                    )
                rule_content, rule_name = cls._resolve_rule_content(
                    rule,
                    custom_rule,
                    custom_rule_content,
                )

            service_id, model_name = cls.parse_service_model(vlm_service)
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
                "max_tokens": max(
                    int(target_model.get("max_tokens", 1000) or 1000),
                    5000 if is_storyboard else (2600 if is_h3 else 1200),
                ),
                "top_p": target_model.get("top_p", 0.9),
            }
            if service.get("type") == "ollama":
                provider_config["auto_unload"] = ollama_auto_unload

            if not provider_config.get("model"):
                raise ValueError(f"Please configure model for {vlm_service}")
            if cls._service_requires_api_key(service) and not provider_config.get("api_key"):
                raise ValueError(f"Please configure API key and model for {vlm_service}")

            model_image_limit = get_model_max_images(provider_config.get("model"))
            if is_h3:
                if is_text_to_video:
                    images_data = []
                    payload_labels = []
                    preview_tensor = torch.zeros((1, 1, 1, 3), dtype=torch.float32)
                else:
                    max_payload_images = max(1, min(model_image_limit, 32))
                    payload_tensor, payload_labels = build_visual_payload(
                        image_frames,
                        videos,
                        max_payload_images=max_payload_images,
                        frames_per_video=video_frames_per_ref,
                    )
                    images_data, preview_tensor = cls._tensor_to_data_urls(
                        payload_tensor, max_images=payload_tensor.shape[0]
                    )
                prompt_to_send = cls._build_h3_prompt(
                    rule_content=rule_content,
                    fusion_description=fusion_description,
                    image_count=len(image_frames),
                    video_count=len(videos),
                    audio_count=len(audios),
                    payload_labels=payload_labels,
                    additional_rule=custom_rule_content if custom_rule else "",
                    reference_prompt_content=reference_prompt_content,
                )
                if is_text_to_video:
                    reference_manifest = (
                        "MiniMax H3 T2V: no image, video, or audio references.\n"
                        f"User intent: {fusion_description or '(empty)'}"
                    )
                else:
                    reference_manifest = build_h3_reference_manifest(
                        len(image_frames), videos, audios, payload_labels, fusion_description
                    )
            else:
                if image_frames:
                    batch = normalize_frames(image_frames)
                    max_images = max(1, min(model_image_limit, 8))
                    images_data, preview_tensor = cls._tensor_to_data_urls(
                        batch, max_images=max_images
                    )
                else:
                    images_data = []
                    preview_tensor = torch.zeros((1, 1, 1, 3), dtype=torch.float32)
                prompt_to_send = cls._build_prompt(
                    rule_content=rule_content,
                    fusion_description=fusion_description,
                    image_count=len(images_data),
                    output_style=output_style,
                    reference_prompt_content=reference_prompt_content,
                )
                reference_manifest = cls._build_image_roles_text(
                    len(images_data), fusion_description
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
                {
                    "图片参考": len(image_frames),
                    "视频参考": len(videos),
                    "音频参考": len(audios),
                    "VLM视觉载荷": len(images_data),
                    "输出风格": output_style,
                    "生成模式": "T2V" if is_text_to_video else "媒体参考融合",
                    "自备提示词参考": "已连接" if reference_prompt_content.strip() else "未连接",
                },
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
                fusion_prompt = (
                    sanitize_h3_prompt(
                        fusion_prompt,
                        image_count=len(image_frames),
                        video_count=len(videos),
                        audio_count=len(audios),
                    )
                    if is_h3
                    else cls._sanitize_fusion_prompt(fusion_prompt)
                )
                if not fusion_prompt:
                    error_msg = "API returned empty result"
                    log_error(TASK_MULTI_IMAGE_FUSION, request_id, error_msg, source=SOURCE_NODE)
                    raise RuntimeError(f"Fusion failed: {error_msg}")

                return io.NodeOutput(fusion_prompt, reference_manifest, preview_tensor)

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
            error_msg = str(e) if isinstance(e, ValueError) else format_api_error(e, vlm_service)
            log_error(TASK_MULTI_IMAGE_FUSION, request_id, error_msg, source=SOURCE_NODE)
            raise RuntimeError(f"Fusion error: {error_msg}")
