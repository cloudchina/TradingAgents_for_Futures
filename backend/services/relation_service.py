"""品种关联图谱（阶段 2，见 memory-system-plan.md 二.3 / 四.阶段2 / 10.2）。

静态关系（人工维护的 yaml）+ 动态相关（由 technical_analysis 收盘价算得的滚动相关系数
与领先滞后 lag）融合后，给 agent 注入"关联品种正在发生什么"。

必须守住的三条硬约束（【二评】）：
1. 关系**无序对称**：`pair_key = "-".join(sorted([a, b]))`，分析任意一端都命中同一条记录；
   方向由 `lead_symbol` + `lag_days` 表达，查询时按锚点翻译文案。
2. **最小样本门槛**：收益率样本 < 60 不算 60 日相关；< 150 不算 120 日相关与 lag。
3. **显著性门槛**：只有 `|corr| >= 0.3` 才落 `lead_symbol` / `lag_days`，
   否则 60 日窗口上算 lag∈[-3,+3] 会过拟合出"JM 领先 J 1 日"这类无统计支撑的结论。

`lag_days` 的符号约定（相对 pair 内 `a`/`b` 的书写顺序，不是相对查询品种）：
- 正 = `a` 领先 `b` |lag| 个交易日；负 = `b` 领先 `a` |lag| 个交易日。
- `lead_symbol` 始终指向**领先方**，与符号冗余但便于阅读与 SQL 过滤。
- 展示时按"当前被分析品种"翻译：对端领先 → "领先本品种 N 日"，本品种领先 → "滞后 N 日"。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml
from loguru import logger

from core.settings import settings
from models.memory import RelationMetric
from services.memory_service import (
    MIN_CORR_FOR_LEAD,
    MIN_SAMPLE_120D,
    MIN_SAMPLE_60D,
    STM_WINDOW,
    TOKEN_BUDGET,
    memory_service,
)
from services.trade_calendar import TradeCalendar, trade_calendar

# 关联段 recency 衰减常数（τ=10，与 STM 窗口对齐）
RELATION_RECENCY_TAU = 10.0

# 关联段最多注入的品种数（【二评】关联段独立排序，top-3）
RELATION_TOP_K = 3

# lag 搜索范围
MAX_LAG = 3

# 计算 lag 所需的最小样本（同 120 日窗口门槛）
MIN_SAMPLE_FOR_LAG = MIN_SAMPLE_120D

# 关系类型 → 中文
RELATION_TYPE_CN = {
    "substitute": "替代/套利",
    "upstream": "上游原料",
    "chain": "产业链",
    "macro": "宏观同向",
}

# 静态 strength 缺失时的回退值（**禁止回退 0**：related_relevance 是乘式，0 会让整条边消失）
DEFAULT_STRENGTH = 0.5

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "commodity_relations.yaml"


# ─────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────

@dataclass
class StaticRelation:
    """静态关系（members 展开后的一条无序对）。"""
    a: str
    b: str
    pair_key: str
    relation_type: str = ""
    strength: float = DEFAULT_STRENGTH
    lead: Optional[str] = None        # 仅二元组有
    lead_hint: str = ""
    note: str = ""

    def peer_of(self, symbol: str) -> Optional[str]:
        sym = symbol.upper()
        if sym == self.a:
            return self.b
        if sym == self.b:
            return self.a
        return None


@dataclass
class RelatedInfo:
    """get_related / build_segment 的输出项（已按锚点翻译）。"""
    symbol: str                                  # 对端品种
    pair_key: str
    relation_type: str = ""
    relation_type_cn: str = ""
    strength: float = DEFAULT_STRENGTH
    corr: Optional[float] = None
    corr_source: str = "static_only"
    lead_symbol: Optional[str] = None
    lag_days: Optional[int] = None
    sample_size: int = 0
    window: Optional[int] = None
    related_relevance: float = 0.0
    recent_return: Optional[float] = None        # 近 5 个交易日涨跌
    lead_text: str = ""                          # 相对当前品种的方向文案
    last_episode: Optional[Dict[str, Any]] = None
    stale: bool = False                          # 对端结论距今 > STM_WINDOW 交易日
    note: str = ""


# ─────────────────────────────────────────────────────────────
# 纯计算函数（便于单测，无 IO）
# ─────────────────────────────────────────────────────────────

def pair_key_of(a: str, b: str) -> str:
    """无序对规范化：("-".join(sorted([a, b])))。"""
    return "-".join(sorted([a.upper().strip(), b.upper().strip()]))


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """皮尔逊相关系数；样本不足或零方差返回 None。"""
    n = len(xs)
    if n != len(ys) or n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sxx = syy = 0.0
    for x, y in zip(xs, ys):
        dx = x - mx
        dy = y - my
        sxy += dx * dy
        sxx += dx * dx
        syy += dy * dy
    if sxx <= 0 or syy <= 0:
        return None
    c = sxy / math.sqrt(sxx * syy)
    return max(-1.0, min(1.0, c))


def to_returns(prices: Sequence[float]) -> List[float]:
    """日收益率序列（长度 = len(prices) - 1）。"""
    return [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices)) if prices[i - 1]]


def best_lag(
    ret_a: Sequence[float], ret_b: Sequence[float], max_lag: int = MAX_LAG
) -> Optional[Tuple[int, float]]:
    """交叉相关搜领先滞后。

    Args:
        ret_a: a 的日收益率（时间升序）
        ret_b: b 的日收益率

    Returns:
        (lag_days, corr)：lag_days > 0 表示 **a 领先 b** |lag| 日；< 0 表示 a 滞后 b。
        取 |corr| 最大的偏移量（负相关同样可能是真实的领先关系）。
    """
    n = min(len(ret_a), len(ret_b))
    if n < 10:
        return None
    best: Optional[Tuple[int, float]] = None
    for k in range(-max_lag, max_lag + 1):
        if k >= 0:
            xs = list(ret_a[: n - k])
            ys = list(ret_b[k:])
        else:
            xs = list(ret_a[-k:])
            ys = list(ret_b[: n + k])
        c = pearson(xs, ys)
        if c is None:
            continue
        if best is None or abs(c) > abs(best[1]):
            best = (k, c)
    return best


def align_closes(
    closes_a: Dict[str, float], closes_b: Dict[str, float]
) -> Tuple[List[str], List[float], List[float]]:
    """按共同交易日对齐两个品种的收盘价（时间升序）。"""
    common = sorted(set(closes_a) & set(closes_b))
    return common, [closes_a[d] for d in common], [closes_b[d] for d in common]


# ─────────────────────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────────────────────

class RelationService:
    """静态关系加载 + 动态相关计算 + 关联段拼装。"""

    def __init__(
        self,
        svc: Any = None,
        data_root: Optional[Path] = None,
        config_path: Optional[Path] = None,
        calendar: Optional[TradeCalendar] = None,
    ):
        self.svc = svc or memory_service
        self.root = Path(data_root) if data_root else Path(settings.DATA_ROOT_DIR)
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.calendar = calendar or trade_calendar
        self._closes_cache: Dict[str, Optional[Dict[str, float]]] = {}
        self._static_cache: Optional[List[StaticRelation]] = None

    # ─── 静态关系 ───

    def load_static(self, refresh: bool = False) -> List[StaticRelation]:
        """读 yaml 并把 members 展开为 C(n,2) 条无序对。

        - strength 缺失或为 0 → 回退 0.5（**不得为 0**，否则 related_relevance 连乘后整条边消失）
        - 【三评 H】lead 只在二元组生效，多元组的方向由 relation_metrics 按 pair 单独算
        """
        if self._static_cache is not None and not refresh:
            return self._static_cache

        out: List[StaticRelation] = []
        if not self.config_path.exists():
            logger.warning(f"[relation] 静态关系配置缺失: {self.config_path}")
            self._static_cache = out
            return out
        try:
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning(f"[relation] 静态关系配置解析失败 {e}")
            self._static_cache = out
            return out

        seen = set()
        for item in raw.get("relations", []) or []:
            members = [str(m).upper().strip() for m in (item.get("members") or [])]
            if len(members) < 2:
                continue
            try:
                strength = float(item.get("strength") or 0)
            except (TypeError, ValueError):
                strength = 0.0
            if strength <= 0:
                strength = DEFAULT_STRENGTH
            lead = item.get("lead")
            lead = str(lead).upper().strip() if lead else None
            if lead and len(members) > 2:
                # 多元组定义 lead 无法表达多品种 lead-lag，丢弃避免误导
                logger.debug(f"[relation] 多元组 {members} 的 lead 被忽略（三评 H）")
                lead = None
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = sorted([members[i], members[j]])
                    key = pair_key_of(a, b)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(
                        StaticRelation(
                            a=a,
                            b=b,
                            pair_key=key,
                            relation_type=str(item.get("type") or ""),
                            strength=strength,
                            lead=lead,
                            lead_hint=str(item.get("lead_hint") or ""),
                            note=str(item.get("note") or ""),
                        )
                    )
        self._static_cache = out
        return out

    # ─── 价格读取 ───

    def ohlc_path(self, symbol: str) -> Path:
        return self.root / "technical_analysis" / symbol.upper() / "ohlc_data.csv"

    def load_closes(self, symbol: str) -> Optional[Dict[str, float]]:
        """读主连 OHLC 收盘价（日期 → 收盘）。

        与阶段 4 回填同一份文件（后复权连续序列），保证相关性与复盘口径一致。
        """
        sym = symbol.upper()
        if sym in self._closes_cache:
            return self._closes_cache[sym]
        path = self.ohlc_path(sym)
        if not path.exists():
            self._closes_cache[sym] = None
            return None
        try:
            import csv

            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    self._closes_cache[sym] = None
                    return None
                heads = [h.strip() for h in reader.fieldnames]
                date_col = next((h for h in heads if h in ("时间", "日期", "date")), None)
                close_col = next((h for h in heads if h in ("收盘", "close")), None)
                if not date_col or not close_col:
                    self._closes_cache[sym] = None
                    return None
                mapping: Dict[str, float] = {}
                for row in reader:
                    d = _norm_date(str(row.get(date_col) or ""))
                    try:
                        v = float(str(row.get(close_col)).replace(",", ""))
                    except (TypeError, ValueError):
                        continue
                    if d and v > 0:
                        mapping[d] = v
        except Exception as e:
            logger.warning(f"[relation] {sym} 读 OHLC 失败 {e}")
            self._closes_cache[sym] = None
            return None
        result = mapping or None
        self._closes_cache[sym] = result
        return result

    # ─── 动态相关计算 ───

    def compute_pair(
        self,
        rel: StaticRelation,
        windows: Sequence[int] = (60, 120),
    ) -> List[RelationMetric]:
        """算一条 pair 的动态相关；未达门槛落 `corr_source='static_only'`。

        门槛（【二评】）：收益率样本 < 60 不算 60 日相关；< 150 不算 120 日相关与 lag。
        全部窗口都不达标时落一条 window=60 的 static_only 记录（保留 sample_size 便于诊断）。
        """
        closes_a = self.load_closes(rel.a)
        closes_b = self.load_closes(rel.b)
        now = datetime.now().isoformat(timespec="seconds")

        if not closes_a or not closes_b:
            return [
                RelationMetric(
                    pair_key=rel.pair_key, a=rel.a, b=rel.b, window=min(windows),
                    corr=None, lead_symbol=None, lag_days=None,
                    corr_source="static_only", sample_size=0, updated_at=now,
                )
            ]

        _, pa, pb = align_closes(closes_a, closes_b)
        ret_a = to_returns(pa)
        ret_b = to_returns(pb)
        n = min(len(ret_a), len(ret_b))
        if n < 3:
            return [
                RelationMetric(
                    pair_key=rel.pair_key, a=rel.a, b=rel.b, window=min(windows),
                    corr=None, lead_symbol=None, lag_days=None,
                    corr_source="static_only", sample_size=n, updated_at=now,
                )
            ]
        ret_a = ret_a[-n:]
        ret_b = ret_b[-n:]

        metrics: List[RelationMetric] = []
        for w in sorted(windows, reverse=True):
            min_n = MIN_SAMPLE_60D if w == 60 else MIN_SAMPLE_120D
            if n < min_n:
                continue
            size = min(w, n)
            corr = pearson(ret_a[-size:], ret_b[-size:])
            if corr is None:
                continue
            lead_symbol: Optional[str] = None
            lag_days: Optional[int] = None
            # lag 只在 120 日窗口（样本 ≥150）上算，60 日窗口上极易过拟合
            if n >= MIN_SAMPLE_FOR_LAG and w >= 120:
                found = best_lag(ret_a[-size:], ret_b[-size:])
                if found and abs(found[1]) >= MIN_CORR_FOR_LEAD:
                    k, _ = found
                    lag_days = int(k)
                    if k > 0:
                        lead_symbol = rel.a
                    elif k < 0:
                        lead_symbol = rel.b
                    else:
                        lead_symbol = rel.lead or rel.a
            metrics.append(
                RelationMetric(
                    pair_key=rel.pair_key, a=rel.a, b=rel.b, window=w,
                    corr=corr, lead_symbol=lead_symbol, lag_days=lag_days,
                    corr_source="dynamic", sample_size=size, updated_at=now,
                )
            )
        if not metrics:
            metrics.append(
                RelationMetric(
                    pair_key=rel.pair_key, a=rel.a, b=rel.b, window=min(windows),
                    corr=None, lead_symbol=None, lag_days=None,
                    corr_source="static_only", sample_size=n, updated_at=now,
                )
            )
        return metrics

    def refresh(
        self,
        symbols: Optional[List[str]] = None,
        windows: Sequence[int] = (60, 120),
        persist: bool = True,
    ) -> Dict[str, Any]:
        """重算并落库全量（或指定品种的）关联指标。

        symbols=None → 全部静态 pair；否则只算涉及这些品种的 pair。
        """
        pairs = self.load_static()
        if symbols:
            wanted = {s.upper() for s in symbols}
            pairs = [p for p in pairs if p.a in wanted or p.b in wanted]
        stats: Dict[str, Any] = {
            "pairs": len(pairs),
            "dynamic": 0,
            "static_only": 0,
            "no_csv": [],
        }
        for rel in pairs:
            try:
                metrics = self.compute_pair(rel, windows)
            except Exception as e:
                logger.warning(f"[relation] {rel.pair_key} 计算失败 {e}")
                continue
            for m in metrics:
                if persist:
                    try:
                        self.svc.save_relation_metric(m)
                    except Exception as e:
                        logger.warning(f"[relation] {m.pair_key} 落库失败 {e}")
                        continue
                if m.corr_source == "dynamic":
                    stats["dynamic"] += 1
                else:
                    stats["static_only"] += 1
                    if m.sample_size == 0:
                        stats["no_csv"].extend(
                            [s for s in (rel.a, rel.b) if not self.load_closes(s)]
                        )
        stats["no_csv"] = sorted(set(stats["no_csv"]))
        return stats

    # ─── 查询 ───

    def get_related(
        self,
        symbol: str,
        analysis_date: Optional[str] = None,
        top_k: int = RELATION_TOP_K,
    ) -> List[RelatedInfo]:
        """取当前品种的关联品种（含实测相关、近 5 日涨跌、对端最近结论）。

        排序按 `related_relevance = strength × |corr| × recency_factor` 降序，
        **不参与本品种 episode 的 score 竞争**（【二评】两套逻辑不得混用）。
        """
        sym = symbol.upper()
        day = analysis_date or date.today().isoformat()

        metrics_by_pair: Dict[str, RelationMetric] = {}
        try:
            for m in self.svc.get_relation_metrics():
                # 同一 pair 多窗口时取窗口最大的一条（120 更稳健，缺失则 60）
                cur = metrics_by_pair.get(m.pair_key)
                if cur is None or (m.window or 0) > (cur.window or 0):
                    metrics_by_pair[m.pair_key] = m
        except Exception as e:
            logger.warning(f"[relation] 读 relation_metrics 失败 {e}")

        out: List[RelatedInfo] = []
        for rel in self.load_static():
            peer = rel.peer_of(sym)
            if not peer:
                continue
            metric = metrics_by_pair.get(rel.pair_key)
            corr = metric.corr if metric else None
            corr_source = metric.corr_source if metric else "static_only"
            # 无动态相关时 |corr| 回退为静态 strength（【二评】相关段子公式）
            corr_eff = abs(corr) if corr is not None else rel.strength

            last_ep = self._last_episode(peer, day)
            delta_t = self._trading_days_since(last_ep.get("analysis_date") if last_ep else None, day)
            recency = math.exp(-delta_t / RELATION_RECENCY_TAU)

            info = RelatedInfo(
                symbol=peer,
                pair_key=rel.pair_key,
                relation_type=rel.relation_type,
                relation_type_cn=RELATION_TYPE_CN.get(rel.relation_type, rel.relation_type),
                strength=rel.strength,
                corr=corr,
                corr_source=corr_source,
                lead_symbol=(metric.lead_symbol if metric else None) or rel.lead,
                lag_days=(metric.lag_days if metric else None),
                sample_size=metric.sample_size if metric else 0,
                window=metric.window if metric else None,
                recent_return=self._recent_return(peer, day),
                last_episode=last_ep,
                note=rel.note,
            )
            info.related_relevance = rel.strength * corr_eff * recency
            info.lead_text = self._lead_text(info, sym, rel)
            info.stale = bool(last_ep) and delta_t > STM_WINDOW
            out.append(info)

        out.sort(key=lambda x: -x.related_relevance)
        return out[:top_k]

    def graph(self) -> Dict[str, Any]:
        """关联图谱（阶段 5 前端用）：节点 + 边（含实测 corr / lead）。"""
        metrics_by_pair: Dict[str, RelationMetric] = {}
        try:
            for m in self.svc.get_relation_metrics():
                cur = metrics_by_pair.get(m.pair_key)
                if cur is None or (m.window or 0) > (cur.window or 0):
                    metrics_by_pair[m.pair_key] = m
        except Exception as e:
            logger.warning(f"[relation] 读 relation_metrics 失败 {e}")

        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []
        for rel in self.load_static():
            nodes.setdefault(rel.a, {"symbol": rel.a, "degree": 0})
            nodes.setdefault(rel.b, {"symbol": rel.b, "degree": 0})
            nodes[rel.a]["degree"] += 1
            nodes[rel.b]["degree"] += 1
            m = metrics_by_pair.get(rel.pair_key)
            edges.append(
                {
                    "pair_key": rel.pair_key,
                    "a": rel.a,
                    "b": rel.b,
                    "type": rel.relation_type,
                    "type_cn": RELATION_TYPE_CN.get(rel.relation_type, rel.relation_type),
                    "strength": rel.strength,
                    "corr": m.corr if m else None,
                    "corr_source": m.corr_source if m else "static_only",
                    "lead_symbol": (m.lead_symbol if m else None) or rel.lead,
                    "lag_days": m.lag_days if m else None,
                    "sample_size": m.sample_size if m else 0,
                    "window": m.window if m else None,
                    "updated_at": m.updated_at if m else None,
                    "note": rel.note,
                }
            )
        return {"nodes": sorted(nodes.values(), key=lambda x: x["symbol"]), "edges": edges}

    # ─── 关联段（注入 prompt） ───

    def build_segment(self, symbol: str, analysis_date: str) -> str:
        """拼装关联品种段（≤ TOKEN_BUDGET['relation'] 字符，中文 1 字 ≈ 1~1.5 token）。"""
        try:
            items = self.get_related(symbol, analysis_date)
        except Exception as e:
            logger.warning(f"[relation] 关联段构建失败 {symbol}: {e}")
            return ""
        lines: List[str] = []
        for it in items:
            lines.append(self._format_related(it, symbol))
        return _trim_lines(lines, TOKEN_BUDGET["relation"])

    @staticmethod
    def _format_related(it: RelatedInfo, anchor: str) -> str:
        parts = [f"{it.symbol}（{it.relation_type_cn}"]
        if it.corr is not None:
            parts.append(f"corr={it.corr:.2f}，{it.window or 60} 日")
        else:
            parts.append(f"静态关系 strength={it.strength:.2f}，实测样本不足")
        if it.lead_text:
            parts.append(it.lead_text)
        head = "，".join(parts) + "）"
        tail = ""
        if it.recent_return is not None:
            tail += f" 近5日 {it.recent_return:+.2%}"
        if it.last_episode:
            ep = it.last_episode
            tail += (
                f"；最近结论 {str(ep.get('analysis_date'))[5:]} "
                f"{ep.get('direction_view') or ep.get('direction')}"
                f"（conf={float(ep.get('confidence') or 0):.2f}）"
            )
        stale = "（结论陈旧）" if it.stale else ""
        return f"- {head}{tail}{stale}"

    # ─── 辅助 ───

    @staticmethod
    def _lead_text(info: RelatedInfo, anchor: str, rel: StaticRelation) -> str:
        """按锚点翻译领先滞后：【二评】同一条关系两端展示方向相反。"""
        lead = info.lead_symbol
        lag = info.lag_days
        # 无实测 lag 时不写方向：静态 lead 只是人工标注，写"同步"会误导 agent
        if not lead or lag is None or lag == 0:
            return ""
        if lead.upper() == anchor.upper():
            return f"{info.symbol} 滞后 {anchor} 约 {abs(lag)} 日"
        return f"{info.symbol} 领先 {anchor} 约 {abs(lag)} 日"

    def _last_episode(self, symbol: str, before_date: str) -> Optional[Dict[str, Any]]:
        try:
            eps = self.svc.get_recent_episodes(symbol, limit=1, before_date=before_date)
        except Exception as e:
            logger.debug(f"[relation] 读 {symbol} episode 失败 {e}")
            return None
        if not eps:
            return None
        ep = eps[0]
        return {
            "id": ep.id,
            "analysis_date": ep.analysis_date,
            "direction": ep.direction,
            "direction_view": ep.direction_view,
            "confidence": ep.confidence,
            "status": ep.status,
        }

    def _trading_days_since(self, past: Optional[str], today: str) -> float:
        """对端最近 episode 距今交易日数；无任何 episode 时按窗口边缘（STM_WINDOW）计。"""
        if not past:
            return float(STM_WINDOW)
        try:
            n = self.calendar.trading_days_between(str(past)[:10], str(today)[:10])
        except Exception:
            n = 0
        if n <= 0:
            return 0.0
        return float(n)

    def _recent_return(self, symbol: str, as_of: str) -> Optional[float]:
        """近 5 个交易日涨跌幅（只用 ≤ as_of 的数据，避免未来函数）。"""
        closes = self.load_closes(symbol)
        if not closes:
            return None
        days = [d for d in sorted(closes) if d <= as_of]
        if len(days) < 6:
            return None
        first = closes[days[-6]]
        last = closes[days[-1]]
        if first <= 0:
            return None
        return last / first - 1.0


# ─────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────

def _norm_date(value: str) -> str:
    """YYYYMMDD / YYYY-MM-DD / ISO datetime → YYYY-MM-DD。"""
    s = str(value or "").strip().replace("/", "-")
    if not s:
        return ""
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def _trim_lines(lines: Sequence[str], budget: int) -> str:
    used = 0
    out: List[str] = []
    for line in lines:
        cost = len(line) + 1
        if used + cost > budget:
            break
        out.append(line)
        used += cost
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────
# 单例
# ─────────────────────────────────────────────────────────────

relation_service = RelationService()
