"""
图像反推（图片反推提示词）节点 - V3 版本

V3 迁移说明：
    - 继承 VLMNodeBase + io.ComfyNode
    - INPUT_TYPES → define_schema()
    - IS_CHANGED → fingerprint_inputs()
    - def analyze_image(self, ...) → @classmethod execute(cls, ...)
"""

from comfy.model_management import InterruptProcessingException
from comfy_api.latest import io

from ..services.vlm import VisionService
from ..utils.common import (
    format_api_error, format_model_with_thinking, generate_request_id,
    log_prepare, log_error, TASK_IMAGE_CAPTION, SOURCE_NODE
)
from ..services.thinking_control import build_thinking_suppression
from .base import VLMNodeBase


class ImageCaptionNode(VLMNodeBase, io.ComfyNode):
    """图像反推节点（V3）"""

    @classmethod
    def define_schema(cls):
        # 动态获取服务列表
        service_options = cls.get_vlm_service_options()
        default_service = service_options[0] if service_options else "智谱"

        # 获取模板
        from ..config_manager import config_manager
        system_prompts = config_manager.get_system_prompts()
        
        vision_prompts = {}
        active_vision_id = None
        if system_prompts:
            vision_prompts = system_prompts.get('vision_prompts', {}) or {}
            active_vision_id = system_prompts.get('active_prompts', {}).get('vision')

        prompt_template_options = []
        id_to_display_name = {}
        for key, value in vision_prompts.items():
            show_in = value.get('showIn', ["frontend", "node"])
            if 'node' not in show_in:
                continue
            name = value.get('name', key)
            category = value.get('category', '')
            display_name = f"{category}/{name}" if category else name
            id_to_display_name[key] = display_name
            prompt_template_options.append(display_name)

        default_template_name = prompt_template_options[0] if prompt_template_options else "反推-自然语言"
        if active_vision_id and active_vision_id in id_to_display_name:
            default_template_name = id_to_display_name[active_vision_id]
            
        if not prompt_template_options:
            prompt_template_options = ["反推-自然语言"]

        return io.Schema(
            node_id="ImageCaptionNode",
            display_name="✨Image Caption (VLM)",
            category="✨Prompt Assistant",
            description="Extract text prompt from image using Vision-Language Models",
            inputs=[
                io.Image.Input("image", tooltip="The image to analyze"),
                io.Combo.Input(
                    "rule",
                    options=prompt_template_options,
                    default=default_template_name,
                    tooltip="Choose a preset rule for analysis"
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
        image=None, rule=None, custom_rule=None, custom_rule_content=None,
        user_prompt=None, vlm_service=None, ollama_auto_unload=None, seed=None
    ):
        """替代 V1 IS_CHANGED"""
        import hashlib
        temp_rule_hash = hashlib.md5((custom_rule_content or "").encode('utf-8')).hexdigest()
        user_hint_hash = hashlib.md5((user_prompt or "").encode('utf-8')).hexdigest()
        img_hash = cls._compute_image_hash(image)

        return hash((
            img_hash,
            rule,
            bool(custom_rule),
            temp_rule_hash,
            user_hint_hash,
            vlm_service,
            bool(ollama_auto_unload),
            seed
        ))

    @classmethod
    def execute(
        cls,
        image, rule, custom_rule, custom_rule_content, 
        user_prompt, vlm_service, ollama_auto_unload, seed=None
    ):
        unique_id = cls.hidden.unique_id
        request_id = None

        try:
            if image is None:
                raise ValueError("No image provided. Please connect an image to the 'image' input.")

            system_message = None
            rule_name = "Custom Rule" if (custom_rule and custom_rule_content) else rule

            if custom_rule and custom_rule_content:
                system_message = {"role": "system", "content": custom_rule_content}
            else:
                from ..config_manager import config_manager
                system_prompts = config_manager.get_system_prompts()
                vision_prompts = system_prompts.get('vision_prompts', {}) if system_prompts else {}

                template_found = False
                for key, value in vision_prompts.items():
                    name = value.get('name', key)
                    category = value.get('category', '')
                    display_name = f"{category}/{name}" if category else name
                    if display_name == rule or value.get('name') == rule or key == rule:
                        system_message = {"role": value.get('role', 'system'), "content": value.get('content', '')}
                        template_found = True
                        break
                
                if not template_found or not system_message or not system_message.get('content'):
                    system_message = {"role": "system", "content": "请详细描述这张图片的内容"}
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

            request_id = generate_request_id("vlm", None, unique_id)
            base64_image = cls._image_to_base64(image)

            model_full_name = provider_config.get('model')
            disable_thinking_enabled = service.get('disable_thinking', True)
            thinking_extra = build_thinking_suppression(service_id, model_full_name) if disable_thinking_enabled else None
            model_display = format_model_with_thinking(model_full_name, bool(thinking_extra))
            service_display_name = service.get('name', service_id)

            log_prepare(TASK_IMAGE_CAPTION, request_id, SOURCE_NODE, service_display_name, model_display, rule_name)

            if not provider_config.get('api_key', '') or not provider_config.get('model', ''):
                raise ValueError(f"Please configure API key and model for {vlm_service}")

            result = cls._run_vision_task(
                VisionService.analyze_image,
                service_id,
                base64_image=base64_image,
                user_prompt=user_prompt,
                request_id=request_id,
                custom_provider=service_id,
                custom_provider_config=provider_config,
                system_message_override=system_message,
                source=SOURCE_NODE
            )

            if result and result.get('success'):
                caption_text = result.get('data', {}).get('caption', '').strip()
                if not caption_text:
                    error_msg = 'API returned empty result'
                    log_error(TASK_IMAGE_CAPTION, request_id, error_msg, source=SOURCE_NODE)
                    raise RuntimeError(f"Analysis failed: {error_msg}")
                return io.NodeOutput(caption_text)
            else:
                error_msg = result.get('error', 'Unknown error') if result else 'No result returned'
                if error_msg == "任务被中断":
                    raise InterruptProcessingException()
                log_error(TASK_IMAGE_CAPTION, request_id, error_msg, source=SOURCE_NODE)
                raise RuntimeError(f"Analysis failed: {error_msg}")

        except InterruptProcessingException:
            raise
        except Exception as e:
            error_msg = format_api_error(e, vlm_service)
            log_error(TASK_IMAGE_CAPTION, request_id, error_msg, source=SOURCE_NODE)
            raise RuntimeError(f"Analysis error: {error_msg}")