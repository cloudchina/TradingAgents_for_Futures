"""【三评 B】注入反馈回写：invoked_count / failed_invocations 的累加口径。

判据：只要 ContextBuilder 把该 semantic 写进了本次 prompt（memory_injections 埋点），
就算一次"被注入"，与 agent 是否在 memory_refs 里回引无关——否则 agent 不回引时
永远少计，降级统计（failed_invocations / invoked_count > 0.5）会失真。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import List

import pytest

from models.memory import Episode, EpisodeStatus, MemoryInjection, Semantic
from services.outcome_service import OutcomeService, RollAdjuster
from services.trade_calendar import TradeCalendar


def _workdays(n: int, start: str = "2026-09-01") -> List[str]:
    d = date.fromisoformat(start)
    out: List[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


@pytest.fixture
def svc(memory_svc, tmp_path) -> OutcomeService:
    return OutcomeService(
        svc=memory_svc,
        data_root=tmp_path,
        calendar=TradeCalendar(cache_path=tmp_path / "cal.json", fetcher=lambda: _workdays(150)),
        adjuster=RollAdjuster(cache_dir=tmp_path / "adj", fetcher=lambda *_a: None),
    )


def _sem(symbol: str, claim: str) -> Semantic:
    return Semantic(symbol=symbol, claim=claim, category="pattern")


def _inject(memory_svc, run_id: str, semantic_ids: List[int], segment: str = "ltm") -> None:
    memory_svc.write_injection(
        MemoryInjection(
            run_id=run_id,
            symbol="JM",
            injected_at="2026-09-01T10:00:00",
            segment=segment,
            episode_ids=[],
            semantic_ids=semantic_ids,
            token_count=100,
            char_count=300,
        )
    )


def test_injected_semantic_ids_dedup(memory_svc):
    """同一 run 跨 segment 注入同一条 semantic，只算一次。"""
    ids = [memory_svc.write_semantic(_sem("JM", f"规律{i}")) for i in range(3)]
    _inject(memory_svc, "run_1", [ids[0], ids[1]], segment="ltm")
    _inject(memory_svc, "run_1", [ids[1], ids[2]], segment="relation")
    assert memory_svc.injected_semantic_ids("run_1") == sorted(ids)
    assert memory_svc.injected_semantic_ids("run_missing") == []


def test_bump_on_hit_false_counts_failure(memory_svc):
    """注入后未命中：invoked_count +1 且 failed_invocations +1。"""
    sid = memory_svc.write_semantic(_sem("JM", "库存去化看多"))
    memory_svc.bump_semantic_invocations([sid], failed=True)
    sem = memory_svc.list_semantics(symbol="JM")[0]
    assert sem.invoked_count == 1
    assert sem.failed_invocations == 1


def test_bump_on_hit_true_only_counts_invocation(memory_svc):
    """命中：只累加 invoked_count，失败数不动（否则降级判据分子虚高）。"""
    sid = memory_svc.write_semantic(_sem("JM", "基差走强看多"))
    memory_svc.bump_semantic_invocations([sid], failed=False)
    sem = memory_svc.list_semantics(symbol="JM")[0]
    assert sem.invoked_count == 1
    assert sem.failed_invocations == 0


def test_bump_injections_uses_outcome_hit(svc, memory_svc):
    """回填结果 hit=False → 本次注入的每条 semantic 都记一次失败。"""
    ids = [memory_svc.write_semantic(_sem("JM", f"规律{i}")) for i in range(2)]
    _inject(memory_svc, "run_hit_false", ids)
    ep = Episode(
        symbol="JM", analysis_date="2026-09-01", run_id="run_hit_false",
        direction="long", direction_view="看多", confidence=0.6, confidence_level="中",
        summary="s", horizon=5, status=EpisodeStatus.RESOLVED.value,
    )
    bumped = svc._bump_injections(ep, {"hit": False})
    assert bumped == 2
    sems = memory_svc.list_semantics(symbol="JM")
    assert all(s.invoked_count == 1 and s.failed_invocations == 1 for s in sems)


def test_bump_injections_no_injection_noop(svc, memory_svc):
    """没注入过任何 semantic（如首日无记忆）→ 不累加，不报错。"""
    ep = Episode(
        symbol="JM", analysis_date="2026-09-01", run_id="run_empty",
        direction="long", direction_view="看多", confidence=0.6, confidence_level="中",
        summary="s", horizon=5, status=EpisodeStatus.RESOLVED.value,
    )
    assert svc._bump_injections(ep, {"hit": True}) == 0


def test_bump_injections_ignores_unknown_ids(memory_svc, svc):
    """埋点里的 id 已被清理时，UPDATE 影响 0 行但不报错。"""
    _inject(memory_svc, "run_gone", [9999])
    ep = Episode(
        symbol="JM", analysis_date="2026-09-01", run_id="run_gone",
        direction="long", direction_view="看多", confidence=0.6, confidence_level="中",
        summary="s", horizon=5, status=EpisodeStatus.RESOLVED.value,
    )
    assert svc._bump_injections(ep, {"hit": False}) == 1


# ─────────────────────────────────────────────────────────────
# 降级判据（【三评 B】后半）：active → archived
# ─────────────────────────────────────────────────────────────

def _active_sem(memory_svc, symbol: str, claim: str, invoked: int, failed: int) -> int:
    sid = memory_svc.write_semantic(_sem(symbol, claim))
    memory_svc.update_semantic_status(sid, "active")
    with memory_svc._connect() as conn:
        conn.execute(
            "UPDATE semantics SET invoked_count=?, failed_invocations=? WHERE id=?",
            (invoked, failed, sid),
        )
    return sid


def test_active_downgraded_when_fail_ratio_high(svc, memory_svc):
    """注入 6 次失败 4 次（0.67 > 0.5 且 ≥5 次）→ 归档，停止注入。"""
    sid = _active_sem(memory_svc, "JM", "高失败率规律", invoked=6, failed=4)
    result = svc.consolidate_semantics()
    assert result["downgraded"] == 1
    assert memory_svc.list_semantics(symbol="JM")[0].status == "archived"


def test_active_kept_when_fail_ratio_low(svc, memory_svc):
    """失败率 0.2 ≤ 0.5 → 不降级（一次分析失败 ≠ 这条记忆有错）。"""
    _active_sem(memory_svc, "JM", "低失败率规律", invoked=5, failed=1)
    result = svc.consolidate_semantics()
    assert result["downgraded"] == 0
    assert memory_svc.list_semantics(symbol="JM")[0].status == "active"


def test_no_downgrade_on_small_sample(svc, memory_svc):
    """注入 3 次全失败（比值 1.0）但样本 < 5 → 不降级，避免小样本误杀。"""
    _active_sem(memory_svc, "JM", "小样本规律", invoked=3, failed=3)
    result = svc.consolidate_semantics()
    assert result["downgraded"] == 0
    assert memory_svc.list_semantics(symbol="JM")[0].status == "active"
