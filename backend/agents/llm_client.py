"""LLM 客户端（OpenAI 兼容协议 / 同步）

设计要点：
1. 懒加载 + 配置热更新。客户端不再在 import 时固定创建，而是按需按当前
   LLM 运行时配置（core.llm_config）创建；base_url / api_key 变化后自动重建，
   因此前端「LLM 配置」页面保存后无需重启服务即可生效。
2. 保持同步模型：analysis_service 用 threading.Thread 跑分析，
   引入 asyncio 会复杂化，故用同步 OpenAI 客户端。
"""
import time
import threading
from typing import List, Optional, Dict, Any, Tuple

from openai import OpenAI
from loguru import logger

from core.llm_config import llm_config, DEFAULT_BASE_URL, DEFAULT_MODEL


class LLMClient:
    """OpenAI 兼容客户端（懒加载，配置变更自动重建）"""

    def __init__(self):
        self._lock = threading.RLock()
        self._client: Optional[OpenAI] = None
        self._signature: Optional[Tuple[str, str]] = None

    # ---------------- 客户端管理 ----------------
    def _get_client(self) -> OpenAI:
        cfg = llm_config.get()
        base_url = (cfg.base_url or "").strip() or DEFAULT_BASE_URL
        api_key = cfg.api_key or "empty"
        signature = (base_url, api_key)

        with self._lock:
            if self._client is None or signature != self._signature:
                if not cfg.api_key:
                    logger.warning("LLM API Key 未配置，调用将失败（可在前端「LLM 配置」页面设置）")
                self._client = OpenAI(base_url=base_url, api_key=api_key)
                self._signature = signature
                logger.info(f"LLM 客户端已就绪: base_url={base_url}, model={cfg.model or DEFAULT_MODEL}")
            return self._client

    @property
    def client(self) -> OpenAI:
        return self._get_client()

    def reload(self) -> None:
        """丢弃当前客户端，下次调用时按最新配置重建。"""
        with self._lock:
            self._client = None
            self._signature = None
        logger.info("LLM 客户端已重置，将在下次调用时按最新配置重建")

    def resolve_model(self, model: Optional[str] = None) -> str:
        return llm_config.resolve_model(model)

    # ---------------- 对话 ----------------
    def chat(
        self,
        model: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ):
        """同步调用 chat.completions.create。

        返回 resp.choices[0].message（含 content / tool_calls 字段）。
        出错时抛异常，由调用方处理。
        """
        model = self.resolve_model(model)
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages or [],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = self._get_client().chat.completions.create(**kwargs)
        return resp.choices[0].message

    def list_models(self) -> List[str]:
        """列出服务端可用模型 ID（失败时抛异常）。"""
        models = self._get_client().models.list()
        return sorted(str(m.id) for m in (getattr(models, "data", None) or []))


# 模块级单例：所有 agent 共用同一个 client
llm_client = LLMClient()


def build_client(base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 60.0) -> OpenAI:
    """按指定参数构建一个临时客户端（用于连通性测试/拉取模型列表，不复用单例）。"""
    cfg = llm_config.get()
    return OpenAI(
        base_url=(base_url or "").strip() or (cfg.base_url or "").strip() or DEFAULT_BASE_URL,
        api_key=(api_key or "").strip() or cfg.api_key or "empty",
        timeout=timeout,
        max_retries=0,
    )


def probe_connection(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """测试 LLM 连通性。

    先尝试 /models 列表（开销小），失败再退化为一次极短的对话请求，
    以兼容未实现 /models 的第三方 OpenAI 兼容服务。
    """
    cfg = llm_config.get()
    use_base_url = (base_url or "").strip() or cfg.base_url
    use_api_key = (api_key or "").strip() or cfg.api_key
    use_model = (model or "").strip() or cfg.model or DEFAULT_MODEL

    if not use_api_key:
        return {"ok": False, "message": "未配置 API Key", "latency_ms": 0, "models": []}

    client = build_client(use_base_url, use_api_key, timeout=timeout)
    started = time.time()

    try:
        models = client.models.list()
        ids = sorted(str(m.id) for m in (getattr(models, "data", None) or []))
        return {
            "ok": True,
            "message": f"连接成功，可用模型 {len(ids)} 个",
            "latency_ms": int((time.time() - started) * 1000),
            "models": ids,
            "checked_by": "models.list",
        }
    except Exception as list_err:  # noqa: BLE001
        try:
            resp = client.chat.completions.create(
                model=use_model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=8,
            )
            content = (resp.choices[0].message.content or "").strip()
            return {
                "ok": True,
                "message": f"连接成功（模型 {use_model} 可用" + (f"，回复：{content[:20]}）" if content else "）"),
                "latency_ms": int((time.time() - started) * 1000),
                "models": [],
                "checked_by": "chat.completions",
            }
        except Exception as chat_err:  # noqa: BLE001
            return {
                "ok": False,
                "message": f"连接失败：{chat_err}（模型列表接口同样失败：{list_err}）",
                "latency_ms": int((time.time() - started) * 1000),
                "models": [],
                "checked_by": "chat.completions",
            }


def fetch_models(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """拉取服务端模型列表。"""
    use_api_key = (api_key or "").strip() or llm_config.get().api_key
    if not use_api_key:
        return {"ok": False, "message": "未配置 API Key", "models": []}
    try:
        models = build_client(base_url, use_api_key, timeout=timeout).models.list()
        ids = sorted(str(m.id) for m in (getattr(models, "data", None) or []))
        return {"ok": True, "message": f"已获取 {len(ids)} 个模型", "models": ids}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"获取模型列表失败：{e}", "models": []}
