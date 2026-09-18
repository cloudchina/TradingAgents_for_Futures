"""阶段 2 测试：品种关联图谱（memory-system-plan.md 10.2）。

覆盖 P0 口径：
- 【二评】无序对称：pair_key 规范化、查两端互为镜像、lag 文案方向相反
- 【二评】最小样本门槛：13 行不算 60 日相关、100 行不算 120 日与 lag
- 【二评】显著性门槛：|corr| < 0.3 不写 lead_symbol
- 【三评 H】多元组展开且不定义 lead
- strength 缺失回退 0.5（禁止回退 0）
- lag 计算正确性（合成 A 领先 B / A 滞后 B）
- 无 CSV 静态回退、关联段预算与陈旧标记
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path
from typing import List

import pytest

from models.memory import RelationMetric
from services.relation_service import (
    RelationService,
    best_lag,
    pair_key_of,
    pearson,
    to_returns,
)
from services.trade_calendar import TradeCalendar


# ─────────────────────────────────────────────────────────────
# 合成数据
# ─────────────────────────────────────────────────────────────

BASE_YAML = """relations:
  - members: [RB, HC]
    type: substitute
    strength: 0.9
  - members: [JM, J]
    type: upstream
    strength: 0.9
    lead: JM
  - members: [Y, P, OI]
    type: substitute
    strength: 0.8
  - members: [SC, FU, LU, BU]
    type: chain
    strength: 0.8
"""


def _workdays(n: int, start: str = "2025-01-01") -> List[str]:
    d = date.fromisoformat(start)
    out: List[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _write_ohlc(root: Path, symbol: str, days: List[str], closes: List[float]) -> None:
    d = root / "technical_analysis" / symbol
    d.mkdir(parents=True, exist_ok=True)
    with (d / "ohlc_data.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["时间", "开盘", "收盘", "成交量"])
        for day, c in zip(days, closes):
            w.writerow([day.replace("-", ""), c, c, 1000])


def _prices_from_returns(rets: List[float], base: float = 100.0) -> List[float]:
    out = [base]
    for r in rets:
        out.append(out[-1] * (1 + r))
    return out[1:]


def _rand(n: int, seed: int = 7) -> List[float]:
    rnd = random.Random(seed)
    return [rnd.uniform(-0.02, 0.02) for _ in range(n)]


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def config(tmp_path: Path) -> Path:
    p = tmp_path / "relations.yaml"
    p.write_text(BASE_YAML, encoding="utf-8")
    return p


@pytest.fixture
def calendar(tmp_path: Path) -> TradeCalendar:
    days = _workdays(400)
    return TradeCalendar(cache_path=tmp_path / "cal.json", fetcher=lambda: days)


@pytest.fixture
def svc_rel(memory_svc, data_root: Path, config: Path, calendar: TradeCalendar):
    return RelationService(
        svc=memory_svc, data_root=data_root, config_path=config, calendar=calendar
    )


# ─────────────────────────────────────────────────────────────
# 纯函数
# ─────────────────────────────────────────────────────────────

def test_pearson_basic():
    assert pearson([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert pearson([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert pearson([1, 1, 1], [1, 2, 3]) is None  # 零方差


def test_pair_key_is_order_independent():
    assert pair_key_of("RB", "HC") == pair_key_of("HC", "RB") == "HC-RB"
    assert pair_key_of("JM", "J") == "J-JM"


def test_best_lag_positive_and_negative():
    """A 领先 B 1 日 → lag=+1；A 滞后 B 2 日 → lag=-2。"""
    r = _rand(160, seed=11)
    # A 领先 B 1 日：B 的收益是 A 的收益延后 1 期
    a = r
    b = [0.0] + r[:-1]
    lag, corr = best_lag(a, b)
    assert lag == 1
    assert corr > 0.9

    # A 滞后 B 2 日：A 的收益是 B 的收益延后 2 期
    b2 = r
    a2 = [0.0, 0.0] + r[:-2]
    lag2, corr2 = best_lag(a2, b2)
    assert lag2 == -2
    assert corr2 > 0.9


def test_to_returns_length():
    assert len(to_returns([100, 110, 99])) == 2
    assert to_returns([100, 110])[0] == pytest.approx(0.1)


# ─────────────────────────────────────────────────────────────
# 静态关系加载
# ─────────────────────────────────────────────────────────────

def test_multiple_member_expansion(svc_rel: RelationService):
    """【三评 H】三元组 → 3 条无序对，四元组 → 6 条。"""
    rels = svc_rel.load_static()
    y_p_oi = [r for r in rels if r.pair_key in {"OI-P", "OI-Y", "P-Y"}]
    assert len(y_p_oi) == 3
    chain = [r for r in rels if {"SC", "FU", "LU", "BU"} & {r.a, r.b} and r.relation_type == "chain"]
    assert len(chain) == 6
    # 多元组不定义 lead（即使 yaml 里写了也不得生效）
    assert all(r.lead is None for r in chain)


def test_strength_missing_falls_back_to_half(tmp_path, data_root, memory_svc, calendar):
    """【二评】strength 缺失/为 0 必须回退 0.5，禁止回退 0（乘式会让整条边消失）。"""
    cfg = tmp_path / "r.yaml"
    cfg.write_text(
        "relations:\n"
        "  - members: [A, B]\n"
        "    type: chain\n"
        "    strength: 0\n"
        "  - members: [C, D]\n"
        "    type: chain\n",
        encoding="utf-8",
    )
    svc = RelationService(svc=memory_svc, data_root=data_root, config_path=cfg, calendar=calendar)
    rels = {r.pair_key: r for r in svc.load_static()}
    assert rels["A-B"].strength == pytest.approx(0.5)
    assert rels["C-D"].strength == pytest.approx(0.5)


# ─────────────────────────────────────────────────────────────
# 最小样本 / 显著性门槛
# ─────────────────────────────────────────────────────────────

def test_min_sample_60d_not_computed(svc_rel: RelationService, data_root: Path):
    """13 行数据：收益率只有 12 条 < 60 → 只落 static_only，corr 为 None。"""
    days = _workdays(13)
    _write_ohlc(data_root, "JM", days, [100 + i for i in range(13)])
    _write_ohlc(data_root, "J", days, [200 + 2 * i for i in range(13)])
    metrics = svc_rel.compute_pair(svc_rel.load_static()[1])
    assert len(metrics) == 1
    m = metrics[0]
    assert m.corr_source == "static_only"
    assert m.corr is None
    assert m.lead_symbol is None
    assert m.sample_size == 12


def test_min_sample_120d_and_lag(svc_rel: RelationService, data_root: Path):
    """100 行：算 60 日相关，但不算 120 日与 lag。"""
    days = _workdays(100)
    r = _rand(100)
    _write_ohlc(data_root, "JM", days, _prices_from_returns(r))
    _write_ohlc(data_root, "J", days, _prices_from_returns([0.0] + r[:-1]))
    metrics = svc_rel.compute_pair(svc_rel.load_static()[1])
    windows = {m.window for m in metrics}
    assert windows == {60}
    assert metrics[0].corr_source == "dynamic"
    assert metrics[0].corr is not None
    # 构造是 JM 领先 J 1 日，故同期相关本来就很低（这正是需要 lag 的原因）
    assert abs(metrics[0].corr) < 0.3
    # lag 需要 150 样本，此处不写
    assert metrics[0].lead_symbol is None
    assert metrics[0].lag_days is None


def test_120d_and_lag_computed_when_enough(svc_rel: RelationService, data_root: Path):
    """200 行：60/120 都算，且 lag 在 120 窗口上写入 lead。"""
    days = _workdays(200)
    r = _rand(200, seed=3)
    _write_ohlc(data_root, "JM", days, _prices_from_returns(r))
    _write_ohlc(data_root, "J", days, _prices_from_returns([0.0] + r[:-1]))
    metrics = {m.window: m for m in svc_rel.compute_pair(svc_rel.load_static()[1])}
    assert set(metrics) == {60, 120}
    m120 = metrics[120]
    # pair_key="J-JM" → a="J"、b="JM"；JM 领先 → 负号（b 领先）
    assert m120.lead_symbol == "JM"
    assert m120.lag_days == -1
    # lead 能写入说明 lag 处的 |corr| 已达 0.3 门槛（同期相关反而低）
    assert abs(m120.corr) < 0.3


def test_lead_requires_significance(svc_rel: RelationService, data_root: Path):
    """|corr| 未达 0.3 → 不写 lead_symbol（防过拟合的领先结论）。"""
    days = _workdays(220)
    ra = _rand(220, seed=21)
    rb = _rand(220, seed=22)
    _write_ohlc(data_root, "JM", days, _prices_from_returns(ra))
    _write_ohlc(data_root, "J", days, _prices_from_returns(rb))
    metrics = {m.window: m for m in svc_rel.compute_pair(svc_rel.load_static()[1])}
    m120 = metrics[120]
    assert abs(m120.corr) < 0.3
    assert m120.lead_symbol is None
    assert m120.lag_days is None


def test_no_csv_falls_back_to_static_only(svc_rel: RelationService):
    """无 technical CSV → static_only、corr 为 NULL、sample_size=0。"""
    metrics = svc_rel.compute_pair(svc_rel.load_static()[0])  # RB-HC 无数据
    assert len(metrics) == 1
    assert metrics[0].corr_source == "static_only"
    assert metrics[0].corr is None
    assert metrics[0].sample_size == 0


def test_refresh_persists_and_reports(svc_rel: RelationService, data_root: Path, memory_svc):
    days = _workdays(200)
    r = _rand(200, seed=5)
    _write_ohlc(data_root, "JM", days, _prices_from_returns(r))
    _write_ohlc(data_root, "J", days, _prices_from_returns([0.0] + r[:-1]))
    stats = svc_rel.refresh(persist=True)
    assert stats["dynamic"] >= 2          # JM-J 的 60/120 两条
    assert stats["static_only"] >= 1      # 其余无数据 pair
    rows = memory_svc.get_relation_metrics()
    assert rows
    assert all(isinstance(r, RelationMetric) for r in rows)


# ─────────────────────────────────────────────────────────────
# 对称性与方向文案
# ─────────────────────────────────────────────────────────────

def test_related_is_mirrored(svc_rel: RelationService, memory_svc):
    """查 J 得 JM、查 JM 得 J，且 corr 一致。"""
    memory_svc.save_relation_metric(
        RelationMetric(
            pair_key="J-JM", a="J", b="JM", window=120,
            corr=0.88, lead_symbol="JM", lag_days=-1,
            corr_source="dynamic", sample_size=120,
        )
    )
    for_j = svc_rel.get_related("J", "2025-01-01")
    for_jm = svc_rel.get_related("JM", "2025-01-01")
    assert [x.symbol for x in for_j] == ["JM"]
    assert [x.symbol for x in for_jm] == ["J"]
    assert for_j[0].corr == for_jm[0].corr == pytest.approx(0.88)
    # 同一条关系，两端文案方向相反
    assert "领先" in for_j[0].lead_text and "JM" in for_j[0].lead_text
    assert "滞后" in for_jm[0].lead_text and "J" in for_jm[0].lead_text


def test_related_relevance_uses_strength_when_no_dynamic(svc_rel: RelationService, memory_svc):
    """无动态相关时 |corr| 回退为静态 strength（【二评】子公式）。"""
    items = svc_rel.get_related("RB", "2025-01-01")
    assert items and items[0].symbol == "HC"
    assert items[0].corr is None
    assert items[0].corr_source == "static_only"
    # strength=0.9 → corr_eff=0.9，无 episode → recency=exp(-10/10)
    expected = 0.9 * 0.9 * pow(2.718281828459045, -1.0)
    assert items[0].related_relevance == pytest.approx(expected, rel=1e-3)


def test_graph_nodes_and_edges(svc_rel: RelationService, memory_svc):
    g = svc_rel.graph()
    symbols = {n["symbol"] for n in g["nodes"]}
    assert {"RB", "HC", "JM", "J"} <= symbols
    assert any(e["pair_key"] == "HC-RB" for e in g["edges"])


# ─────────────────────────────────────────────────────────────
# 关联段
# ─────────────────────────────────────────────────────────────

def test_build_segment_within_budget(svc_rel: RelationService, memory_svc, data_root: Path):
    """关联段含对端品种、近 5 日涨跌，且不超过预算。"""
    days = _workdays(60)
    _write_ohlc(data_root, "HC", days, [100 + i * 0.5 for i in range(60)])
    seg = svc_rel.build_segment("RB", days[-1])
    assert "HC" in seg
    assert len(seg) <= 400
    assert "静态关系" in seg  # 无 relation_metrics → 静态回退文案


def test_stale_marked_when_peer_episode_old(svc_rel: RelationService, memory_svc):
    from models.memory import Episode

    memory_svc.write_episode(
        Episode(
            symbol="HC", analysis_date="2024-01-01", run_id="r1", direction="long",
            direction_view="看多", confidence=0.6, confidence_level="中",
            horizon=5, status="resolved", is_canonical=True,
        )
    )
    items = svc_rel.get_related("RB", "2025-06-01")
    assert items and items[0].last_episode is not None
    assert items[0].stale is True
    assert "陈旧" in svc_rel.build_segment("RB", "2025-06-01")


def test_segment_excludes_future_prices(svc_rel: RelationService, memory_svc, data_root: Path):
    """近 5 日涨跌不得用到 analysis_date 之后的价格（未来函数）。"""
    days = _workdays(30)
    closes = [100.0] * 20 + [200.0] * 10     # 后 10 天暴涨
    _write_ohlc(data_root, "HC", days, closes)
    items = svc_rel.get_related("RB", days[19])
    assert items[0].recent_return == pytest.approx(0.0)
