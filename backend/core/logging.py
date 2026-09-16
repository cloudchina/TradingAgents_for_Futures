#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一日志配置（loguru）

全项目日志规范：
- 统一使用 loguru，入口只需 `from loguru import logger`，禁止 print 输出运行日志；
- 日志名 loguru 自动取自调用处模块（{name}:{line}），无需各模块自行创建 logger；
- 级别约定：
    DEBUG   逐品种/逐合约等细粒度明细（量大，仅落文件）
    INFO    阶段进度与结果汇总（更新开始/结束、取数成功、统计）
    WARNING 可容忍的异常与降级（重试、无新数据、跳过、格式错误回退）
    ERROR   明确失败（接口全部重试失败、保存失败、读取失败）
    EXCEPTION 带堆栈的异常（用 logger.exception / logger.opt(exception=True)）
- FastAPI 服务在 main.py 启动时调用 setup_logging()；命令行脚本（如 run_scheduled_task.py
  / 各 updater 的 __main__）也需显式调用一次，保证控制台与文件格式一致。
"""
import sys
from pathlib import Path
from typing import Optional

from loguru import logger

_CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)
_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} - {message}"
)

_configured = False


def _ensure_utf8_streams() -> None:
    """Windows 下 stdout/stderr 若为 GBK（管道/重定向），emoji 等字符会抛 UnicodeEncodeError"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def setup_logging(
    logs_dir: Optional[str] = None,
    console_level: str = "INFO",
    file_level: str = "DEBUG",
    console: bool = True,
) -> None:
    """初始化全局日志（幂等，重复调用无副作用）

    Args:
        logs_dir: 日志文件目录；为空或不传则不写文件
        console_level: 控制台输出级别
        file_level: 文件输出级别
        console: 是否输出到 stderr
    """
    global _configured
    if _configured:
        return

    _ensure_utf8_streams()
    logger.remove()
    if console:
        logger.add(sys.stderr, level=console_level, format=_CONSOLE_FORMAT)

    if logs_dir:
        try:
            log_dir = Path(logs_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            logger.add(
                str(log_dir / "backend.log"),
                rotation="10 MB",
                retention="7 days",
                level=file_level,
                format=_FILE_FORMAT,
                encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"日志文件初始化失败，仅输出到控制台: {e}")

    _configured = True
