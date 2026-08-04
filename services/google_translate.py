"""Google Cloud Translation Basic (v2) service."""

from __future__ import annotations

import asyncio
import html
import time
from typing import Any, Dict, Optional

import httpx

from ..utils.common import (
    ERROR_PREFIX,
    ProgressBar,
    TASK_TRANSLATE,
    WARN_PREFIX,
)
from .baidu import BaiduTranslateService
from .core import HTTPClientPool


class GoogleTranslateService:
    """Translate text through the official Google Cloud Translation v2 REST API."""

    API_URL = "https://translation.googleapis.com/language/translate/v2"

    @staticmethod
    async def translate(
        text: str,
        from_lang: str = "auto",
        to_lang: str = "zh",
        request_id: Optional[str] = None,
        is_auto: bool = False,
        cancel_event: Optional[Any] = None,
        task_type: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        request_id = request_id or f"google_trans_{int(time.time())}"
        pbar = None
        try:
            if not text or not text.strip():
                return {"success": False, "error": "Google: 待翻译文本不能为空"}

            from ..config_manager import config_manager

            config = config_manager.get_google_translate_config()
            api_key = config.get("api_key") or ""
            if not api_key:
                return {
                    "success": False,
                    "error": "Google: 请先配置 Google Cloud Translation API Key",
                }

            chunks = BaiduTranslateService.split_text_by_paragraphs(text, max_length=4000) or [text]
            client = HTTPClientPool.get_client(
                provider="google_translate",
                base_url=GoogleTranslateService.API_URL,
                timeout=20.0,
            )
            pbar = ProgressBar(
                request_id=request_id,
                service_name="Google翻译",
                streaming=False,
                extra_info=f"长度:{len(text)}",
                task_type=task_type or TASK_TRANSLATE,
                source=source,
            )

            translated_parts = []
            start_time = time.perf_counter()
            for chunk in chunks:
                if cancel_event is not None and cancel_event.is_set():
                    pbar.cancel(f"{WARN_PREFIX} 任务被中断 | 服务:Google翻译")
                    return {"success": False, "error": "任务被中断", "interrupted": True}

                payload = {"q": chunk, "target": to_lang, "format": "text"}
                if from_lang and from_lang != "auto":
                    payload["source"] = from_lang
                response = await client.post(
                    GoogleTranslateService.API_URL,
                    params={"key": api_key},
                    json=payload,
                )
                try:
                    result = response.json()
                except ValueError:
                    result = {}
                if response.status_code >= 400 or result.get("error"):
                    error = result.get("error", {})
                    message = error.get("message") if isinstance(error, dict) else None
                    raise RuntimeError(message or f"HTTP {response.status_code}")

                translations = (result.get("data") or {}).get("translations") or []
                if not translations or not translations[0].get("translatedText"):
                    raise RuntimeError("Google API 返回空结果")
                translated_parts.append(html.unescape(translations[0]["translatedText"]))

            translated_text = "\n".join(translated_parts)
            pbar.done(
                char_count=len(translated_text),
                elapsed_ms=int((time.perf_counter() - start_time) * 1000),
            )
            return {
                "success": True,
                "data": {
                    "translated": translated_text,
                    "from": from_lang,
                    "to": to_lang,
                    "original": text,
                },
            }
        except asyncio.CancelledError:
            if pbar:
                pbar.cancel(f"{WARN_PREFIX} 任务被取消 | 服务:Google翻译")
            return {"success": False, "error": "任务被取消", "interrupted": True}
        except (httpx.HTTPError, asyncio.TimeoutError) as error:
            if pbar:
                pbar.error(f"Google: 网络请求失败 ({type(error).__name__})")
            return {"success": False, "error": f"Google: 网络请求失败，请检查网络连接 ({type(error).__name__})"}
        except Exception as error:
            if pbar:
                pbar.error(str(error))
            print(f"{ERROR_PREFIX} Google翻译请求异常 | 错误:{error}")
            return {"success": False, "error": f"Google: {error}"}

    @staticmethod
    async def batch_translate(texts, from_lang="auto", to_lang="zh"):
        return await asyncio.gather(
            *(
                GoogleTranslateService.translate(text, from_lang, to_lang)
                for text in texts
            )
        )
