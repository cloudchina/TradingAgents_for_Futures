"""交易日历（阶段 4，见 memory-system-plan.md 6.2 / 二.1）。

horizon 与 recency 都是**交易日**语义，不能用自然日近似：
- 周五的 5 日窗口跨 2 个周末，实际要 9 个自然日；
- 国庆/春节会跨 10+ 自然日，用自然日算会系统性错配。

数据源：akshare `tool_trade_date_hist_sina`（上交所历史交易日，含未来一年的已公布日历）。
本地缓存：<MEMORY_DIR>/trade_calendar.json，缓存已覆盖到未来则不再联网（一年一次足够）。
联网失败时：有缓存用缓存，无缓存退化到"跳过周末的自然日近似"并标记 degraded。
"""
from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from loguru import logger

from core.settings import get_memory_path

CALENDAR_FILE = "trade_calendar.json"


def _default_fetcher() -> List[str]:
    """默认数据源：akshare 上交所历史交易日。"""
    import akshare as ak  # 延迟导入，避免无网络环境下的启动开销

    df = ak.tool_trade_date_hist_sina()
    days = sorted({str(d)[:10] for d in df["trade_date"].tolist()})
    return days


class TradeCalendar:
    """交易日查询：trading_days_after / is_trading_day / trading_days_between。"""

    def __init__(
        self,
        cache_path: Optional[Path] = None,
        fetcher: Optional[Callable[[], List[str]]] = None,
    ):
        self.cache_path = Path(cache_path) if cache_path else get_memory_path(CALENDAR_FILE)
        self._fetcher = fetcher or _default_fetcher
        self._days: List[str] = []
        self._loaded = False
        # True = 联网失败且无缓存，只能用"跳过周末"近似（口径退步，需告警）
        self.degraded = False

    # ─── 加载 ───

    def days(self, refresh_if_stale: bool = True) -> List[str]:
        if not self._loaded:
            self._load_cache()
            self._loaded = True
        if refresh_if_stale and self._stale():
            self._refresh()
        return self._days

    def _stale(self) -> bool:
        """缓存未覆盖到今天 → 需要重新拉一次。"""
        if not self._days:
            return True
        today = date.today().isoformat()
        return self._days[-1] < today

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            days = sorted(set(data.get("days", [])))
            if days:
                self._days = days
                logger.debug(f"trade_calendar: 读本地缓存 {len(days)} 个交易日")
        except Exception as e:
            logger.warning(f"trade_calendar: 缓存读取失败 {e}")

    def _refresh(self) -> None:
        try:
            days = sorted(set(self._fetcher()))
        except Exception as e:
            logger.warning(f"trade_calendar: 联网拉取失败，沿用旧缓存 {e}")
            if not self._days:
                self.degraded = True
            return
        if not days:
            return
        self._days = days
        self.degraded = False
        self._save(days)

    def _save(self, days: List[str]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(
                {"updated": datetime.now().isoformat(timespec="seconds"), "days": days},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    # ─── 查询 ───

    def trading_days_after(self, day: str, n: int) -> Optional[str]:
        """day 之后的第 n 个交易日（day 本身不计）。

        例：day=周五，n=5 → 下周五；跨周末与节假日自动跳过。
        """
        if n <= 0:
            return day
        days = self.days()
        if not days:
            return self._approx_after(day, n)
        idx = bisect_right(days, day)
        target = idx + n - 1
        if target >= len(days):
            return None
        return days[target]

    def trading_days_between(self, start: str, end: str) -> int:
        """[start, end) 区间内的交易日数。"""
        days = self.days()
        if not days:
            return self._approx_between(start, end)
        return max(0, bisect_left(days, end) - bisect_left(days, start))

    def is_trading_day(self, day: str) -> bool:
        days = self.days()
        if not days:
            return date.fromisoformat(day).weekday() < 5 if _valid(day) else False
        i = bisect_left(days, day)
        return i < len(days) and days[i] == day

    # ─── 退化近似（无缓存且联网失败） ───

    @staticmethod
    def _approx_after(day: str, n: int) -> Optional[str]:
        if not _valid(day):
            return None
        cur = date.fromisoformat(day)
        count = 0
        while count < n:
            cur += timedelta(days=1)
            if cur.weekday() < 5:  # 只跳周末，节假日无法识别
                count += 1
            if cur.year > date.today().year + 2:  # 死循环保护
                return None
        return cur.isoformat()

    @staticmethod
    def _approx_between(start: str, end: str) -> int:
        if not (_valid(start) and _valid(end)):
            return 0
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
        if d1 <= d0:
            return 0
        return int((d1 - d0).days * 5.0 / 7.0)


def _valid(day: str) -> bool:
    try:
        date.fromisoformat(str(day)[:10])
        return True
    except Exception:
        return False


# 全局单例
trade_calendar = TradeCalendar()
