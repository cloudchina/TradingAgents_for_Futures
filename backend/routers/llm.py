"""LLM 运行时配置 API 路由

提供前端「LLM 配置」页面所需的全部接口：
    GET  /api/llm/config   查询当前生效配置
    PUT  /api/llm/config   保存配置（立即生效，无需重启）
    POST /api/llm/test     连通性测试（可用未保存的参数先试）
    POST /api/llm/models   拉取远端模型列表
    POST /api/llm/reset    恢复为环境变量默认值
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from agents.llm_client import llm_client, probe_connection, fetch_models
from core.llm_config import llm_config

router = APIRouter(prefix="/api/llm", tags=["LLM配置"])


class LLMConfigPayload(BaseModel):
    """配置更新/测试入参；None 表示不修改该项。"""
    base_url: Optional[str] = Field(default=None, description="OpenAI 兼容接口地址")
    api_key: Optional[str] = Field(default=None, description="API Key")
    model: Optional[str] = Field(default=None, description="模型名称")


class LLMTestPayload(LLMConfigPayload):
    timeout: float = Field(default=30.0, ge=1, le=120, description="超时秒数")


@router.get("/config")
async def get_llm_config() -> Dict[str, Any]:
    """获取当前生效的 LLM 配置"""
    return llm_config.public_dict()


@router.put("/config")
async def update_llm_config(payload: LLMConfigPayload) -> Dict[str, Any]:
    """保存 LLM 配置并立即生效（重建 LLM 客户端，无需重启服务）"""
    try:
        llm_config.update(
            base_url=payload.base_url,
            api_key=payload.api_key,
            model=payload.model,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"保存 LLM 配置失败：{e}")

    # 关键：丢弃旧客户端，下次调用按新配置重建
    llm_client.reload()
    return {
        "success": True,
        "message": "LLM 配置已保存并立即生效",
        "config": llm_config.public_dict(),
    }


@router.post("/test")
async def test_llm_connection(payload: Optional[LLMTestPayload] = None) -> Dict[str, Any]:
    """测试连通性。不传参则测当前已保存的配置；传参则测临时配置。"""
    payload = payload or LLMTestPayload()
    result = probe_connection(
        base_url=payload.base_url,
        api_key=payload.api_key,
        model=payload.model,
        timeout=payload.timeout,
    )
    logger.info(f"LLM 连通性测试: ok={result.get('ok')} - {result.get('message')}")
    return result


@router.post("/models")
async def list_llm_models(payload: Optional[LLMConfigPayload] = None) -> Dict[str, Any]:
    """拉取远端可用模型列表（便于在页面上选择/补全模型名称）"""
    payload = payload or LLMConfigPayload()
    return fetch_models(base_url=payload.base_url, api_key=payload.api_key)


@router.post("/reset")
async def reset_llm_config() -> Dict[str, Any]:
    """删除配置文件，恢复为环境变量（DASHSCOPE_API_KEY / BAILIAN_BASE_URL / LLM_MODEL）配置"""
    try:
        llm_config.reset()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"恢复默认 LLM 配置失败：{e}")

    llm_client.reload()
    return {
        "success": True,
        "message": "已恢复为环境变量默认配置",
        "config": llm_config.public_dict(),
    }
