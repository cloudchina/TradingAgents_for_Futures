"""记忆体系 API 路由 - 阶段0/1/2/4/5。

对应 memory-system-plan.md 第四章阶段 5 完整接口清单。当前已实现：
- GET  /api/memory/health
- POST /api/memory/init              手动触发 migrate（启动时已自动调）
- GET  /api/memory/availability      数据可用性清单（阶段0交付物）
- POST /api/memory/availability/refresh  强制现场扫描并落盘
- POST /api/memory/backfill          手动触发复盘回填（阶段4）
- POST /api/memory/consolidate       手动触发语义巩固（阶段4）
- GET  /api/memory/stats             命中率 / 校准曲线 / 错误模式（阶段4）
- GET  /api/memory/symbols/{symbol}  品种记忆详情（阶段2+5）
- GET  /api/memory/relations         关联图谱（阶段2+5）
- POST /api/memory/relations/refresh 重算动态相关（阶段2+5）
- POST /api/memory/note + GET/DELETE /api/memory/notes  人工记忆（阶段5）
- POST /api/memory/semantics/{id}/review  语义记忆人工覆盖（阶段5）

路由前缀统一用 /api/memory/symbols/{symbol}，避免路径参数吞掉 relations/stats
（【二评】已确认）。
"""
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

from loguru import logger

from services.memory_service import STM_WINDOW, memory_service
from services.availability_service import availability_service
from services.outcome_service import outcome_service
from services.backfill_scheduler import backfill_runner
from services.relation_service import relation_service

router = APIRouter(prefix="/api/memory", tags=["记忆体系"])


class NoteRequest(BaseModel):
    """人工记忆写入请求。"""
    symbol: Optional[str] = None
    content: str
    author: str = ""


class ReviewRequest(BaseModel):
    """语义记忆人工覆盖请求。"""
    action: str = Field(description="activate | force_draft | archive | restore")
    reviewer: str = ""


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
# 阶段2+5：品种记忆详情 / 关联图谱 / 人工记忆
# ─────────────────────────────────────────────────────────────

@router.get("/symbols/{symbol}")
async def get_symbol_memory(symbol: str) -> Dict[str, Any]:
    """品种记忆详情：短期记忆 / 长期记忆 / 关联品种 / 校准统计 / 人工备注。"""
    sym = symbol.upper()
    try:
        episodes = memory_service.get_recent_episodes(sym, limit=STM_WINDOW)
    except Exception as e:
        logger.warning(f"get_symbol_memory: 读 episode 失败 {sym}: {e}")
        episodes = []
    try:
        semantics = memory_service.list_semantics(symbol=sym)
    except Exception as e:
        logger.warning(f"get_symbol_memory: 读 semantics 失败 {sym}: {e}")
        semantics = []
    try:
        relations = [vars(r) for r in relation_service.get_related(sym)]
    except Exception as e:
        logger.warning(f"get_symbol_memory: 关联查询失败 {sym}: {e}")
        relations = []
    try:
        stats = outcome_service.compute_stats(symbol=sym, persist=False)
    except Exception as e:
        logger.warning(f"get_symbol_memory: 统计失败 {sym}: {e}")
        stats = {}
    try:
        notes = memory_service.list_notes(symbol=sym)
    except Exception as e:
        logger.warning(f"get_symbol_memory: 读 notes 失败 {sym}: {e}")
        notes = []

    return {
        "status": "ok",
        "symbol": sym,
        "has_prior": bool(episodes),
        "stm": [ep.model_dump() for ep in episodes],
        "ltm": [s.model_dump() for s in semantics],
        "relations": relations,
        "stats": stats,
        "notes": notes,
    }


@router.get("/relations")
async def get_relations() -> Dict[str, Any]:
    """关联图谱（静态关系 + 实测 corr / lead）。"""
    try:
        return {"status": "ok", **relation_service.graph()}
    except Exception as e:
        logger.error(f"关联图谱读取失败: {e}")
        return {"status": "failed", "error": str(e), "nodes": [], "edges": []}


@router.post("/relations/refresh")
async def refresh_relations(symbols: Optional[str] = None) -> Dict[str, Any]:
    """重算动态相关（数据更新后调用；symbols 留空 = 全部静态 pair）。"""
    symbol_list: Optional[List[str]] = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    )
    try:
        return {"status": "ok", **relation_service.refresh(symbols=symbol_list)}
    except Exception as e:
        logger.error(f"关联指标刷新失败: {e}")
        return {"status": "failed", "error": str(e)}


@router.post("/note")
async def write_note(req: NoteRequest) -> Dict[str, Any]:
    """人工写入/修正记忆。"""
    if not req.content.strip():
        return {"status": "failed", "error": "content 不能为空"}
    try:
        note_id = memory_service.write_note(req.symbol, req.content.strip(), req.author)
        return {"status": "ok", "id": note_id}
    except Exception as e:
        logger.error(f"写入人工记忆失败: {e}")
        return {"status": "failed", "error": str(e)}


@router.get("/notes")
async def list_notes(symbol: Optional[str] = None) -> Dict[str, Any]:
    """人工记忆列表。"""
    return {"status": "ok", "notes": memory_service.list_notes(symbol=symbol)}


@router.delete("/notes/{note_id}")
async def delete_note(note_id: int) -> Dict[str, Any]:
    """删除人工记忆（LLM 产出的语义记忆不删，走 archived）。"""
    ok = memory_service.delete_note(note_id)
    return {"status": "ok" if ok else "not_found", "deleted": ok}


@router.post("/semantics/{semantic_id}/review")
async def review_semantic(semantic_id: int, req: ReviewRequest) -> Dict[str, Any]:
    """语义记忆人工覆盖（【二评 / 决策 3】人工通道）。

    action:
    - activate    强制转 active（人工背书）
    - force_draft 强制回退 draft
    - archive     归档（停止注入）
    - restore     从 archived 恢复为 active
    """
    action_map = {
        "activate": "active",
        "force_draft": "draft",
        "archive": "archived",
        "restore": "active",
    }
    status = action_map.get(req.action)
    if not status:
        return {
            "status": "failed",
            "error": f"未知 action: {req.action}",
            "supported": sorted(action_map),
        }
    try:
        memory_service.update_semantic_status(
            semantic_id, status=status, reviewed_by=req.reviewer or "human"
        )
        return {"status": "ok", "id": semantic_id, "new_status": status}
    except Exception as e:
        logger.error(f"语义记忆 review 失败 {semantic_id}: {e}")
        return {"status": "failed", "error": str(e)}
