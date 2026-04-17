from __future__ import annotations

import os


class VLMConfig:
    """视觉语言模型配置类（OpenAI 兼容接口）"""

    DEFAULT_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    DEFAULT_MODEL = "qwen-vl-plus"
    DEFAULT_MAX_TOKENS = 500
    DEFAULT_TIMEOUT = 120

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ):
        """
        初始化 VLM 配置

        Args:
            api_key: API 密钥，默认从环境变量 VLM_API_KEY 读取
            api_url: API 端点 URL，默认从环境变量 VLM_API_URL 读取
            model: 模型名称，默认从环境变量 VLM_MODEL 读取
            max_tokens: 最大 token 数，默认从环境变量 VLM_MAX_TOKENS 读取
            timeout: 请求超时时间（秒），默认从环境变量 VLM_TIMEOUT 读取
        """
        self.api_key = api_key or os.getenv("VLM_API_KEY", "")
        self.api_url = api_url or os.getenv("VLM_API_URL", self.DEFAULT_API_URL)
        self.model = model or os.getenv("VLM_MODEL", self.DEFAULT_MODEL)

        env_max_tokens = int(os.getenv("VLM_MAX_TOKENS", str(self.DEFAULT_MAX_TOKENS)))
        env_timeout = int(os.getenv("VLM_TIMEOUT", str(self.DEFAULT_TIMEOUT)))

        # 显式传入的 0 也应被尊重，因此使用 is not None 判断
        self.max_tokens = max_tokens if max_tokens is not None else env_max_tokens
        self.timeout = timeout if timeout is not None else env_timeout

    def validate(self) -> tuple[bool, str]:
        """
        验证配置是否完整

        Returns:
            (是否有效, 错误信息)
        """
        if not self.api_key:
            return False, "API key is missing"
        if not self.api_url:
            return False, "API URL is missing"
        if not self.model:
            return False, "Model name is missing"
        return True, ""

    def to_dict(self) -> dict:
        """导出为字典（不包含 api_key，避免泄漏）"""
        return {
            "api_url": self.api_url,
            "model": self.model,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
        }


# 全局默认配置实例
default_vlm_config = VLMConfig()
