"""复盘回填服务（阶段 4，见 memory-system-plan.md 6.2 / 四.阶段4 / 10）。

三件事：
1. outcome 回填：pending episode 到期后按**后复权价格序列**算 hit / realized_return / MAE
2. 不确定处理：无 CSV 或取不到价格 → status=unverifiable（reason=no_csv|no_price），
   **不进校准分母**（【三评 A】）
3. 语义记忆巩固：draft → pending → active 的门槛流转（LLM 二次校验可插拔）

两个必须守住的口径：
- **交易日**：horizon 用交易日历推进，不用自然日（周五的 5 日窗口跨 2 个周末）
- **后复权**：主力换月日的价格缺口必须修掉，否则"换月当天凭空涨跌 3%"。
  实现上只算区间内的换月因子（区间之后的因子在收益率里会分子分母抵消），
  因此可以按需联网、不需要为全历史序列拉逐合约数据。
"""
from __future__ import annotations

import json
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
from loguru import logger

from core.settings import get_memory_path, settings
from models.memory import Episode, EpisodeStatus
from services.memory_service import (
    SEMANTIC_DOWNGRADE_MIN_INVOKED,
    SEMANTIC_MIN_CONFIDENCE,
    SEMANTIC_MIN_EVIDENCE,
    memory_service,
)
from services.trade_calendar import TradeCalendar, trade_calendar

# 中性判据：区间涨跌不超过 ±0.5% 视为"中性说对了"（与 evaluate.py tolerance 一致）
NEUTRAL_TOLERANCE = 0.005

# 换月因子缓存目录：<MEMORY_DIR>/adj_factors/<SYM>.json
ADJ_CACHE_DIR = "adj_factors"


# ─────────────────────────────────────────────────────────────
# 价格序列
# ─────────────────────────────────────────────────────────────

@dataclass
class PriceSeries:
    """某品种的主连价格序列（未复权收盘 + 换月点）。

    adj 因子按需计算（懒加载），因为区间外的因子在收益率里会抵消掉。
    """
    symbol: str
    dates: List[str]
    close: List[float]
    contracts: List[str] = field(default_factory=list)
    rolls: List[Tuple[int, str]] = field(default_factory=list)  # (换月日下标, 旧合约)
    has_contract_map: bool = False

    def index_of(self, day: str) -> Optional[int]:
        i = bisect_left(self.dates, day)
        if i < len(self.dates) and self.dates[i] == day:
            return i
        return None

    def contracts_in(self, lo: int, hi: int) -> List[str]:
        return [c for c in self.contracts[lo:hi + 1] if c]


# ─────────────────────────────────────────────────────────────
# 换月复权因子
# ─────────────────────────────────────────────────────────────

def _default_contract_fetcher(contract: str, day: str) -> Optional[float]:
    """取某具体合约某交易日的收盘价（新浪日线）。

    back-adjust 需要"换月日当天新旧两个合约的收盘价"，主连序列只给一个新合约价，
    旧合约价只能额外取。失败返回 None → 调用方按未复权处理并标记 degraded。
    """
    import akshare as ak  # 延迟导入

    df = ak.futures_zh_daily_sina(symbol=contract)
    if df is None or df.empty:
        return None
    date_col = "date" if "date" in df.columns else df.columns[0]
    close_col = "close" if "close" in df.columns else "收盘"
    df = df[[date_col, close_col]].copy()
    df[date_col] = df[date_col].astype(str).str[:10]
    hit = df[df[date_col] == str(day)[:10]]
    if hit.empty:
        return None
    value = pd.to_numeric(hit.iloc[0][close_col], errors="coerce")
    return None if pd.isna(value) else float(value)


class RollAdjuster:
    """换月因子：按需联网 + 本地 JSON 缓存（同一点只拉一次，失败记 failed 下次再试）。"""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        fetcher: Optional[Callable[[str, str], Optional[float]]] = None,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else get_memory_path(ADJ_CACHE_DIR)
        self.fetcher = fetcher or _default_contract_fetcher
        self._cache: Dict[str, Dict[str, Any]] = {}

    def _load(self, symbol: str) -> Dict[str, Any]:
        if symbol in self._cache:
            return self._cache[symbol]
        path = self.cache_dir / f"{symbol.upper()}.json"
        data: Dict[str, Any] = {"factors": {}, "failed": []}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                data["factors"] = {k: float(v) for k, v in loaded.get("factors", {}).items()}
                data["failed"] = list(loaded.get("failed", []))
            except Exception as e:
                logger.warning(f"[roll_adjuster] {symbol} 缓存读取失败 {e}")
        self._cache[symbol] = data
        return data

    def _save(self, symbol: str, data: Dict[str, Any]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{symbol.upper()}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def factor_for(
        self, symbol: str, roll_date: str, old_contract: str, new_close: float
    ) -> Optional[float]:
        """换月日因子 = 新合约收盘 / 旧合约同日收盘。

        历史价格（换月日之前）乘上它之后，序列在换月处连续，
        收益 = 旧合约持有到换月日的真实收益 + 之后新合约的收益。
        """
        data = self._load(symbol)
        if roll_date in data["factors"]:
            return data["factors"][roll_date]
        if roll_date in data["failed"]:
            return None
        if not old_contract or not new_close:
            return None
        try:
            old_close = self.fetcher(old_contract, roll_date)
        except Exception as e:
            logger.warning(f"[roll_adjuster] {symbol} {roll_date} 取旧合约价失败: {str(e)[:80]}")
            old_close = None
        if not old_close:
            data["failed"].append(roll_date)
            self._save(symbol, data)
            return None
        factor = round(new_close / old_close, 8)
        data["factors"][roll_date] = factor
        self._save(symbol, data)
        return factor


# ─────────────────────────────────────────────────────────────
# OutcomeService
# ─────────────────────────────────────────────────────────────

class OutcomeService:
    """复盘回填 + 统计 + 语义巩固。"""

    def __init__(
        self,
        svc: Any = None,
        data_root: Optional[Path] = None,
        calendar: Optional[TradeCalendar] = None,
        adjuster: Optional[RollAdjuster] = None,
    ):
        self.svc = svc or memory_service
        self.root = Path(data_root) if data_root else Path(settings.DATA_ROOT_DIR)
        self.calendar = calendar or trade_calendar
        self.adjuster = adjuster or RollAdjuster()
        self._series_cache: Dict[str, Optional[PriceSeries]] = {}

    # ─── 数据读取 ───

    def ohlc_path(self, symbol: str) -> Path:
        return self.root / "technical_analysis" / symbol.upper() / "ohlc_data.csv"

    def contract_path(self, symbol: str) -> Path:
        return self.root / "main_contract" / symbol.upper() / "dominant_contract.csv"

    def load_price_series(self, symbol: str) -> Optional[PriceSeries]:
        """读主连 OHLC + 主力合约映射，构造带换月标记的价格序列。"""
        sym = symbol.upper()
        if sym in self._series_cache:
            return self._series_cache[sym]

        path = self.ohlc_path(sym)
        if not path.exists():
            self._series_cache[sym] = None
            return None
        try:
            df = pd.read_csv(path, encoding="utf-8-sig")
        except Exception as e:
            logger.warning(f"[outcome] {sym} 读 OHLC 失败 {e}")
            self._series_cache[sym] = None
            return None

        date_col = next((c for c in ("时间", "日期", "date") if c in df.columns), None)
        close_col = next((c for c in ("收盘", "close") if c in df.columns), None)
        if not date_col or not close_col:
            self._series_cache[sym] = None
            return None

        df = df[[date_col, close_col]].copy()
        df[date_col] = df[date_col].astype(str).map(_norm_date)
        df[close_col] = pd.to_numeric(df[close_col], errors="coerce")
        df = df.dropna().drop_duplicates(subset=[date_col], keep="last").sort_values(date_col)
        if df.empty:
            self._series_cache[sym] = None
            return None

        dates = df[date_col].tolist()
        closes = [float(x) for x in df[close_col].tolist()]
        contracts = self._load_contracts(sym, dates)

        rolls: List[Tuple[int, str]] = []
        for i in range(1, len(dates)):
            prev, cur = contracts[i - 1], contracts[i]
            if prev and cur and prev != cur:
                rolls.append((i, prev))

        series = PriceSeries(
            symbol=sym,
            dates=dates,
            close=closes,
            contracts=contracts,
            rolls=rolls,
            has_contract_map=any(contracts),
        )
        self._series_cache[sym] = series
        return series

    def _load_contracts(self, symbol: str, dates: List[str]) -> List[str]:
        """读主力合约映射；缺失返回等长空串列表（不影响回填，只是 rolled 判定失效）。"""
        path = self.contract_path(symbol)
        empty = [""] * len(dates)
        if not path.exists():
            return empty
        try:
            df = pd.read_csv(path, encoding="utf-8-sig")
            if "dominant_contract" not in df.columns or "date" not in df.columns:
                return empty
            mapping: Dict[str, str] = {}
            for _, row in df.iterrows():
                key = _norm_date(row["date"])
                val = str(row["dominant_contract"]).strip()
                if key and val and val.lower() != "nan":
                    mapping[key] = val
        except Exception as e:
            logger.warning(f"[outcome] {symbol} 读主力合约失败 {e}")
            return empty
        return [mapping.get(d, "") for d in dates]

    # ─── 区间收益 ───

    def window_returns(
        self, series: PriceSeries, lo: int, hi: int
    ) -> Tuple[List[float], bool, bool]:
        """区间内逐 bar 相对收益序列（已修掉换月缺口）。

        因子语义：factor = 换月日新合约收盘 / 旧合约同日收盘（>1 表示新合约更贵）。
        这里 base 是区间起点的旧合约价格，因此要把换月后（新合约尺度）的价格
        **除以** factor 折算回起点尺度——方向写反会让"换月当天凭空涨 3%"。

        Returns: (returns, rolled, adjust_degraded)
        - returns[k] 对应 series.dates[lo + k] 相对 dates[lo] 的收益
        - rolled：区间内发生过换月
        - adjust_degraded：有换月点但拿不到旧合约价，本次只能按未复权算
        """
        roll_map = {idx: old for idx, old in series.rolls}
        scale = 1.0  # 新合约尺度 → 起点尺度的折算系数 = 1 / Π(区间内换月因子)
        degraded = False
        rolled = False
        base = series.close[lo]
        out: List[float] = []
        for i in range(lo, hi + 1):
            if i > lo and i in roll_map:
                rolled = True
                factor = self.adjuster.factor_for(
                    series.symbol, series.dates[i], roll_map[i], series.close[i]
                )
                if factor is None:
                    degraded = True
                else:
                    scale /= factor
            out.append((series.close[i] * scale) / base - 1.0)
        return out, rolled, degraded

    # ─── 回填 ───

    def backfill(
        self,
        symbols: Optional[List[str]] = None,
        as_of: Optional[str] = None,
        limit: int = 1000,
        retry_unverifiable: bool = True,
    ) -> Dict[str, Any]:
        """回填所有到期 episode。

        Args:
            symbols: 限定品种；None = 全部
            as_of: 回填基准日（默认今天）；历史补跑可传具体日期
            limit: 单次最多处理条数
            retry_unverifiable: 重扫历史 unverifiable（默认 True）。

                unverifiable 不是终态：当时只是"数据还没到"。数据补齐后若不重扫，
                这批样本会永久停在 unverifiable，命中率分母被系统性低估。
        """
        today = as_of or date.today().isoformat()
        pending = self.svc.list_pending_episodes(symbols, limit=limit)
        if retry_unverifiable:
            pending = pending + self.svc.list_episodes_by_status(
                EpisodeStatus.UNVERIFIABLE.value, symbols
            )
        stats: Dict[str, Any] = {
            "as_of": today,
            "scanned": len(pending),
            "resolved": 0,
            "recovered": 0,   # 从 unverifiable 恢复（数据补齐后重扫命中）
            "unverifiable": 0,
            "not_due": 0,
            "skipped_no_calendar": 0,
            "by_symbol": {},
        }

        for ep in pending:
            symbol = (ep.symbol or "").upper()
            bucket = stats["by_symbol"].setdefault(
                symbol, {"resolved": 0, "unverifiable": 0, "not_due": 0}
            )

            target = self.calendar.trading_days_after(ep.analysis_date, ep.horizon or 10)
            if target is None:
                stats["skipped_no_calendar"] += 1
                continue
            if today < target:
                stats["not_due"] += 1
                bucket["not_due"] += 1
                continue

            outcome = self._resolve_one(ep, target)
            if outcome is None or "unverifiable_reason" in outcome:
                # 取不到价格：明确标 unverifiable，绝不猜（【三评 A】）
                # no_csv（无数据文件）与 no_price（有文件但缺对应交易日）分开记，便于定位数据缺口
                reason = (outcome or {}).get("unverifiable_reason", "no_csv")
                self.svc.update_outcome(
                    ep.id,
                    {"unverifiable_reason": reason},
                    EpisodeStatus.UNVERIFIABLE.value,
                )
                stats["unverifiable"] += 1
                bucket["unverifiable"] += 1
                continue

            if ep.status == EpisodeStatus.UNVERIFIABLE.value:
                stats["recovered"] += 1
            self.svc.update_outcome(ep.id, outcome, EpisodeStatus.RESOLVED.value)
            stats["resolved"] += 1
            bucket["resolved"] += 1

        logger.info(
            f"[outcome] 回填完成 scanned={stats['scanned']} "
            f"resolved={stats['resolved']} unverifiable={stats['unverifiable']} "
            f"not_due={stats['not_due']}"
        )
        return stats

    def _resolve_one(self, ep: Episode, target: str) -> Optional[Dict[str, Any]]:
        """算单条 episode 的 outcome；取不到价格返回 None。"""
        series = self.load_price_series(ep.symbol)
        if series is None:
            return None
        lo = series.index_of(ep.analysis_date)
        hi = series.index_of(target)
        if lo is None or hi is None or hi <= lo:
            # 有 CSV 但没有对应交易日的价格 → 与 no_csv 区分开，便于定位数据缺口
            return {
                "unverifiable_reason": "no_price",
                "target_date": target,
            }

        returns, rolled, degraded = self.window_returns(series, lo, hi)
        direction = (ep.direction or "").lower()
        sign = 1.0 if direction == "long" else (-1.0 if direction == "short" else 0.0)

        raw_ret = returns[-1]
        realized = sign * raw_ret
        if sign == 0.0:
            # 中性：判据是"没怎么动"，沿用 evaluate.py 的 ±0.5% 容差
            hit = abs(raw_ret) <= NEUTRAL_TOLERANCE
        else:
            hit = realized > 0

        # MAE：区间内最大不利偏移（做多取最深回撤，做空取最大反弹，中性取最大绝对值）
        if sign > 0:
            mae = max(0.0, -min(returns))
        elif sign < 0:
            mae = max(0.0, max(returns))
        else:
            mae = max(abs(min(returns)), abs(max(returns)))

        contracts = series.contracts_in(lo, hi)
        return {
            "hit": bool(hit),
            "realized_return": round(realized, 6),
            "MAE": round(mae, 6),
            "holding_days": hi - lo,
            "rolled": bool(rolled),
            "actual_contract": series.contracts[lo] or None,
            "target_date": target,
            "direction": direction,
            "adjust_degraded": bool(degraded),
            # 主力合约映射缺失 → 换月检测与复权都失效，收益仍含拼接缺口。
            # 显式标记而不是静默处理，统计里能直接看出这批样本的可信度边界。
            "contract_map_missing": not series.has_contract_map,
        }

    # ─── 统计 ───

    def compute_stats(
        self, symbol: Optional[str] = None, persist: bool = True
    ) -> Dict[str, Any]:
        """命中率 / 校准曲线 / 错误模式（只统计 resolved，parse_failed 与 unverifiable 不入分母）。"""
        symbols = [symbol.upper()] if symbol else None
        episodes = self.svc.list_episodes_by_status(
            EpisodeStatus.RESOLVED.value, symbols
        )
        unverifiable = len(
            self.svc.list_episodes_by_status(EpisodeStatus.UNVERIFIABLE.value, symbols)
        )

        n = len(episodes)
        hits = sum(1 for e in episodes if isinstance(e.outcome, dict) and e.outcome.get("hit"))
        result: Dict[str, Any] = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "symbol": (symbol or "ALL").upper(),
            "n_resolved": n,
            "n_unverifiable": unverifiable,
            "hit_rate": round(hits / n, 4) if n else 0.0,
            "by_direction": {},
            "by_confidence": [],
            "rolled": {"n": 0, "hit_rate": 0.0},
            "avg_return": 0.0,
            "avg_mae": 0.0,
            "error_patterns": {},
        }
        if n == 0:
            return result

        # 按方向
        for direction in ("long", "short", "neutral"):
            sub = [e for e in episodes if (e.direction or "").lower() == direction]
            if not sub:
                continue
            sub_hits = sum(1 for e in sub if isinstance(e.outcome, dict) and e.outcome.get("hit"))
            result["by_direction"][direction] = {
                "n": len(sub),
                "hit_rate": round(sub_hits / len(sub), 4),
            }

        # 校准曲线：置信度分 5 档 vs 实际命中率
        bins = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
        for lo_bin, hi_bin in bins:
            sub = [
                e for e in episodes
                if (e.confidence or 0.0) >= lo_bin and (e.confidence or 0.0) < hi_bin
            ]
            if not sub:
                continue
            sub_hits = sum(1 for e in sub if isinstance(e.outcome, dict) and e.outcome.get("hit"))
            result["by_confidence"].append({
                "bin": f"{lo_bin:.1f}-{min(hi_bin, 1.0):.1f}",
                "n": len(sub),
                "hit_rate": round(sub_hits / len(sub), 4),
            })

        # 换月样本
        rolled_eps = [
            e for e in episodes
            if isinstance(e.outcome, dict) and e.outcome.get("rolled")
        ]
        if rolled_eps:
            rolled_hits = sum(
                1 for e in rolled_eps if isinstance(e.outcome, dict) and e.outcome.get("hit")
            )
            result["rolled"] = {
                "n": len(rolled_eps),
                "hit_rate": round(rolled_hits / len(rolled_eps), 4),
            }

        returns = [
            float(e.outcome["realized_return"])
            for e in episodes
            if isinstance(e.outcome, dict) and isinstance(e.outcome.get("realized_return"), (int, float))
        ]
        maes = [
            float(e.outcome["MAE"])
            for e in episodes
            if isinstance(e.outcome, dict) and isinstance(e.outcome.get("MAE"), (int, float))
        ]
        result["avg_return"] = round(sum(returns) / len(returns), 6) if returns else 0.0
        result["avg_mae"] = round(sum(maes) / len(maes), 6) if maes else 0.0

        # 错误模式：高置信度说错最该被复盘
        high_wrong = sum(
            1 for e in episodes
            if (e.confidence or 0.0) >= 0.7
            and isinstance(e.outcome, dict) and not e.outcome.get("hit")
        )
        result["error_patterns"] = {
            "high_confidence_wrong": high_wrong,
            "rolled_samples": len(rolled_eps),
            "adjust_degraded": sum(
                1 for e in episodes
                if isinstance(e.outcome, dict) and e.outcome.get("adjust_degraded")
            ),
            # 无主力合约映射 → 这批样本未做换月复权，收益含主连拼接缺口
            "contract_map_missing": sum(
                1 for e in episodes
                if isinstance(e.outcome, dict) and e.outcome.get("contract_map_missing")
            ),
        }

        if persist:
            self._persist_stats(result)
        return result

    def _persist_stats(self, result: Dict[str, Any]) -> None:
        path = get_memory_path("stats.json")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[outcome] stats 落盘失败 {e}")

    # ─── 语义记忆巩固 ───

    def consolidate_semantics(
        self,
        symbol: Optional[str] = None,
        llm_verify: Optional[Callable[[Any, int], bool]] = None,
    ) -> Dict[str, Any]:
        """draft → pending → active 的门槛流转。

        - evidence_count = 该品种已 resolved 的 episode 数（无 embedding 时的口径）
        - draft 且证据达标 → 二次校验通过后转 pending（校验可注入，测试可关）
        - pending 且被调用次数达标 + 置信度达标 → 自动转 active
        """
        resolved = self.svc.list_episodes_by_status(EpisodeStatus.RESOLVED.value)
        evidence = Counter((e.symbol or "").upper() for e in resolved)
        hit_of: Dict[str, float] = {}
        for sym, cnt in evidence.items():
            sub = [e for e in resolved if (e.symbol or "").upper() == sym]
            hit_of[sym] = sum(
                1 for e in sub if isinstance(e.outcome, dict) and e.outcome.get("hit")
            ) / cnt

        result = {"scanned": 0, "promoted": 0, "activated": 0, "skipped": 0}
        semantics = self.svc.list_semantics(symbol=(symbol.upper() if symbol else None))
        for sem in semantics:
            if sem.status not in ("draft", "pending"):
                continue
            result["scanned"] += 1
            n_evidence = evidence.get((sem.symbol or "").upper(), 0)
            confidence = round(hit_of.get((sem.symbol or "").upper(), 0.0), 4)
            self.svc.update_semantic_status(
                sem.id, sem.status, evidence_count=n_evidence, confidence=confidence
            )

            if n_evidence < SEMANTIC_MIN_EVIDENCE:
                result["skipped"] += 1
                continue

            if sem.status == "draft":
                ok = True if llm_verify is None else bool(llm_verify(sem, n_evidence))
                if not ok:
                    result["skipped"] += 1
                    continue
                self.svc.update_semantic_status(sem.id, "pending", confidence=confidence)
                result["promoted"] += 1
            elif (
                sem.invoked_count >= SEMANTIC_DOWNGRADE_MIN_INVOKED
                and confidence >= SEMANTIC_MIN_CONFIDENCE
            ):
                self.svc.update_semantic_status(sem.id, "active", confidence=confidence)
                result["activated"] += 1

        logger.info(f"[outcome] 语义巩固 {result}")
        return result

    @staticmethod
    def default_llm_verify(sem: Any, n_evidence: int) -> bool:
        """默认二次校验：LLM(temp=0) 判断该 claim 是否可由证据支持。

        校验失败/未配置 LLM → 返回 False（保守：保持 draft，不自动激活）。
        """
        prompt = (
            "你是期货研究的质量审核员。以下是一条由 AI 分析自动沉淀的规律，"
            f"该品种当前有 {n_evidence} 条已完成复盘的历史结论作为证据。\n"
            f"品种: {sem.symbol}\n规律: {sem.claim}\n\n"
            "请判断这条规律是否表述清晰、可证伪、且属于可被历史数据支持的经验规律。\n"
            "只回答两个字：通过 / 不通过。"
        )
        try:
            from agents.llm_client import llm_client

            msg = llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=16,
            )
            text = str(getattr(msg, "content", "") or "")
        except Exception as e:
            logger.warning(f"[outcome] LLM 二次校验不可用，保持 draft: {str(e)[:80]}")
            return False
        return "通过" in text and "不通过" not in text


def _norm_date(value: Any) -> str:
    """YYYYMMDD / YYYY/MM/DD / ISO → YYYY-MM-DD。"""
    s = str(value).strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


# 全局单例
outcome_service = OutcomeService()
