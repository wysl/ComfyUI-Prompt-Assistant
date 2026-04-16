"""
视频反推（视频理解）节点 - V3 版本
"""

import os
import shutil
import tempfile
import hashlib
from typing import List, Tuple
from io import BytesIO

from PIL import Image as PILImage
import torch
import numpy as np

from comfy.model_management import InterruptProcessingException
from comfy_api.latest import io

from ..services.vlm import VisionService
from ..utils.common import (
    format_api_error, format_model_with_thinking, generate_request_id,
    log_prepare, log_error, TASK_VIDEO_CAPTION, SOURCE_NODE
)
from ..services.thinking_control import build_thinking_suppression
from .base import VLMNodeBase


class VideoCaptionNode(VLMNodeBase, io.ComfyNode):
    """视频反推节点（V3）"""
    
    @classmethod
    def define_schema(cls):
        service_options = cls.get_vlm_service_options()
        default_service = service_options[0] if service_options else "智谱"

        from ..config_manager import config_manager
        system_prompts = config_manager.get_system_prompts()
        
        video_prompts = {}
        active_video_id = None
        if system_prompts:
            video_prompts = system_prompts.get('video_prompts', {}) or {}
            active_video_id = system_prompts.get('active_prompts', {}).get('video')

        prompt_template_options = []
        id_to_display_name = {}
        for key, value in video_prompts.items():
            show_in = value.get('showIn', ["frontend", "node"])
            if 'node' not in show_in:
                continue
            name = value.get('name', key)
            category = value.get('category', '')
            display_name = f"{category}/{name}" if category else name
            id_to_display_name[key] = display_name
            prompt_template_options.append(display_name)

        default_template_name = prompt_template_options[0] if prompt_template_options else "视频-自然语言"
        if active_video_id and active_video_id in id_to_display_name:
            default_template_name = id_to_display_name[active_video_id]
            
        if not prompt_template_options:
            prompt_template_options = ["视频-自然语言"]

        return io.Schema(
            node_id="VideoCaptionNode",
            display_name="✨Video Caption (VLM)",
            category="✨Prompt Assistant",
            description="Extract text prompt from video frames using Vision-Language Models",
            inputs=[
                # ComfyUI 的 VIDEO 数据类型比较特殊，可能并不是直接的一个 tensor
                # 很多节点出来的视频类型是类似于 IMAGE batch（一堆图片）
                # 这里我们假设它是 IMAGE 类型，因为 ComfyUI 通常用批量 IMAGE 表示视频
                io.Image.Input("video_frames", tooltip="The video frames to analyze (IMAGE batch)"),
                io.Combo.Input(
                    "rule",
                    options=prompt_template_options,
                    default=default_template_name,
                ),
                io.Boolean.Input(
                    "custom_rule",
                    default=False,
                    label_on="Enable",
                    label_off="Disable"
                ),
                io.String.Input(
                    "custom_rule_content",
                    multiline=True,
                    default="",
                ),
                io.String.Input(
                    "user_prompt",
                    multiline=True,
                    default="",
                ),
                io.Int.Input(
                    "frame_count",
                    default=4,
                    min=1,
                    max=16,
                    step=1
                ),
                io.Combo.Input(
                    "vlm_service",
                    options=service_options,
                    default=default_service,
                ),
                io.Boolean.Input(
                    "ollama_auto_unload",
                    default=True,
                    label_on="Enable",
                    label_off="Disable"
                ),
                io.Int.Input(
                    "seed",
                    default=0,
                    min=0,
                    max=0xffffffffffffffff,
                    control_after_generate=True,
                ),
            ],
            outputs=[
                io.String.Output("caption_text"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(
        cls,
        video_frames=None, rule=None, custom_rule=None, custom_rule_content=None,
        user_prompt=None, frame_count=None, vlm_service=None, ollama_auto_unload=None, seed=None
    ):
        import hashlib
        temp_rule_hash = hashlib.md5((custom_rule_content or "").encode('utf-8')).hexdigest()
        user_hint_hash = hashlib.md5((user_prompt or "").encode('utf-8')).hexdigest()
        
        # 计算视频帧哈希
        video_hash = ""
        if video_frames is not None:
            shape_str = str(video_frames.shape)
            hasher = hashlib.md5(shape_str.encode('utf-8'))
            if video_frames.numel() > 0:
                step = max(1, video_frames.shape[0] // min(4, video_frames.shape[0]))
                sample = video_frames[::step]
                hasher.update(str(int(sample.sum().item())).encode('utf-8'))
            video_hash = hasher.hexdigest()

        return hash((
            video_hash,
            rule,
            bool(custom_rule),
            temp_rule_hash,
            user_hint_hash,
            frame_count,
            vlm_service,
            bool(ollama_auto_unload),
            seed
        ))

    @classmethod
    def _extract_frames(cls, video_tensor: torch.Tensor, target_count: int) -> List[str]:
        """按时间均匀抽取视频帧并转为 base64"""
        total_frames = video_tensor.shape[0]
        if total_frames == 0:
            return []

        if target_count >= total_frames:
            indices = list(range(total_frames))
        else:
            indices = np.linspace(0, total_frames - 1, target_count, dtype=int).tolist()

        base64_frames = []
        for idx in indices:
            frame_tensor = video_tensor[idx:idx+1]
            b64 = cls._image_to_base64(frame_tensor, quality=85)
            base64_frames.append(b64)

        return base64_frames

    @classmethod
    def execute(
        cls,
        video_frames, rule, custom_rule, custom_rule_content, 
        user_prompt, frame_count, vlm_service, ollama_auto_unload, seed=None
    ):
        unique_id = cls.hidden.unique_id
        request_id = None

        try:
            if video_frames is None or len(video_frames) == 0:
                raise ValueError("No video frames provided.")

            system_message = None
            rule_name = "Custom Rule" if (custom_rule and custom_rule_content) else rule

            if custom_rule and custom_rule_content:
                system_message = {"role": "system", "content": custom_rule_content}
            else:
                from ..config_manager import config_manager
                system_prompts = config_manager.get_system_prompts()
                video_prompts = system_prompts.get('video_prompts', {}) if system_prompts else {}

                template_found = False
                for key, value in video_prompts.items():
                    name = value.get('name', key)
                    category = value.get('category', '')
                    display_name = f"{category}/{name}" if category else name
                    if display_name == rule or value.get('name') == rule or key == rule:
                        system_message = {"role": value.get('role', 'system'), "content": value.get('content', '')}
                        template_found = True
                        break
                
                if not template_found or not system_message or not system_message.get('content'):
                    system_message = {"role": "system", "content": "请分析这些视频抽帧图片"}
                    rule_name = "Default Rule"

            service_id, model_name = cls.parse_service_model(vlm_service)
            if not service_id:
                raise ValueError(f"Invalid service selection: {vlm_service}")

            from ..config_manager import config_manager
            service = config_manager.get_service(service_id)
            if not service:
                raise ValueError(f"Service config not found: {vlm_service}")

            vlm_models = service.get('vlm_models', [])
            target_model = None

            if model_name:
                target_model = next((m for m in vlm_models if m.get('name') == model_name), None)

            if not target_model:
                target_model = next((m for m in vlm_models if m.get('is_default')), vlm_models[0] if vlm_models else None)

            if not target_model:
                raise ValueError(f"Service {vlm_service} has no available vision models")

            provider_config = {
                'provider': service_id,
                'model': target_model.get('name', ''),
                'base_url': service.get('base_url', ''),
                'api_key': service.get('api_key', ''),
                'temperature': target_model.get('temperature', 0.7),
                'max_tokens': target_model.get('max_tokens', 1000),
                'top_p': target_model.get('top_p', 0.9),
            }

            if service.get('type') == 'ollama':
                provider_config['auto_unload'] = ollama_auto_unload

            request_id = generate_request_id("video", None, unique_id)
            
            # 抽帧
            base64_images = cls._extract_frames(video_frames, frame_count)

            model_full_name = provider_config.get('model')
            disable_thinking_enabled = service.get('disable_thinking', True)
            thinking_extra = build_thinking_suppression(service_id, model_full_name) if disable_thinking_enabled else None
            model_display = format_model_with_thinking(model_full_name, bool(thinking_extra))
            service_display_name = service.get('name', service_id)

            log_prepare(TASK_VIDEO_CAPTION, request_id, SOURCE_NODE, service_display_name, model_display, rule_name, {"帧数": len(base64_images)})

            if not provider_config.get('api_key', '') or not provider_config.get('model', ''):
                raise ValueError(f"Please configure API key and model for {vlm_service}")

            # 从 system_message 字典中提取纯文本内容
            system_text = system_message.get('content', '') if isinstance(system_message, dict) else str(system_message)
            prompt_to_send = f"{system_text}\n\n{user_prompt}".strip() if user_prompt else system_text

            result = cls._run_vision_task(
                VisionService.analyze_images,
                service_id,
                images_data=base64_images,
                prompt_content=prompt_to_send,
                request_id=request_id,
                custom_provider=service_id,
                custom_provider_config=provider_config,
                source=SOURCE_NODE
            )

            if result and result.get('success'):
                # V3 重构后返回的键名是 description 而不是 caption
                data = result.get('data', {})
                caption_text = data.get('description', data.get('caption', '')).strip()
                if not caption_text:
                    error_msg = 'API returned empty result'
                    log_error(TASK_VIDEO_CAPTION, request_id, error_msg, source=SOURCE_NODE)
                    raise RuntimeError(f"Analysis failed: {error_msg}")
                return io.NodeOutput(caption_text)
            else:
                error_msg = result.get('error', 'Unknown error') if result else 'No result returned'
                if error_msg == "任务被中断":
                    raise InterruptProcessingException()
                log_error(TASK_VIDEO_CAPTION, request_id, error_msg, source=SOURCE_NODE)
                raise RuntimeError(f"Analysis failed: {error_msg}")

        except InterruptProcessingException:
            raise
        except Exception as e:
            error_msg = format_api_error(e, vlm_service)
            log_error(TASK_VIDEO_CAPTION, request_id, error_msg, source=SOURCE_NODE)
            raise RuntimeError(f"Analysis error: {error_msg}")
