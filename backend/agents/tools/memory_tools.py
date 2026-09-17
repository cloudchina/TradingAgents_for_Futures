"""记忆体系工具（阶段 3）。

对应 memory-system-plan.md 第四章阶段 3：
- search_memory：agent 主动检索历史结论与复盘（省 token，按需深挖）
- get_related_symbols：查关联品种及其实测相关/当前信号（阶段 2 落地后生效）
- record_insight：agent 主动沉淀语义记忆，落库一律 status='draft'

【三评 E】record_insight 单次 analysis run 上限 2 次，超额直接拦截并返回错误文案，
防止 agent 在一次分析里反复产 draft 污染 semantics 表。
"""
from __future__ import annotations

import json
import threading
from contextvars import ContextVar
from typing import Any, Dict, List

from loguru import logger

from models.memory import Semantic, SemanticStatus
from services.memory_service import (
    RECORD_INSIGHT_MAX_PER_RUN,
    STM_WINDOW,
    memory_service,
)


# ─────────────────────────────────────────────────────────────
# run 上下文（record_insight 限次用）
# ─────────────────────────────────────────────────────────────

_current_run_id: ContextVar[str] = ContextVar("memory_run_id", default="")
_insight_counts: Dict[str, int] = {}
_insight_lock = threading.Lock()


def set_run_context(run_id: str) -> None:
    """分析开始时绑定 run_id（由 analysis_service._analyze_commodity 调用）。"""
    _current_run_id.set(run_id or "")


def reset_run_context() -> None:
    """分析结束后清理，避免线程复用时串味。"""
    _current_run_id.set("")


def _bump_insight_count(run_id: str) -> int:
    """返回本次调用后的累计次数（含本次）。"""
    with _insight_lock:
        _insight_counts[run_id] = _insight_counts.get(run_id, 0) + 1
        return _insight_counts[run_id]


def reset_insight_counts() -> None:
    """测试与手动重置用：清空限次计数。"""
    with _insight_lock:
        _insight_counts.clear()


# ─────────────────────────────────────────────────────────────
# 工具实现
# ─────────────────────────────────────────────────────────────

def search_memory(symbol: str, days: int = STM_WINDOW, top_k: int = 5) -> str:
    """检索某品种的历史分析结论与复盘结果。

    Args:
        symbol: 品种代码，如 RB
        days: 回看天数（自然日），默认 10
        top_k: 返回条数，默认 5
    """
    try:
        episodes = memory_service.get_recent_episodes(symbol, limit=max(top_k * 4, 20))
    except Exception as e:
        logger.warning(f"[memory_tools] search_memory 读库失败 {symbol}: {e}")
        return f"[记忆检索失败] {e}"

    if not episodes:
        return f"{symbol} 暂无历史分析结论（首次分析）。"

    lines: List[str] = []
    for ep in episodes[: max(top_k, 1)]:
        outcome_text = ""
        if ep.status == "resolved" and isinstance(ep.outcome, dict):
            hit = ep.outcome.get("hit")
            ret = ep.outcome.get("realized_return")
            ret_text = f"，收益 {ret:+.2%}" if isinstance(ret, (int, float)) else ""
            outcome_text = f"，复盘：{'命中' if hit else '未命中'}{ret_text}"
        elif ep.status == "pending":
            outcome_text = "，复盘：待验证"
        conf = ep.confidence if ep.confidence is not None else 0.0
        lines.append(
            f"- [id={ep.id}] {ep.analysis_date}｜{ep.direction_view or ep.direction}"
            f"（conf={conf:.2f}）{ep.summary or ''}{outcome_text}"
        )
    return f"{symbol} 最近 {len(lines)} 条历史结论：\n" + "\n".join(lines)


def get_related_symbols(symbol: str) -> str:
    """查关联品种及其实测相关性。

    阶段 2（relation_service）落地前返回明确提示，避免 agent 误以为无关联。
    """
    try:
        from services.relation_service import relation_service  # type: ignore
    except Exception:
        return "关联图谱尚未启用（阶段 2 未落地），本次分析不提供参考关联品种。"

    try:
        related = relation_service.get_related(symbol)
    except Exception as e:
        logger.warning(f"[memory_tools] get_related_symbols 失败 {symbol}: {e}")
        return f"[关联查询失败] {e}"

    if not related:
        return f"{symbol} 暂无已配置的关联品种。"

    lines: List[str] = []
    for item in related:
        corr = item.get("corr")
        corr_text = f"{corr:.2f}" if isinstance(corr, (int, float)) else "暂无实测"
        lead = item.get("lead_symbol")
        lag = item.get("lag_days")
        lead_text = ""
        if lead and isinstance(lag, int):
            if lead == symbol:
                lead_text = f"，{symbol} 领先 {abs(lag)} 日"
            else:
                lead_text = f"，{lead} 领先 {symbol} {abs(lag)} 日"
        lines.append(
            f"- {item.get('other')}（{item.get('type', '关联')}，"
            f"强度 {item.get('strength', 0.5):.2f}，实测 corr={corr_text}{lead_text}）"
        )
    return f"{symbol} 的关联品种：\n" + "\n".join(lines)


def record_insight(symbol: str, claim: str, category: str = "规律") -> str:
    """沉淀一条语义记忆（落库为 draft，需二次校验后才可注入）。

    单次分析最多调用 2 次（【三评 E】）。
    """
    run_id = _current_run_id.get() or "unknown"
    used = _bump_insight_count(run_id)
    if used > RECORD_INSIGHT_MAX_PER_RUN:
        logger.warning(
            f"[memory_tools] record_insight 超额拦截 run={run_id} "
            f"({used}/{RECORD_INSIGHT_MAX_PER_RUN})"
        )
        return (
            f"[已拦截] 单次分析最多沉淀 {RECORD_INSIGHT_MAX_PER_RUN} 条规律，"
            "本次调用未落库。请保留最重要的结论。"
        )

    try:
        sem = Semantic(
            symbol=symbol,
            claim=claim,
            category=category,
            evidence_count=0,
            confidence=0.0,
            status=SemanticStatus.DRAFT.value,
            reviewed_by=f"agent:{run_id}",
        )
        sem_id = memory_service.write_semantic(sem)
    except Exception as e:
        logger.warning(f"[memory_tools] record_insight 落库失败 {symbol}: {e}")
        return f"[沉淀失败] {e}"

    logger.info(f"[memory_tools] {symbol} 沉淀 draft 语义记忆 id={sem_id}")
    return (
        f"[已记录] 语义记忆 id={sem_id} 已存为 draft（需校验通过后才会在后续分析中被引用）。"
    )


# 工具函数映射（与 tool_specs.ALL_TOOLS 中的 name 对应）
MEMORY_TOOL_MAP = {
    "search_memory": search_memory,
    "get_related_symbols": get_related_symbols,
    "record_insight": record_insight,
}
