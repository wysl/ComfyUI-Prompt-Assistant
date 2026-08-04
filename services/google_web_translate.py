"""Google web translation service (no API key required).

This uses the endpoint currently used by Google's web translator. It is not a
supported Cloud API and may be rate-limited or changed by Google at any time.
"""

from __future__ import annotations

import asyncio
import time
from html.parser import HTMLParser
from typing import Any, Dict, Optional

import httpx

from ..utils.common import ERROR_PREFIX, ProgressBar, TASK_TRANSLATE, WARN_PREFIX
from .baidu import BaiduTranslateService
from .core import HTTPClientPool


class _MobileResultParser(HTMLParser):
    """Read the translated text from Google Translate's lightweight page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        if self._depth:
            self._depth += 1
        elif "result-container" in classes:
            self._depth = 1

    def handle_endtag(self, tag):
        if self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if self._depth:
            self._parts.append(data)

    @property
    def result(self):
        return "".join(self._parts).strip()


class GoogleWebTranslateService:
    """Translate through Google's web translation endpoint without a key."""

    API_URL = "https://translate.googleapis.com/translate_a/single"
    MOBILE_URL = "https://translate.google.com/m"
    HEADERS = {
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Referer": "https://translate.google.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127.0.0.0 Safari/537.36",
    }

    @staticmethod
    def _parse_response(payload: Any) -> str:
        """Extract translated segments from the web endpoint's nested array."""
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
            raise RuntimeError("Google网页翻译返回格式无效")

        parts = []
        for segment in payload[0]:
            if isinstance(segment, list) and segment and isinstance(segment[0], str):
                parts.append(segment[0])
        translated = "".join(parts)
        if not translated:
            raise RuntimeError("Google网页翻译返回空结果")
        return translated

    @staticmethod
    def _parse_mobile_response(content: str) -> str:
        parser = _MobileResultParser()
        parser.feed(content or "")
        if not parser.result:
            raise RuntimeError("Google轻量网页翻译返回空结果")
        return parser.result

    @staticmethod
    def _mobile_language_code(language: str) -> str:
        return "zh-CN" if language == "zh" else language

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
        request_id = request_id or f"google_web_trans_{int(time.time())}"
        pbar = None
        try:
            if not text or not text.strip():
                return {"success": False, "error": "Google网页翻译: 待翻译文本不能为空"}

            # The lightweight page accepts at most 2048 characters, so chunks
            # stay below that limit for the automatic fallback path.
            chunks = BaiduTranslateService.split_text_by_paragraphs(text, max_length=1800) or [text]
            client = HTTPClientPool.get_client(
                provider="google_web_translate",
                base_url=GoogleWebTranslateService.API_URL,
                timeout=20.0,
                headers=GoogleWebTranslateService.HEADERS,
            )
            pbar = ProgressBar(
                request_id=request_id,
                service_name="Google网页翻译",
                streaming=False,
                extra_info=f"长度:{len(text)}",
                task_type=task_type or TASK_TRANSLATE,
                source=source,
            )

            translated_parts = []
            start_time = time.perf_counter()
            for index, chunk in enumerate(chunks):
                if cancel_event is not None and cancel_event.is_set():
                    pbar.cancel(f"{WARN_PREFIX} 任务被中断 | 服务:Google网页翻译")
                    return {"success": False, "error": "任务被中断", "interrupted": True}

                params = {
                    "client": "gtx",
                    "sl": from_lang or "auto",
                    "tl": to_lang or "zh",
                    "dt": "t",
                    "q": chunk,
                }
                try:
                    response = await client.get(GoogleWebTranslateService.API_URL, params=params)
                    if response.status_code >= 400:
                        raise RuntimeError(f"JSON endpoint HTTP {response.status_code}")
                    translated = GoogleWebTranslateService._parse_response(response.json())
                except (httpx.HTTPError, asyncio.TimeoutError, ValueError, RuntimeError):
                    mobile_params = {
                        "sl": GoogleWebTranslateService._mobile_language_code(from_lang or "auto"),
                        "tl": GoogleWebTranslateService._mobile_language_code(to_lang or "zh"),
                        "q": chunk,
                    }
                    response = await client.get(
                        GoogleWebTranslateService.MOBILE_URL,
                        params=mobile_params,
                    )
                    if response.status_code >= 400:
                        if response.status_code == 429:
                            raise RuntimeError("访问频率受限 (HTTP 429)，请稍后再试或改用 Cloud API")
                        raise RuntimeError(f"轻量网页 HTTP {response.status_code}")
                    translated = GoogleWebTranslateService._parse_mobile_response(response.text)
                translated_parts.append(translated)
                if index < len(chunks) - 1:
                    await asyncio.sleep(0.15)

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
                pbar.cancel(f"{WARN_PREFIX} 任务被取消 | 服务:Google网页翻译")
            return {"success": False, "error": "任务被取消", "interrupted": True}
        except (httpx.HTTPError, asyncio.TimeoutError) as error:
            if pbar:
                pbar.error(f"Google网页翻译: 网络请求失败 ({type(error).__name__})")
            return {
                "success": False,
                "error": f"Google网页翻译: 网络请求失败，请检查网络连接 ({type(error).__name__})",
            }
        except Exception as error:
            if pbar:
                pbar.error(str(error))
            print(f"{ERROR_PREFIX} Google网页翻译请求异常 | 错误:{error}")
            return {"success": False, "error": f"Google网页翻译: {error}"}

    @staticmethod
    async def batch_translate(texts, from_lang="auto", to_lang="zh"):
        return await asyncio.gather(
            *(GoogleWebTranslateService.translate(text, from_lang, to_lang) for text in texts)
        )
