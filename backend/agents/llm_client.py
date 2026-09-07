"""百炼 OpenAI 兼容客户端单例（同步）

百炼 compatible-mode 完全兼容 OpenAI Python SDK，
function-calling 协议开箱即用，无需手写 httpx 解析 tool_calls。

保持同步模型：analysis_service 用 threading.Thread 跑分析，
引入 asyncio 会复杂化，故用同步 OpenAI 客户端。
"""
from typing import List, Optional, Dict, Any

from openai import OpenAI
from loguru import logger

from core.settings import settings


class LLMClient:
    """百炼 OpenAI 兼容客户端单例（同步）"""

    def __init__(self):
        if not settings.DASHSCOPE_API_KEY:
            logger.warning("DASHSCOPE_API_KEY 未配置，LLM 调用将失败")
        self.client = OpenAI(
            base_url=settings.BAILIAN_BASE_URL,
            api_key=settings.DASHSCOPE_API_KEY or "empty",
        )

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ):
        """同步调用 chat.completions.create。

        返回 resp.choices[0].message（含 content / tool_calls 字段）。
        出错时抛异常，由调用方处理。
        """
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = self.client.chat.completions.create(**kwargs)
        return resp.choices[0].message


# 模块级单例：所有 agent 共用同一个 client
llm_client = LLMClient()
