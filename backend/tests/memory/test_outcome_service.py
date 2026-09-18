"""阶段 4 测试：复盘回填（memory-system-plan.md 10.5）。

覆盖 P0 口径：
- 交易日推进：5 日窗口跨周末，target 落在正确的交易日
- 换月缺口必须被复权修掉（这是阶段 4 最容易写反的一处）
- 区间内换月标记 rolled=True 且**不弃样**
- 复权因子取不到时降级为未复权并标记 adjust_degraded（仍不弃样）
- 无 CSV / 无价格 → unverifiable，绝不猜
- 未到期不回填
- 中性判据 ±0.5%
- MAE 计算
- compute_stats 分母不含 unverifiable / parse_failed
- 语义巩固门槛流转
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import List

import pandas as pd
import pytest

from models.memory import Episode, EpisodeStatus
from services.outcome_service import OutcomeService, RollAdjuster
from services.trade_calendar import TradeCalendar


# ─────────────────────────────────────────────────────────────
# 合成数据
# ─────────────────────────────────────────────────────────────

START = "2026-09-01"
ROLL_IDX = 10          # 第 11 个工作日换月
CONTRACT_OLD = "JM2609"
CONTRACT_NEW = "JM2610"

# 真实连续价格（假设不换月时的走势），index 2 处有一次回撤用于验 MAE
TRUE = [100, 101, 99, 101, 102, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114]
# 主连序列：换月日之后切到新合约，价格比旧合约高 3 点（凭空缺口）
RAW = [p if i < ROLL_IDX else p + 3 for i, p in enumerate(TRUE)]


def _workdays(n: int, start: str = START) -> List[str]:
    d = date.fromisoformat(start)
    out: List[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


@pytest.fixture
def days() -> List[str]:
    return _workdays(15)


@pytest.fixture
def calendar(tmp_path) -> TradeCalendar:
    """离线交易日历：fetcher 直接返回工作日月历，不联网。"""
    all_days = _workdays(150)
    return TradeCalendar(cache_path=tmp_path / "cal.json", fetcher=lambda: all_days)


@pytest.fixture
def adjuster(tmp_path) -> RollAdjuster:
    """换月因子：旧合约在换月日的收盘 = 真实连续价格 TRUE[ROLL_IDX]=110。"""
    def _fetch(contract: str, day: str):
        if contract == CONTRACT_OLD and day == _workdays(15)[ROLL_IDX]:
            return float(TRUE[ROLL_IDX])
        return None
    return RollAdjuster(cache_dir=tmp_path / "adj", fetcher=_fetch)


@pytest.fixture
def price_root(tmp_path, days):
    """JM：主连带换月缺口；FLAT：价格恒定（验中性判据）。"""
    ta = tmp_path / "technical_analysis" / "JM"
    ta.mkdir(parents=True)
    pd.DataFrame({"时间": days, "收盘": RAW}).to_csv(
        ta / "ohlc_data.csv", index=False, encoding="utf-8-sig"
    )
    mc = tmp_path / "main_contract" / "JM"
    mc.mkdir(parents=True)
    contracts = [CONTRACT_OLD if i < ROLL_IDX else CONTRACT_NEW for i in range(len(days))]
    pd.DataFrame(
        {"date": days, "symbol": "JM", "dominant_contract": contracts}
    ).to_csv(mc / "dominant_contract.csv", index=False, encoding="utf-8-sig")

    flat = tmp_path / "technical_analysis" / "FLAT"
    flat.mkdir(parents=True)
    pd.DataFrame({"时间": days, "收盘": [100.0] * len(days)}).to_csv(
        flat / "ohlc_data.csv", index=False, encoding="utf-8-sig"
    )
    return tmp_path


@pytest.fixture
def svc(memory_svc, price_root, calendar, adjuster) -> OutcomeService:
    return OutcomeService(
        svc=memory_svc, data_root=price_root, calendar=calendar, adjuster=adjuster
    )


def _ep(symbol, analysis_date, direction="long", horizon=5, conf=0.6) -> Episode:
    return Episode(
        symbol=symbol,
        analysis_date=analysis_date,
        run_id=f"r_{symbol}_{analysis_date}",
        direction=direction,
        direction_view="看多" if direction == "long" else ("看空" if direction == "short" else "中性"),
        confidence=conf,
        confidence_level="中",
        summary="测试结论",
        key_evidence=[],
        decision={},
        horizon=horizon,
        status=EpisodeStatus.PENDING.value,
        is_canonical=True,
    )


# ─────────────────────────────────────────────────────────────
# 交易日历
# ─────────────────────────────────────────────────────────────

def test_trading_days_after_skips_weekend(calendar, days):
    """5 个交易日 ≠ 5 个自然日：周五起算要跨两个周末。"""
    friday = days[4]  # 2026-09-01 起第 5 个工作日
    target = calendar.trading_days_after(friday, 5)
    assert target == days[9]
    assert (date.fromisoformat(target) - date.fromisoformat(friday)).days >= 7


def test_trading_days_after_returns_none_when_calendar_short(tmp_path):
    cal = TradeCalendar(cache_path=tmp_path / "c.json", fetcher=lambda: ["2026-09-01"])
    assert cal.trading_days_after("2026-09-01", 10) is None


# ─────────────────────────────────────────────────────────────
# 回填主流程
# ─────────────────────────────────────────────────────────────

def test_backfill_resolves_simple(svc, memory_svc, days):
    """不跨换月的区间：收益 = 105/100 - 1 = 5%，MAE = 1%（index 2 的回撤）。"""
    memory_svc.write_episode(_ep("JM", days[0], "long", horizon=5))
    stats = svc.backfill(as_of=days[10])
    assert stats["resolved"] == 1

    ep = memory_svc.get_recent_episodes("JM", limit=1)[0]
    assert ep.status == "resolved"
    assert abs(ep.outcome["realized_return"] - 0.05) < 1e-6
    assert ep.outcome["hit"] is True
    assert abs(ep.outcome["MAE"] - 0.01) < 1e-6
    assert ep.outcome["rolled"] is False
    assert ep.outcome["actual_contract"] == CONTRACT_OLD
    assert ep.outcome["holding_days"] == 5


def test_backfill_roll_adjusted(svc, memory_svc, days):
    """P0：换月缺口必须被修掉。

    区间跨换月：主连 raw 从 105 跳到 113（+7.6%），但旧合约只从 105 涨到 110，
    正确答案应是 110/105 - 1 ≈ 4.76%。若复权方向写反会得到 12.6% 之类的错值。
    """
    memory_svc.write_episode(_ep("JM", days[5], "long", horizon=5))
    svc.backfill(as_of=days[11])

    ep = memory_svc.get_recent_episodes("JM", limit=1)[0]
    assert ep.outcome["rolled"] is True
    assert abs(ep.outcome["realized_return"] - (110 / 105 - 1)) < 1e-6
    # 明确不等于未复权的错值
    assert abs(ep.outcome["realized_return"] - (113 / 105 - 1)) > 1e-3
    assert ep.outcome["adjust_degraded"] is False
    assert ep.outcome["actual_contract"] == CONTRACT_OLD


def test_roll_degraded_still_resolved(memory_svc, price_root, calendar, tmp_path, days):
    """拿不到旧合约价 → 按未复权算并标记 degraded，但**不弃样**（三评口径）。"""
    svc = OutcomeService(
        svc=memory_svc,
        data_root=price_root,
        calendar=calendar,
        adjuster=RollAdjuster(cache_dir=tmp_path / "adj2", fetcher=lambda c, d: None),
    )
    memory_svc.write_episode(_ep("JM", days[5], "long", horizon=5))
    stats = svc.backfill(as_of=days[11])

    assert stats["resolved"] == 1
    ep = memory_svc.get_recent_episodes("JM", limit=1)[0]
    assert ep.outcome["rolled"] is True
    assert ep.outcome["adjust_degraded"] is True
    assert abs(ep.outcome["realized_return"] - (113 / 105 - 1)) < 1e-6


def test_not_due_not_backfilled(svc, memory_svc, days):
    memory_svc.write_episode(_ep("JM", days[8], "long", horizon=10))
    stats = svc.backfill(as_of=days[10])
    assert stats["not_due"] == 1
    assert stats["resolved"] == 0
    ep = memory_svc.get_recent_episodes("JM", limit=1)[0]
    assert ep.status == "pending"


def test_no_csv_unverifiable(svc, memory_svc, days):
    """无 CSV：标 unverifiable，绝不猜（reason=no_csv）。"""
    memory_svc.write_episode(_ep("NOSUCH", days[0], "long", horizon=5))
    stats = svc.backfill(as_of=days[10])
    assert stats["unverifiable"] == 1

    ep = memory_svc.list_episodes_by_status("unverifiable")[0]
    assert ep.outcome["unverifiable_reason"] == "no_csv"


def test_no_price_unverifiable(svc, memory_svc, days):
    """有 CSV 但区间内取不到价格：reason=no_price（与 no_csv 区分）。"""
    memory_svc.write_episode(_ep("JM", "2020-01-02", "long", horizon=5))
    svc.backfill(as_of=days[10])
    ep = memory_svc.list_episodes_by_status("unverifiable")[0]
    assert ep.outcome["unverifiable_reason"] == "no_price"


def test_unverifiable_is_not_terminal(svc, memory_svc, days, price_root):
    """数据补齐后重扫：unverifiable 应恢复为 resolved（否则分母被系统性低估）。"""
    memory_svc.write_episode(_ep("LATE", days[0], "long", horizon=5))
    svc.backfill(as_of=days[10])
    assert (
        memory_svc.list_episodes_by_status("unverifiable")[0].outcome["unverifiable_reason"]
        == "no_csv"
    )

    ta = price_root / "technical_analysis" / "LATE"
    ta.mkdir(parents=True)
    pd.DataFrame({"时间": days, "收盘": [100 + i for i in range(len(days))]}).to_csv(
        ta / "ohlc_data.csv", index=False, encoding="utf-8-sig"
    )
    svc._series_cache.clear()  # 模拟新进程：缓存失效后重新读盘

    stats = svc.backfill(as_of=days[10])
    assert stats["recovered"] == 1
    assert not memory_svc.list_episodes_by_status("unverifiable")
    assert memory_svc.list_episodes_by_status("resolved")[0].outcome["hit"] is True


def test_contract_map_missing_flagged(svc, memory_svc, days):
    """无主力合约映射：显式标 contract_map_missing，不静默按未复权处理。"""
    memory_svc.write_episode(_ep("FLAT", days[0], "long", horizon=5))  # FLAT 无 main_contract
    svc.backfill(as_of=days[10])
    ep = memory_svc.list_episodes_by_status("resolved")[0]
    assert ep.outcome["contract_map_missing"] is True
    assert ep.outcome["actual_contract"] is None


def test_contract_map_present(svc, memory_svc, days):
    memory_svc.write_episode(_ep("JM", days[0], "long", horizon=5))
    svc.backfill(as_of=days[10])
    ep = memory_svc.list_episodes_by_status("resolved")[0]
    assert ep.outcome["contract_map_missing"] is False
    assert ep.outcome["actual_contract"] == CONTRACT_OLD


def test_short_direction(svc, memory_svc, days):
    memory_svc.write_episode(_ep("JM", days[0], "short", horizon=5))
    svc.backfill(as_of=days[10])
    ep = memory_svc.get_recent_episodes("JM", limit=1)[0]
    assert abs(ep.outcome["realized_return"] + 0.05) < 1e-6
    assert ep.outcome["hit"] is False
    assert abs(ep.outcome["MAE"] - 0.05) < 1e-6  # 做空的最大不利偏移 = 最大涨幅


def test_neutral_tolerance(svc, memory_svc, days):
    """中性判据：区间波动 ≤ ±0.5% 才算说对；方向判断错不算错。"""
    memory_svc.write_episode(_ep("FLAT", days[0], "neutral", horizon=5))
    memory_svc.write_episode(_ep("FLAT", days[1], "long", horizon=5))
    svc.backfill(as_of=days[10])

    eps = memory_svc.list_episodes_by_status("resolved")
    by_dir = {e.direction: e.outcome for e in eps}
    assert by_dir["neutral"]["hit"] is True
    assert abs(by_dir["neutral"]["realized_return"]) < 1e-9
    assert by_dir["long"]["hit"] is False  # 涨 0% 不算多头命中


# ─────────────────────────────────────────────────────────────
# 统计
# ─────────────────────────────────────────────────────────────

def test_stats_excludes_unverifiable_and_parse_failed(svc, memory_svc, days):
    """【三评 A】校准分母只含 resolved。"""
    memory_svc.write_episode(_ep("JM", days[0], "long", horizon=5))     # 命中
    memory_svc.write_episode(_ep("JM", days[1], "short", horizon=5))    # 未命中
    memory_svc.write_episode(_ep("NOSUCH", days[0], "long", horizon=5))  # unverifiable
    bad = _ep("JM", days[2], "long", horizon=5)
    bad.status = EpisodeStatus.PARSE_FAILED.value
    memory_svc.write_episode(bad)

    svc.backfill(as_of=days[10])
    stats = svc.compute_stats(persist=False)

    assert stats["n_resolved"] == 2
    assert stats["n_unverifiable"] == 1
    assert abs(stats["hit_rate"] - 0.5) < 1e-9
    assert stats["by_direction"]["long"]["hit_rate"] == 1.0
    assert stats["by_direction"]["short"]["hit_rate"] == 0.0


def test_stats_calibration_bins(svc, memory_svc, days):
    # 不同日期各一条（同一 symbol+date+horizon 有部分唯一索引，会互相替换）
    for i, conf in enumerate((0.1, 0.3, 0.5, 0.75, 0.9)):
        memory_svc.write_episode(_ep("JM", days[i], "long", horizon=5, conf=conf))
    svc.backfill(as_of=days[10])
    stats = svc.compute_stats(persist=False)
    assert stats["n_resolved"] == 5
    assert len(stats["by_confidence"]) >= 3
    assert all(0.0 <= b["hit_rate"] <= 1.0 for b in stats["by_confidence"])
    # 每档都带 avg_confidence / gap，供验收指标判定
    assert all("avg_confidence" in b and "gap" in b for b in stats["by_confidence"])


def test_stats_ece_and_max_bin_gap(svc, memory_svc, days):
    """【三评 J】ECE 与最大分档偏差必须可量化，否则"置信度是否可信"无法验收。

    构造：0.9 档 4 条全命中（gap≈0.1）、0.5 档 4 条全未命中（gap≈0.5）。
    ECE = 0.5×0.1 + 0.5×0.5 = 0.3，max_bin_gap = 0.5。
    """
    for i in range(4):  # 高置信全中
        memory_svc.write_episode(_ep("JM", days[i], "long", horizon=5, conf=0.9))
    for i in range(4, 8):  # 中置信全错（做空，JM 在涨）
        memory_svc.write_episode(_ep("JM", days[i], "short", horizon=5, conf=0.5))

    svc.backfill(as_of=days[14])
    stats = svc.compute_stats(persist=False)

    assert stats["n_resolved"] == 8
    assert stats["ece"] == pytest.approx(0.3, abs=0.02)
    assert stats["max_bin_gap"] == pytest.approx(0.5, abs=0.02)


def test_stats_ece_zero_when_well_calibrated(svc, memory_svc, days):
    """校准良好（说 0.9、10 条中 9 条 → 实际 90%）→ 分档无偏差，ECE = 0。"""
    # 直接用 outcome 落库，避开合成价格的可控性限制
    for i in range(10):
        ep = _ep("JM", days[i], "long", horizon=5, conf=0.9)
        memory_svc.write_episode(ep)
    eps = memory_svc.list_episodes_by_status("pending")
    for i, e in enumerate(eps):
        memory_svc.update_outcome(
            e.id, {"hit": i < 9, "realized_return": 0.01}, EpisodeStatus.RESOLVED.value
        )

    stats = svc.compute_stats(persist=False)
    assert stats["n_resolved"] == 10
    assert abs(stats["ece"]) < 1e-9        # |0.9 - 0.9| = 0
    assert stats["max_bin_gap"] < 1e-9


# ─────────────────────────────────────────────────────────────
# 语义巩固
# ─────────────────────────────────────────────────────────────

def test_consolidate_promotes_when_evidence_enough(svc, memory_svc, days):
    from models.memory import Semantic, SemanticStatus

    for i in range(3):  # 3 条 resolved → 达到 SEMANTIC_MIN_EVIDENCE
        memory_svc.write_episode(_ep("JM", days[i], "long", horizon=5))
    svc.backfill(as_of=days[10])

    sem_id = memory_svc.write_semantic(
        Semantic(
            symbol="JM",
            claim="库存去化通常伴随基差走强",
            category="规律",
            status=SemanticStatus.DRAFT.value,
        )
    )
    result = svc.consolidate_semantics(llm_verify=lambda sem, n: True)

    assert result["promoted"] == 1
    assert memory_svc.list_semantics(symbol="JM")[0].status == "pending"
    assert memory_svc.list_semantics(symbol="JM")[0].evidence_count == 3


def test_consolidate_skips_when_evidence_short(svc, memory_svc, days):
    from models.memory import Semantic, SemanticStatus

    memory_svc.write_episode(_ep("JM", days[0], "long", horizon=5))
    svc.backfill(as_of=days[10])
    memory_svc.write_semantic(
        Semantic(
            symbol="JM",
            claim="证据不足的规律",
            category="规律",
            status=SemanticStatus.DRAFT.value,
        )
    )
    result = svc.consolidate_semantics(llm_verify=lambda sem, n: True)
    assert result["promoted"] == 0
    assert result["skipped"] == 1
    assert memory_svc.list_semantics(symbol="JM")[0].status == "draft"
