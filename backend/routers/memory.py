"""记忆体系 API 路由 - 阶段0/1 骨架。

对应 memory-system-plan.md 第四章阶段 5 完整接口清单。当前阶段只实现：
- GET  /api/memory/health
- POST /api/memory/init              手动触发 migrate（启动时已自动调）
- GET  /api/memory/availability      数据可用性清单（阶段0交付物）
- POST /api/memory/availability/refresh  强制现场扫描并落盘

后续阶段（2/4/5）补全的接口以 TODO 注释占位，先占路由前缀，避免后续加路由时
路径参数冲突（【二评】已确认统一用 /api/memory/symbols/{symbol} 前缀）。
"""
from fastapi import APIRouter
from typing import Any, Dict

from loguru import logger

from services.memory_service import memory_service
from services.availability_service import availability_service

router = APIRouter(prefix="/api/memory", tags=["记忆体系"])


@router.get("/health")
async def memory_health() -> Dict[str, Any]:
    """记忆库健康检查：目录路径 + schema 版本。"""
    try:
        memory_service.init_if_needed()
    except Exception as e:
        logger.warning(f"memory_health: init 失败 {e}")
    return {
        "status": "ok",
        "memory_dir": str(memory_service._resolve_db_path().parent),
        "db_path": str(memory_service._resolve_db_path()),
        "schema_version": 1,
    }


@router.post("/init")
async def memory_init() -> Dict[str, Any]:
    """手动触发 schema migrate；正常启动时已自动调用。"""
    memory_service.init_if_needed()
    return {"status": "ok", "memory_dir": str(memory_service._resolve_db_path().parent)}


@router.get("/availability")
async def get_availability() -> Dict[str, Any]:
    """获取数据可用性清单（优先读缓存 JSON）。"""
    return availability_service.get_or_scan(force_refresh=False)


@router.post("/availability/refresh")
async def refresh_availability() -> Dict[str, Any]:
    """强制重新扫描全部品种并落盘。"""
    return availability_service.get_or_scan(force_refresh=True)


# ─────────────────────────────────────────────────────────────
# 以下为后续阶段接口占位（先不实现，避免 linter 报 unused import）
# ─────────────────────────────────────────────────────────────

# 阶段5：
#   GET  /api/memory/symbols/{symbol}        品种记忆详情（短期/长期/关联/统计）
#   GET  /api/memory/relations               关联图谱（含实测 corr）
#   GET  /api/memory/stats                   命中率、校准曲线、recent episodes
#   POST /api/memory/note                    人工写入/修正记忆
#   DELETE /api/memory/{id}                 删除人工记忆
#   POST /api/memory/backfill                手动触发回填（阶段4）
#   POST /api/memory/consolidate             手动触发巩固（阶段4）
#   POST /api/memory/semantics/{id}/review   语义记忆人工覆盖（强制 draft / 恢复 archived）
