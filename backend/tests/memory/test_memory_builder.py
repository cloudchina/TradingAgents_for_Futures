"""阶段 3 测试：记忆检索与注入（memory-system-plan.md 10.4）。

覆盖：
- 首日场景（无历史 episode）：has_prior=false、不抛异常、prompt 为空
- 预算裁剪：stm/ltm 各段长度不超配置上限，超出按序截断
- 同输入同输出（注入幂等性）
- has_prior 服务端权威 + memory_refs 只保留本次注入过的 id
- use_memory=False 时清掉记忆相关输出字段
- 埋点：build_context 后 memory_injections 有记录
- record_insight 单次 run 上限 2 次（【三评 E】）
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import List

import pytest

from agents.tools import memory_tools
from agents.tools.memory_tools import record_insight, reset_insight_counts, set_run_context
from models.memory import Episode, EpisodeStatus
from services.analysis_service import AnalysisManager
from services.memory_builder import MemoryContextBuilder
from services.memory_service import STM_WINDOW, TOKEN_BUDGET


# ─────────────────────────────────────────────────────────────
# helper
# ─────────────────────────────────────────────────────────────

def _write_episodes(svc, symbol: str, dates: List[str], long_text: bool = False) -> List[int]:
    """按日期列表写 episode，返回 id 列表（与 dates 同序）。"""
    ids: List[int] = []
    for i, d in enumerate(dates):
        summary = "库存持续去化且基差走强，现货偏紧支撑价格" * (8 if long_text else 1)
        ep = Episode(
            symbol=symbol,
            analysis_date=d,
            run_id=f"run_{i}",
            direction="long" if i % 2 == 0 else "short",
            direction_view="看多" if i % 2 == 0 else "看空",
            confidence=0.6,
            confidence_level="中",
            summary=summary,
            key_evidence=["库存去化", "基差走强"],
            decision={"action_items": ["开仓"]},
            status=EpisodeStatus.PENDING.value,
            is_canonical=True,
        )
        ids.append(svc.write_episode(ep))
    return ids


# ─────────────────────────────────────────────────────────────
# build_context
# ─────────────────────────────────────────────────────────────

def test_first_run_no_prior(memory_svc):
    """首日场景：从未分析过的品种，has_prior=false、prompt 为空、不抛异常。"""
    builder = MemoryContextBuilder(svc=memory_svc)
    ctx = builder.build_context("RB", "2026-09-18", run_id="r1")

    assert ctx.has_prior is False
    assert ctx.to_prompt() == ""
    assert ctx.injected_episode_ids == []


def test_has_prior_true_and_stm_filled(memory_svc):
    _write_episodes(memory_svc, "JM", ["2026-09-10", "2026-09-11"])
    builder = MemoryContextBuilder(svc=memory_svc)
    ctx = builder.build_context("JM", "2026-09-18", run_id="r1")

    assert ctx.has_prior is True
    assert "2026-09-11" in ctx.stm_text
    assert len(ctx.injected_episode_ids) == 2
    assert "首次分析" not in ctx.to_prompt()


def test_before_date_excludes_same_day(memory_svc):
    """同日旧 run 不应被当成历史结论注入（force_refresh 重跑场景）。"""
    _write_episodes(memory_svc, "JM", ["2026-09-18", "2026-09-17"])
    builder = MemoryContextBuilder(svc=memory_svc)
    ctx = builder.build_context("JM", "2026-09-18", run_id="r1")

    assert "2026-09-18" not in ctx.stm_text
    assert "2026-09-17" in ctx.stm_text


def test_budget_trim(memory_svc):
    """预算裁剪：各段长度不超 TOKEN_BUDGET，且关联段不挤占本品种段。"""
    base = date(2026, 9, 18)
    dates = [(base - timedelta(days=i + 1)).isoformat() for i in range(40)]
    _write_episodes(memory_svc, "UR", dates, long_text=True)

    builder = MemoryContextBuilder(svc=memory_svc)
    ctx = builder.build_context("UR", "2026-09-18", run_id="r1")

    assert len(ctx.stm_text) <= TOKEN_BUDGET["stm"]
    assert len(ctx.ltm_text) <= TOKEN_BUDGET["ltm"]
    # 被裁掉的 episode 不应出现在注入 id 集合里
    assert len(ctx.injected_episode_ids) < len(dates)


def test_idempotent_same_input(memory_svc):
    """同输入同输出：固定库内容调两次，文本完全一致（不涉及 LLM）。"""
    _write_episodes(memory_svc, "EG", ["2026-09-12", "2026-09-15"])
    builder = MemoryContextBuilder(svc=memory_svc)
    a = builder.build_context("EG", "2026-09-18", run_id="r1")
    b = builder.build_context("EG", "2026-09-18", run_id="r1")

    assert a.stm_text == b.stm_text
    assert a.ltm_text == b.ltm_text
    assert a.injected_episode_ids == b.injected_episode_ids
    assert a.to_prompt() == b.to_prompt()


def test_injection_telemetry_written(memory_svc):
    """【三评 F】每次 build_context 写埋点，段名 stm/ltm。"""
    _write_episodes(memory_svc, "LH", ["2026-09-16"])
    builder = MemoryContextBuilder(svc=memory_svc)
    builder.build_context("LH", "2026-09-18", run_id="run_x")

    with memory_svc._connect() as conn:
        rows = conn.execute(
            "SELECT segment, token_count FROM memory_injections WHERE run_id='run_x'"
        ).fetchall()
    segments = {r["segment"] for r in rows}
    assert "stm" in segments
    assert all(r["token_count"] > 0 for r in rows)


def test_parse_failed_not_injected(memory_svc):
    """parse_failed 不进 STM 注入（10.1 写入前置校验的延续）。"""
    ep = Episode(
        symbol="PK",
        analysis_date="2026-09-15",
        run_id="bad",
        direction="",
        direction_view="",
        confidence=0.0,
        confidence_level="",
        status=EpisodeStatus.PARSE_FAILED.value,
        is_canonical=True,
    )
    memory_svc.write_episode(ep)
    builder = MemoryContextBuilder(svc=memory_svc)
    ctx = builder.build_context("PK", "2026-09-18", run_id="r1")

    assert ctx.has_prior is False
    assert ctx.stm_text == ""


# ─────────────────────────────────────────────────────────────
# 服务端补齐 has_prior / memory_refs
# ─────────────────────────────────────────────────────────────

class _FakeCtx:
    has_prior = True
    token_count = 123
    injected_episode_ids = [7, 8]
    injected_semantic_ids = [3]


def test_memory_refs_filtered_to_injected():
    """memory_refs 只能引用本次注入过的 id，无效 id 丢弃且不报错。"""
    decision = {"final_decision": "long", "memory_refs": [7, 999, "abc", 3]}
    AnalysisManager._attach_memory_fields(decision, _FakeCtx())

    assert decision["has_prior"] is True
    assert decision["memory_refs"] == [7, 3]


def test_has_prior_server_authoritative():
    """agent 伪造 has_prior=false 时，以服务端计算为准。"""
    decision = {"has_prior": False, "memory_refs": []}
    AnalysisManager._attach_memory_fields(decision, _FakeCtx())
    assert decision["has_prior"] is True


def test_first_run_forces_first_run_value():
    ctx = _FakeCtx()
    ctx.has_prior = False
    decision = {"change_vs_last": "reversed", "change_reason": "编造的理由"}
    AnalysisManager._attach_memory_fields(decision, ctx)
    assert decision["change_vs_last"] == "first_run"


def test_use_memory_off_clears_fields():
    """use_memory=False 时清掉记忆相关字段，保证 A/B 两侧输出口径一致。"""
    decision = {
        "has_prior": True,
        "change_vs_last": "reversed",
        "change_reason": "x",
        "memory_refs": [7],
    }
    AnalysisManager._attach_memory_fields(decision, None)
    assert "has_prior" not in decision
    assert "change_vs_last" not in decision
    assert "memory_refs" not in decision


# ─────────────────────────────────────────────────────────────
# record_insight 限次（【三评 E】）
# ─────────────────────────────────────────────────────────────

def test_record_insight_limit_per_run(memory_svc, monkeypatch):
    """单次 run 最多 2 条，第 3 次被拦截且不落库。"""
    monkeypatch.setattr(memory_tools, "memory_service", memory_svc)
    reset_insight_counts()
    set_run_context("run_limit")

    r1 = record_insight("RB", "规律一")
    r2 = record_insight("RB", "规律二")
    r3 = record_insight("RB", "规律三")

    assert "已记录" in r1 and "已记录" in r2
    assert "已拦截" in r3
    assert len(memory_svc.list_semantics(symbol="RB")) == 2
    assert all(s.status == "draft" for s in memory_svc.list_semantics(symbol="RB"))


def test_record_insight_separate_runs(memory_svc, monkeypatch):
    """不同 run 各自计数，互不影响。"""
    monkeypatch.setattr(memory_tools, "memory_service", memory_svc)
    reset_insight_counts()

    set_run_context("run_a")
    record_insight("RB", "规律A1")
    record_insight("RB", "规律A2")
    set_run_context("run_b")
    r = record_insight("RB", "规律B1")

    assert "已记录" in r
    assert len(memory_svc.list_semantics(symbol="RB")) == 3
