"""定时分析 API 路由"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from loguru import logger

from models.analysis import ScheduledConfig
from services.scheduled_service import (
    ScheduledAnalysisRunner,
    get_scheduled_runner,
    set_scheduled_runner,
)

router = APIRouter(prefix="/api/scheduled", tags=["定时分析"])


@router.get("/config")
async def get_scheduled_config() -> Dict[str, Any]:
    """获取定时分析配置"""
    runner = get_scheduled_runner()
    if runner:
        return runner.config.model_dump()
    return ScheduledConfig().model_dump()


@router.post("/start")
async def start_scheduled(config: ScheduledConfig) -> Dict[str, Any]:
    """启动定时分析"""
    if not config.commodities:
        raise HTTPException(status_code=400, detail="请至少配置一个品种")

    # 停止旧的
    old_runner = get_scheduled_runner()
    if old_runner:
        old_runner.stop()

    # 创建新的
    runner = ScheduledAnalysisRunner(config)
    runner.start()
    set_scheduled_runner(runner)

    return {
        "message": f"定时分析已启动: 每天 {config.schedule_time}",
        "running": True,
        "config": config.model_dump(),
    }


@router.post("/stop")
async def stop_scheduled() -> Dict[str, Any]:
    """停止定时分析"""
    runner = get_scheduled_runner()
    if runner:
        runner.stop()
        set_scheduled_runner(None)
        return {"message": "定时分析已停止", "running": False}
    return {"message": "定时分析未运行", "running": False}


@router.get("/status")
async def get_scheduled_status() -> Dict[str, Any]:
    """获取定时分析状态"""
    runner = get_scheduled_runner()
    if runner and runner.is_running():
        return {
            "running": True,
            "schedule_time": runner.config.schedule_time,
            "commodities": runner.config.commodities,
        }
    return {"running": False}
