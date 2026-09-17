"""记忆检索与注入层（阶段 3）。

对应 memory-system-plan.md 第二章第 4 节：
- 打分：score = 0.35·recency + 0.25·relevance + 0.25·reliability + 0.15·importance
- 分段预算（token）：stm ≤ 800 / ltm ≤ 600 / relation ≤ 400，实现上用 len(text) 保守上界
- 关联段独立排序、不参与本品种 score 竞争（阶段 2 落地后由 relation_service 提供）
- has_prior 由服务端判定，不依赖 agent 回写（【二评】）
- 每次 build_context 写 memory_injections 埋点（【三评 F】）
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger
from pydantic import BaseModel, Field

from models.memory import Episode, Semantic
from services.memory_service import (
    LTM_CANDIDATE_LIMIT,
    RELIABILITY_MIN_N,
    STM_WINDOW,
    TOKEN_BUDGET,
    MemoryService,
    memory_service,
)


# ─────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────

# 二.4 recency = exp(-Δt / λ)，λ = stm_window / 2 = 5
RECENCY_LAMBDA = STM_WINDOW / 2

# 自然日 → 交易日的折算系数（阶段 4 引入 akshare 交易日历后替换为精确口径）
DAYS_TO_TRADING_DAYS = 5.0 / 7.0


# ─────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────

class MemoryContext(BaseModel):
    """build_context 的输出。

    to_prompt() 拼装成固定前缀，由调用方塞进 user_msg 的最前面，
    保证辩论每一轮都带记忆（【二评】禁止只在 Round 1 注入）。
    """
    symbol: str
    analysis_date: str
    run_id: str = ""
    has_prior: bool = False
    stm_text: str = ""
    ltm_text: str = ""
    relation_text: str = ""
    injected_episode_ids: List[int] = Field(default_factory=list)
    injected_semantic_ids: List[int] = Field(default_factory=list)
    token_count: int = 0
    char_count: int = 0

    def to_prompt(self) -> str:
        """拼装记忆段；无内容返回空串（调用方直接跳过注入）。"""
        parts: List[str] = []
        if self.stm_text:
            parts.append(f"### 近期结论（短期记忆）\n{self.stm_text}")
        if self.ltm_text:
            parts.append(f"### 历史规律与复盘（长期记忆）\n{self.ltm_text}")
        if self.relation_text:
            parts.append(f"### 关联品种\n{self.relation_text}")
        if not parts:
            return ""
        header = (
            f"【记忆上下文｜品种 {self.symbol}｜"
            f"{'有历史结论' if self.has_prior else '首次分析，无历史结论'}】\n"
            "以下记忆仅供参考，最新数据优先；不要因为是历史结论就照搬。"
        )
        return header + "\n\n" + "\n\n".join(parts) + "\n"


# ─────────────────────────────────────────────────────────────
# Builder
# ─────────────────────────────────────────────────────────────

class MemoryContextBuilder:
    """记忆上下文构建器。"""

    def __init__(self, svc: Optional[MemoryService] = None):
        self.svc = svc or memory_service

    # ─── 主入口 ───

    def build_context(
        self,
        symbol: str,
        analysis_date: str,
        run_id: str = "",
        with_relation: bool = True,
    ) -> MemoryContext:
        """构建一次分析的记忆上下文。

        Args:
            symbol: 品种代码
            analysis_date: 本次分析日期 YYYY-MM-DD
            run_id: 复用 AnalysisTask.task_id + commodity，用于埋点
            with_relation: 是否拼装关联品种段（阶段 2 未落地时自动降级为空）
        """
        ctx = MemoryContext(symbol=symbol, analysis_date=analysis_date, run_id=run_id)

        try:
            episodes = self.svc.get_recent_episodes(
                symbol, limit=LTM_CANDIDATE_LIMIT, before_date=analysis_date
            )
        except Exception as e:
            logger.warning(f"[memory_builder] 读 episode 失败 {symbol}: {e}")
            episodes = []

        ctx.has_prior = bool(episodes)

        if not episodes:
            return ctx

        rates = self.svc.get_hit_rates(symbol)
        reliability = self._reliability(rates)

        scored: List[Tuple[float, Episode]] = []
        for ep in episodes:
            delta_t = self._trading_days(ep.analysis_date, analysis_date)
            recency = math.exp(-delta_t / RECENCY_LAMBDA)
            importance = self._importance(ep)
            score = (
                0.35 * recency
                + 0.25 * 1.0          # relevance：本品种固定 1.0
                + 0.25 * reliability
                + 0.15 * importance
            )
            scored.append((score, ep))
        scored.sort(key=lambda x: (-x[0], _date_key(x[1].analysis_date)))

        # STM：按时间序取最近 STM_WINDOW 条（滚动队列语义），预算内从旧到新截断
        stm_pool = episodes[:STM_WINDOW]
        stm_lines: List[str] = []
        stm_ids: List[int] = []
        # 展示顺序：由新到旧
        for ep in stm_pool:
            line = self._format_episode(ep)
            stm_lines.append(line)
            stm_ids.append(ep.id or 0)

        # LTM：语义记忆优先，其次 STM 之外的高分历史 episode
        ltm_lines: List[str] = []
        ltm_semantic_ids: List[int] = []
        try:
            semantics = self.svc.list_active_semantics(symbol)
        except Exception as e:
            logger.warning(f"[memory_builder] 读 semantics 失败 {symbol}: {e}")
            semantics = []
        for sem in semantics:
            ltm_lines.append(self._format_semantic(sem))
            if sem.id:
                ltm_semantic_ids.append(sem.id)

        ltm_episode_ids: List[int] = []
        stm_id_set = {e.id for e in stm_pool}
        for score, ep in scored:
            if ep.id in stm_id_set:
                continue
            ltm_lines.append(self._format_episode(ep))
            if ep.id:
                ltm_episode_ids.append(ep.id)

        # 预算裁剪：token 用 len(text) 作保守上界（中文 1 字 ≈ 1~1.5 token）
        stm_text, stm_kept = self._trim(stm_lines, TOKEN_BUDGET["stm"])
        ltm_text, ltm_kept = self._trim(ltm_lines, TOKEN_BUDGET["ltm"])

        ctx.stm_text = stm_text
        ctx.ltm_text = ltm_text
        # 被预算裁剪掉的行，其 id 必须从注入集合里剔除，否则埋点与 memory_refs 校验
        # 会引用本次并未真正注入 prompt 的记忆（【三评 C】）
        # LTM 段内语义记忆排在 episode 之前，故 episode 保留数需扣掉语义行数
        ltm_ep_kept = max(0, ltm_kept - len(ltm_semantic_ids))
        ctx.injected_episode_ids = [i for i in stm_ids[:stm_kept] if i]
        ctx.injected_episode_ids += ltm_episode_ids[:ltm_ep_kept]
        ctx.injected_semantic_ids = ltm_semantic_ids[: min(ltm_kept, len(ltm_semantic_ids))]

        if with_relation:
            ctx.relation_text = self._build_relation_segment(symbol, analysis_date)

        ctx.char_count = len(ctx.stm_text) + len(ctx.ltm_text) + len(ctx.relation_text)
        ctx.token_count = ctx.char_count

        self._write_injections(ctx)
        return ctx

    # ─── 打分分量 ───

    @staticmethod
    def _reliability(rates: Dict[str, Any]) -> float:
        """【三评 D】三层 fallback：品种命中率 → 全局命中率 → 常量 0.5。"""
        sym = rates.get("symbol_rate")
        if sym is not None:
            return float(sym)
        glob = rates.get("global_rate")
        if glob is not None:
            return float(glob)
        return 0.5

    @staticmethod
    def _trading_days(past_date: str, today: str) -> float:
        """距今交易日数。

        阶段 3 用自然日 × 5/7 近似；阶段 4 引入 akshare 交易日历后改为精确口径
        （horizon 与 recency 都是交易日语义，春节/国庆跨 10+ 自然日会显著失真）。
        """
        d0 = _parse_date(past_date)
        d1 = _parse_date(today)
        if d0 is None or d1 is None:
            return float(STM_WINDOW)
        delta = (d1 - d0).days
        if delta < 0:
            delta = 0
        return delta * DAYS_TO_TRADING_DAYS

    @staticmethod
    def _importance(ep: Episode) -> float:
        """importance = 0.5 × (|conf − 0.5| × 2) + 0.5 × action_score"""
        try:
            conf = float(ep.confidence or 0.0)
        except Exception:
            conf = 0.0
        conf_term = min(max(abs(conf - 0.5) * 2, 0.0), 1.0)
        return 0.5 * conf_term + 0.5 * MemoryContextBuilder._action_score(ep)

    @staticmethod
    def _action_score(ep: Episode) -> float:
        """明确开仓/平仓建议 = 1.0，观望/中性 = 0.5，无明确动作 = 0.3。"""
        decision = ep.decision or {}
        action_items = decision.get("action_items")
        if isinstance(action_items, list) and action_items:
            return 1.0
        if ep.direction == "neutral" or ep.direction_view == "中性":
            return 0.5
        if ep.summary or decision:
            return 0.5
        return 0.3

    # ─── 格式化 ───

    @staticmethod
    def _format_episode(ep: Episode) -> str:
        """单条 episode 的 prompt 文案：日期/方向/置信度/依据/是否已验证。"""
        verified = ""
        if ep.status == "resolved" and isinstance(ep.outcome, dict):
            hit = ep.outcome.get("hit")
            ret = ep.outcome.get("realized_return")
            hit_text = "命中" if hit else ("未命中" if hit is False else "未知")
            ret_text = f"，收益 {ret:+.2%}" if isinstance(ret, (int, float)) else ""
            verified = f" [已验证：{hit_text}{ret_text}]"
        elif ep.status == "pending":
            verified = " [待验证]"
        evidence = ""
        if ep.key_evidence:
            evidence = "；依据：" + "、".join(str(x) for x in ep.key_evidence[:3])
        summary = f" {ep.summary}" if ep.summary else ""
        conf = ep.confidence if ep.confidence is not None else 0.0
        return (
            f"- {ep.analysis_date}｜{ep.direction_view or ep.direction}"
            f"（conf={conf:.2f}）{summary}{evidence}{verified}"
        )

    @staticmethod
    def _format_semantic(sem: Semantic) -> str:
        """单条语义记忆的 prompt 文案。"""
        return (
            f"- [{sem.category or '规律'}] {sem.claim}"
            f"（样本 {sem.evidence_count} 次，置信度 {sem.confidence:.2f}）"
        )

    # ─── 关联段（阶段 2 落地后自动生效） ───

    @staticmethod
    def _build_relation_segment(symbol: str, analysis_date: str) -> str:
        """关联品种段。

        阶段 3 时 relation_service 尚未实现，这里动态导入失败即降级为空串，
        保证阶段 3 可独立验收；阶段 2 落地后无需改动本文件。
        """
        try:
            from services.relation_service import relation_service  # type: ignore
        except Exception:
            return ""
        try:
            return relation_service.build_segment(symbol, analysis_date)
        except Exception as e:
            logger.warning(f"[memory_builder] 关联段构建失败 {symbol}: {e}")
            return ""

    # ─── 预算与埋点 ───

    @staticmethod
    def _trim(lines: Sequence[str], budget: int) -> Tuple[str, int]:
        """按给定顺序累加，超出预算即停；返回 (文本, 保留行数)。"""
        kept = 0
        used = 0
        out: List[str] = []
        for line in lines:
            cost = len(line) + 1  # +1 换行
            if used + cost > budget:
                break
            out.append(line)
            used += cost
            kept += 1
        return "\n".join(out), kept

    def _write_injections(self, ctx: MemoryContext) -> None:
        """【三评 F】每段一条埋点；失败不阻塞分析。"""
        from models.memory import MemoryInjection

        segments = [
            ("stm", ctx.stm_text, ctx.injected_episode_ids, []),
            ("ltm", ctx.ltm_text, [], ctx.injected_semantic_ids),
            ("relation", ctx.relation_text, [], []),
        ]
        for segment, text, ep_ids, sem_ids in segments:
            if not text:
                continue
            try:
                self.svc.write_injection(
                    MemoryInjection(
                        run_id=ctx.run_id,
                        symbol=ctx.symbol,
                        segment=segment,
                        episode_ids=list(ep_ids),
                        semantic_ids=list(sem_ids),
                        token_count=len(text),
                        char_count=len(text),
                    )
                )
            except Exception as e:
                logger.warning(f"[memory_builder] 埋点写入失败 {ctx.symbol}/{segment}: {e}")


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def _parse_date(value: str) -> Optional[date]:
    """宽松解析 YYYY-MM-DD / YYYY/MM/DD / ISO datetime。"""
    if not value:
        return None
    s = str(value).strip().replace("/", "-")
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _date_key(value: str) -> str:
    """排序用：无法解析的日期排最后。"""
    return str(value or "")


# ─────────────────────────────────────────────────────────────
# 单例
# ─────────────────────────────────────────────────────────────

memory_builder = MemoryContextBuilder()
