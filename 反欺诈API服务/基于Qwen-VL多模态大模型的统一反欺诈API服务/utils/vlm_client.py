from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from config import VLMConfig, default_vlm_config


logger = logging.getLogger(__name__)


class VLMClient:
    """
    视觉语言模型客户端（支持 OpenAI 标准格式）

    特点：
    - 支持默认配置和请求级配置覆盖
    - 统一使用 OpenAI Chat Completions API 格式
    - 支持多模态输入（文本 + 图片）
    - 自动 JSON 解析与容错
    - 使用 httpx 异步 IO，不阻塞事件循环
    """

    def __init__(self, config: VLMConfig | None = None):
        self.config = config or default_vlm_config
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self, timeout: float) -> httpx.AsyncClient:
        """
        获取带连接池的 httpx.AsyncClient 实例
        """
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=timeout)
        return self._http_client

    async def aclose(self) -> None:
        """
        关闭底层 HTTP 客户端，供应用关闭时调用
        """
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def call_multimodal(
        self,
        prompt_text: str,
        images_b64: list[str],
        config_override: VLMConfig | None = None,
    ) -> dict[str, Any]:
        """
        调用多模态视觉语言模型（OpenAI 标准格式）

        Args:
            prompt_text: Prompt 文本
            images_b64: Base64 编码的图片列表
            config_override: 请求级配置覆盖（可选）

        Returns:
            解析后的 JSON 结果或错误信息
        """
        config = config_override or self.config

        is_valid, error_msg = config.validate()
        if not is_valid:
            return {"error": error_msg}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}"
        }

        content = [{"type": "text", "text": prompt_text}]
        for img_b64 in images_b64:
            if img_b64:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                })

        payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": config.max_tokens
        }

        image_count = len([img for img in images_b64 if img])
        prompt_preview = prompt_text[:120].replace("\n", " ")
        logger.debug(
            "VLM call | url=%s model=%s images=%d max_tokens=%d timeout=%ss | prompt_preview=%r",
            config.api_url, config.model, image_count, config.max_tokens, config.timeout,
            prompt_preview,
        )

        t0 = time.perf_counter()
        try:
            client = await self._get_client(float(config.timeout))
            resp = await client.post(config.api_url, headers=headers, json=payload)
        except httpx.TimeoutException:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.warning("VLM timeout after %.1fms (limit=%ss)", elapsed, config.timeout)
            return {"error": f"Request timeout after {config.timeout}s"}
        except httpx.RequestError as e:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.warning("VLM request error after %.1fms: %s", elapsed, e)
            return {"error": f"Request error: {str(e)}"}

        elapsed = (time.perf_counter() - t0) * 1000

        if resp.status_code != 200:
            logger.warning(
                "VLM upstream HTTP %s | %.1fms | body=%s",
                resp.status_code,
                elapsed,
                resp.text[:800],
            )
            return {
                "error": f"HTTP {resp.status_code}",
                "detail": "Upstream VLM request failed",
            }

        logger.debug("VLM response OK | %.1fms | body_len=%d", elapsed, len(resp.text))

        try:
            data = resp.json()
            content_text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            logger.warning(
                "Failed to parse VLM response: %s | raw=%s",
                str(e),
                resp.text[:800],
            )
            return {
                "error": "Failed to parse VLM response",
                "detail": str(e),
            }

        logger.debug("VLM content_text=%r", content_text[:300])
        result = self._extract_json(content_text)
        if "error" in result:
            logger.warning("VLM JSON extract failed | content_text=%r", content_text[:500])
        return result

    def _extract_json(self, text: str) -> dict[str, Any]:
        """
        从文本中提取 JSON（三层容错处理）
        """
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        return {"raw": text, "error": "Failed to extract JSON from response"}