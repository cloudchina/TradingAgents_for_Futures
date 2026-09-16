#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新进度上报基类（任务式数据更新用）

设计：
- 各数据更新器（Updater）继承本类，获得统一的 _report_progress 上报入口；
- 由 DataManagerService.run_data_update(..., progress_cb=...) 在实例上注入 _progress_cb；
- 未注入回调（如命令行直跑、定时任务）时全部方法为空操作，行为与原先完全一致。
"""
from typing import Dict, Optional, Callable

from loguru import logger


class ProgressReporter:
    """进度上报基类：把当前阶段/单元进度通知给外部回调（线程安全由调用方保证）"""

    _progress_cb: Optional[Callable] = None

    def _report_progress(self, stage: str, done: int, total: int, current: str = ""):
        """
        上报当前阶段的处理进度。

        Args:
            stage: 阶段名，如“处理品种”“拉取交易所行情”
            done:   当前阶段已完成的单元数（1 起）
            total:  当前阶段总单元数
            current: 正在处理的对象（品种/日期/交易所名，用于展示）
        """
        cb = getattr(self, "_progress_cb", None)
        if callable(cb):
            try:
                cb(stage=stage, done=int(done), total=int(total), current=str(current))
            except Exception:
                pass

    def log_update_summary(self, stats: Optional[Dict] = None) -> None:
        """统一格式的更新汇总日志（各数据更新器收尾统计复用）

        输出内容：成功/失败/跳过品种数、新增品种数（有该字段的模块）、
        新增记录数、耗时；失败品种明细以 ERROR 单列，避免 0 失败也报 ERROR。
        """
        stats = stats if stats is not None else getattr(self, "update_stats", {})

        def _count(key: str) -> int:
            # 跨交易日/多数据源会重复追加同一品种，这里按品种去重计数
            return len(set(stats.get(key, []) or []))

        updated = _count("updated_varieties")
        skipped = _count("skipped_varieties")
        failed_names = sorted(set(stats.get("failed_varieties", []) or []))
        total_new = stats.get("total_new_records", 0)

        parts = [f"成功 {updated} 个", f"失败 {len(failed_names)} 个", f"跳过 {skipped} 个"]
        if "new_varieties" in stats:
            parts.insert(1, f"新增品种 {_count('new_varieties')} 个")
        parts.append(f"新增记录 {total_new} 条")

        start, end = stats.get("start_time"), stats.get("end_time")
        if start and end:
            parts.append(f"耗时 {(end - start).total_seconds():.1f}s")

        if failed_names:
            logger.warning("更新完成统计: " + " / ".join(parts))
            logger.error(f"失败品种明细: {', '.join(failed_names)}")
        else:
            logger.info("更新完成统计: " + " / ".join(parts))

        for msg in (stats.get("error_messages", []) or [])[:10]:
            logger.error(f"错误明细: {msg}")
