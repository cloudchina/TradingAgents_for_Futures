"""复盘回填调度器（阶段 4）。

ScheduledBackfillRunner：独立 daemon 线程 + Event，与 ScheduledAnalysisRunner 同构
（不用 schedule 库，避免多调度器互相抢占主线程；停止时用 Event 分段唤醒）。

默认每日 20:00 触发（settings.MEMORY_BACKFILL_TIME）。20:00 的理由：
- 20:00 之后当日日线已落盘（夜盘品种的当日收盘价已确定）
- 早于次日 09:00 开盘，回填结果可供次日分析注入使用

流程：backfill（回填到期 episode） → consolidate_semantics（语义巩固） → compute_stats（统计）
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Optional

from loguru import logger

from core.settings import settings
from services.memory_service import memory_service
from services.outcome_service import outcome_service


class ScheduledBackfillRunner:
    """每日定时触发复盘回填。"""

    def __init__(
        self,
        schedule_time: Optional[str] = None,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.schedule_time = schedule_time or settings.MEMORY_BACKFILL_TIME
        self.callback = callback
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._last_run_date: Optional[str] = None
        self.last_result: Optional[Dict[str, Any]] = None

    # ─── 生命周期 ───

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(f"复盘回填调度已启动: 每天 {self.schedule_time}")

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()

    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    # ─── 主循环 ───

    def _run(self) -> None:
        while not self._stop_event.is_set():
            now = datetime.now()
            try:
                hour, minute = (int(x) for x in str(self.schedule_time).split(":"))
            except Exception:
                logger.error(f"回填时间配置非法: {self.schedule_time}，按 20:00 处理")
                hour, minute = 20, 0

            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now >= target:
                target = (now + timedelta(days=1)).replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )

            wait_seconds = (target - now).total_seconds()
            logger.info(
                f"下次复盘回填: {target.strftime('%Y-%m-%d %H:%M')} "
                f"(等待 {wait_seconds / 3600:.1f} 小时)"
            )

            while wait_seconds > 0 and not self._stop_event.is_set():
                sleep_time = min(wait_seconds, 60)
                self._stop_event.wait(sleep_time)
                wait_seconds -= sleep_time

            if self._stop_event.is_set():
                break

            try:
                self._on_trigger()
            except Exception as e:
                logger.error(f"复盘回填执行失败: {e}")

    def _on_trigger(self) -> Dict[str, Any]:
        today = date.today().isoformat()
        if self._last_run_date == today:
            logger.info(f"复盘回填 {today} 已执行过，跳过重复触发")
            return {"skipped": "already_run_today", "date": today}
        result = self.run_once()
        self._last_run_date = today
        return result

    # ─── 一次完整回填 ───

    def run_once(
        self, symbols: Optional[list] = None, use_llm_verify: bool = False
    ) -> Dict[str, Any]:
        """立即执行一次回填全流程（供 API / CLI / 测试调用）。"""
        logger.info(f"复盘回填开始: symbols={symbols or 'ALL'}")
        result: Dict[str, Any] = {"started_at": datetime.now().isoformat(timespec="seconds")}
        # CLI 路径没有 lifespan，migrate 不会自动跑，这里补一次（幂等）
        try:
            memory_service.init_if_needed()
        except Exception as e:
            logger.error(f"记忆库初始化失败: {e}")
            result["backfill"] = {"error": f"memory db not ready: {e}"}
            return result
        try:
            result["backfill"] = outcome_service.backfill(symbols=symbols)
        except Exception as e:
            logger.error(f"回填失败: {e}")
            result["backfill"] = {"error": str(e)}
        try:
            # 无人值守的每日调度默认不做 LLM 二次校验（避免不可控的模型调用与费用），
            # 只更新证据数；需要真正 promote 时用 API /consolidate 或将该开关打开
            result["consolidate"] = outcome_service.consolidate_semantics(
                llm_verify=outcome_service.default_llm_verify if use_llm_verify else None
            )
        except Exception as e:
            logger.error(f"语义巩固失败: {e}")
            result["consolidate"] = {"error": str(e)}
        try:
            result["stats"] = outcome_service.compute_stats()
        except Exception as e:
            logger.error(f"统计失败: {e}")
            result["stats"] = {"error": str(e)}

        result["finished_at"] = datetime.now().isoformat(timespec="seconds")
        self.last_result = result
        if self.callback:
            try:
                self.callback(result)
            except Exception as e:
                logger.warning(f"回填回调失败: {e}")
        logger.info(
            f"复盘回填完成: resolved={result.get('backfill', {}).get('resolved')} "
            f"hit_rate={result.get('stats', {}).get('hit_rate')}"
        )
        return result


# 全局单例
backfill_runner = ScheduledBackfillRunner()
