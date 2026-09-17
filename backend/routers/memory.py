"""记忆体系 API 路由 - 阶段0/1/4。

对应 memory-system-plan.md 第四章阶段 5 完整接口清单。当前已实现：
- GET  /api/memory/health
- POST /api/memory/init              手动触发 migrate（启动时已自动调）
- GET  /api/memory/availability      数据可用性清单（阶段0交付物）
- POST /api/memory/availability/refresh  强制现场扫描并落盘
- POST /api/memory/backfill          手动触发复盘回填（阶段4）
- POST /api/memory/consolidate       手动触发语义巩固（阶段4）
- GET  /api/memory/stats             命中率 / 校准曲线 / 错误模式（阶段4）

后续阶段（2/5）补全的接口以 TODO 注释占位，先占路由前缀，避免后续加路由时
路径参数冲突（【二评】已确认统一用 /api/memory/symbols/{symbol} 前缀）。
"""
from fastapi import APIRouter
from typing import Any, Dict, List, Optional

from loguru import logger

from services.memory_service import memory_service
from services.availability_service import availability_service
from services.outcome_service import outcome_service
from services.backfill_scheduler import backfill_runner

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
# 阶段4：复盘回填 / 语义巩固 / 统计
# ─────────────────────────────────────────────────────────────

@router.post("/backfill")
async def trigger_backfill(symbols: Optional[str] = None) -> Dict[str, Any]:
    """手动触发一次复盘回填（等价于调度器每日 20:00 的那次）。

    symbols: 逗号分隔的品种代码；留空 = 全部待回填 episode。
    """
    symbol_list: Optional[List[str]] = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    )
    return backfill_runner.run_once(symbols=symbol_list)


@router.post("/consolidate")
async def trigger_consolidate(symbol: Optional[str] = None) -> Dict[str, Any]:
    """手动触发语义记忆巩固（draft → pending → active）。

    LLM 二次校验默认开启；若 LLM 不可用则只更新证据数、不自动流转。
    """
    try:
        result = outcome_service.consolidate_semantics(
            symbol=symbol, llm_verify=outcome_service.default_llm_verify
        )
        return {"status": "ok", **result}
    except Exception as e:
        logger.error(f"语义巩固失败: {e}")
        return {"status": "failed", "error": str(e)}


@router.get("/stats")
async def get_stats(symbol: Optional[str] = None) -> Dict[str, Any]:
    """命中率、校准曲线、换月样本、错误模式（只统计 resolved）。"""
    return outcome_service.compute_stats(symbol=symbol, persist=False)


# ─────────────────────────────────────────────────────────────
# 以下为后续阶段接口占位（先不实现，避免 linter 报 unused import）
# ─────────────────────────────────────────────────────────────

# 阶段5：
#   GET  /api/memory/symbols/{symbol}        品种记忆详情（短期/长期/关联/统计）
#   GET  /api/memory/relations               关联图谱（含实测 corr）
#   POST /api/memory/note                    人工写入/修正记忆
#   DELETE /api/memory/{id}                 删除人工记忆
#   POST /api/memory/semantics/{id}/review   语义记忆人工覆盖（强制 draft / 恢复 archived）
