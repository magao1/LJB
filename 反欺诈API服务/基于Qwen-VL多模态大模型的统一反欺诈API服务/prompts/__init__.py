"""Prompts 模块 - 通用加载器"""
from pathlib import Path


def load_prompt(prompt_name: str) -> str:
    """
    通用 Prompt 加载器，支持按国家子目录加载。

    Args:
        prompt_name: Prompt 名称（不含 .md 后缀），可含国家子路径。
            例如："kyc.photo.repaste.detect" 或 "in/kyc.photo.fraud.detect"

    Returns:
        Prompt 文本内容

    Raises:
        FileNotFoundError: Prompt 文件不存在
    """
    prompt_file = Path(__file__).parent / f"{prompt_name}.md"

    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt 文件不存在: {prompt_name}.md")

    with open(prompt_file, "r", encoding="utf-8") as f:
        return f.read()


__all__ = [
    "load_prompt",
]
