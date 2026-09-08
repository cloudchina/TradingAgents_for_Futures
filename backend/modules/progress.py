#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新进度上报基类（任务式数据更新用）

设计：
- 各数据更新器（Updater）继承本类，获得统一的 _report_progress 上报入口；
- 由 DataManagerService.run_data_update(..., progress_cb=...) 在实例上注入 _progress_cb；
- 未注入回调（如命令行直跑、定时任务）时全部方法为空操作，行为与原先完全一致。
"""
from typing import Optional, Callable


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
