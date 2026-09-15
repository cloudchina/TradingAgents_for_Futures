"""LLM 运行时配置（前端可改，保存后立即生效，无需重启服务）

配置持久化到一个 JSON 文件，路径按部署模式自动推导：

- 直启模式（`npm start` / `uvicorn main:app`）：
  `backend/data/config/llm_config.json`（由 .env 的 DATA_ROOT_DIR=./data/qihuo/database 推导）
- Docker 模式（`docker compose up`）：
  `/app/data/config/llm_config.json`（/app/data 是命名卷 futures-data，容器重建不丢失）

也可用环境变量 `LLM_CONFIG_PATH` 显式指定路径。

优先级：配置文件 > 环境变量（DASHSCOPE_API_KEY / BAILIAN_BASE_URL / LLM_MODEL）。
配置文件不存在时，回退到环境变量并沿用其取值。
"""
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger
from pydantic import BaseModel

from core.settings import settings


# 被视为"未配置"的占位符（与 .env.example 中的示例值保持一致）
_PLACEHOLDER_KEYS = {
    "",
    "your_dashscope_api_key_here",
    "your_api_key_here",
    "your-api-key",
    "sk-xxx",
}

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"
CONFIG_FILENAME = "llm_config.json"


def _default_config_path() -> Path:
    """推导 LLM 运行时配置文件路径。"""
    if settings.LLM_CONFIG_PATH:
        return Path(settings.LLM_CONFIG_PATH).expanduser()
    # <DATA_ROOT_DIR>/../../config/llm_config.json
    #   直启   : ./data/qihuo/database -> ./data/config
    #   Docker : /app/data/qihuo/database -> /app/data/config
    return Path(settings.DATA_ROOT_DIR).resolve().parent.parent / "config" / CONFIG_FILENAME


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return key[0] + "*" * (len(key) - 1)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


class LLMRuntimeConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class LLMConfigManager:
    """LLM 运行时配置管理器（线程安全，单例使用）"""

    def __init__(self):
        self._lock = threading.RLock()
        self._path: Path = _default_config_path()
        self._cfg: Optional[LLMRuntimeConfig] = None
        self._from_file: bool = False

    # ---------------- 基础读写 ----------------
    @property
    def config_path(self) -> Path:
        return self._path

    @staticmethod
    def _from_env() -> LLMRuntimeConfig:
        """从环境变量/settings 构造默认配置。"""
        key = (settings.DASHSCOPE_API_KEY or "").strip()
        return LLMRuntimeConfig(
            base_url=(settings.BAILIAN_BASE_URL or "").strip() or DEFAULT_BASE_URL,
            api_key="" if key in _PLACEHOLDER_KEYS else key,
            model=(settings.LLM_MODEL or "").strip() or DEFAULT_MODEL,
        )

    def _load_locked(self) -> LLMRuntimeConfig:
        if self._cfg is not None:
            return self._cfg

        cfg: Optional[LLMRuntimeConfig] = None
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    cfg = LLMRuntimeConfig(
                        base_url=str(raw.get("base_url") or "").strip(),
                        api_key=str(raw.get("api_key") or "").strip(),
                        model=str(raw.get("model") or "").strip(),
                    )
                    self._from_file = True
        except Exception as e:  # noqa: BLE001 - 配置损坏不应让服务起不来
            logger.warning(f"读取 LLM 配置文件失败（{self._path}）：{e}，回退到环境变量配置")

        if cfg is None:
            cfg = self._from_env()
            self._from_file = False

        self._cfg = cfg
        return cfg

    def _persist_locked(self) -> None:
        """落盘。异常向上抛，由路由层转成 HTTP 500。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._cfg.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"写入 LLM 配置文件失败（{self._path}）：{e}")
            raise RuntimeError(f"写入 LLM 配置文件失败：{e}")

    def get(self) -> LLMRuntimeConfig:
        """获取当前生效配置（副本）。"""
        with self._lock:
            return self._load_locked().model_copy(deep=True)

    def update(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> LLMRuntimeConfig:
        """局部更新配置（None 表示保持不变），并立即落盘。"""
        with self._lock:
            cfg = self._load_locked()
            if base_url is not None:
                cfg.base_url = (base_url or "").strip()
            if api_key is not None:
                cfg.api_key = (api_key or "").strip()
            if model is not None:
                cfg.model = (model or "").strip()
            self._cfg = cfg
            self._from_file = True
            self._persist_locked()
            updated = cfg.model_copy(deep=True)

        logger.info(
            f"LLM 配置已更新: base_url={updated.base_url}, model={updated.model}, "
            f"api_key={'已设置' if updated.api_key else '未设置'}"
        )
        return updated

    def reset(self) -> LLMRuntimeConfig:
        """删除配置文件，回退到环境变量配置。"""
        with self._lock:
            try:
                if self._path.exists():
                    self._path.unlink()
            except Exception as e:  # noqa: BLE001
                logger.error(f"删除 LLM 配置文件失败（{self._path}）：{e}")
                raise RuntimeError(f"删除 LLM 配置文件失败：{e}")
            self._cfg = None
            self._from_file = False
            cfg = self._load_locked().model_copy(deep=True)
        logger.info("LLM 配置已恢复为环境变量默认值")
        return cfg

    # ---------------- 查询辅助 ----------------
    def is_configured(self) -> bool:
        return bool(self.get().api_key.strip())

    def resolve_model(self, model: Optional[str] = None) -> str:
        """模型优先级：显式传入 > 运行时配置 > 兜底默认值。"""
        return (model or "").strip() or (self.get().model or "").strip() or DEFAULT_MODEL

    def public_dict(self) -> Dict[str, Any]:
        """返回给前端的配置视图（含脱敏信息）。"""
        cfg = self.get()
        updated_at = ""
        try:
            if self._path.exists():
                updated_at = datetime.fromtimestamp(self._path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            pass

        return {
            "base_url": cfg.base_url,
            "api_key": cfg.api_key,
            "api_key_masked": _mask(cfg.api_key),
            "model": cfg.model,
            "configured": bool(cfg.api_key.strip()),
            "source": "file" if self._from_file else "env",
            "config_path": str(self._path),
            "updated_at": updated_at,
            "default_base_url": DEFAULT_BASE_URL,
            "default_model": DEFAULT_MODEL,
        }

    def describe(self) -> str:
        cfg = self.get()
        return (
            f"base_url={cfg.base_url}, model={cfg.model}, "
            f"api_key={'已配置' if cfg.api_key else '未配置'}"
        )


# 全局单例
llm_config = LLMConfigManager()
