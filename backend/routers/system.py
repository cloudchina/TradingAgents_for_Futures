"""系统状态 API 路由"""
from fastapi import APIRouter
from typing import Dict, Any, List
import os

from core.settings import settings
from services.cache_service import cache_service
from services.commodity_service import commodity_service
from services.scheduled_service import get_scheduled_runner
from services.word_service import word_report_service

router = APIRouter(prefix="/api/system", tags=["系统状态"])


@router.get("/status")
async def get_system_status() -> Dict[str, Any]:
    """获取系统状态"""
    runner = get_scheduled_runner()
    # 🔧 修复(B4)：品种数量统一取自 commodities.yaml（59），
    # 此前 settings.SUPPORTED_COMMODITIES 硬编码 36 个，与 /api/data 数据品种不一致。
    configured_symbols = commodity_service.get_symbols()
    return {
        "bailian_api_configured": bool(settings.DASHSCOPE_API_KEY and settings.DASHSCOPE_API_KEY != "your_dashscope_api_key_here"),
        "serper_api_configured": bool(settings.SERPER_API_KEY and settings.SERPER_API_KEY != "your_serper_api_key_here"),
        "docx_available": word_report_service.available,
        "cache_count": cache_service.get_cache_count(),
        "scheduler_running": runner.is_running() if runner else False,
        "scheduler_config": runner.config.model_dump() if runner else None,
        "supported_commodities": len(configured_symbols),
        "commodity_symbols": configured_symbols,
    }


@router.get("/commodities")
async def get_commodities() -> Dict[str, Any]:
    """获取支持的品种列表（统一以 commodities.yaml 为准）"""
    commodities = commodity_service.get_all_commodities()
    return {
        "commodities": commodities,
        "symbols": [c["symbol"] for c in commodities],
        "count": len(commodities),
    }


@router.get("/health")
async def health_check() -> Dict[str, str]:
    """健康检查"""
    return {"status": "ok"}
