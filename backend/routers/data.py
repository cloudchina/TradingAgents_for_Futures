"""数据管理 API 路由"""
from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, List, Optional
from loguru import logger

from services.data_service import data_manager_service
from services.commodity_service import commodity_service
from services.data_task_service import data_task_service
from models.data import DataStatusResponse

router = APIRouter(prefix="/api/data", tags=["数据管理"])

VALID_MODULES = ["inventory", "positioning", "term_structure", "technical_analysis", "basis", "receipt"]


@router.get("/status", response_model=DataStatusResponse)
async def get_data_status() -> DataStatusResponse:
    """获取数据状态"""
    return data_manager_service.get_data_status()


@router.get("/commodities")
async def get_commodities() -> List[Dict[str, Any]]:
    """获取所有品种配置（含主力合约和数据状态）"""
    return data_manager_service.get_commodities()


@router.get("/dominant-contracts")
async def get_dominant_contracts() -> Dict[str, str]:
    """获取主力合约映射"""
    return commodity_service.get_all_dominant_contracts()


@router.post("/dominant-contracts/refresh")
async def refresh_dominant_contracts() -> Dict[str, Any]:
    """从 akshare 刷新主力合约映射（异步任务：提交后立即返回，由前端轮询进度）"""
    task, already = data_task_service.submit_refresh_contracts()
    message = (
        f"已有任务在运行（{task['module_name']}），已自动跟踪该任务"
        if already
        else f"刷新主力合约任务已提交：{task['module_name']}"
    )
    return {"task_id": task["task_id"], "already_running": already, "message": message, "task": task}


@router.get("/varieties/{module_key}")
async def get_module_varieties(module_key: str) -> List[Dict[str, Any]]:
    """获取模块下各品种数据详情"""
    if module_key not in VALID_MODULES:
        raise HTTPException(status_code=400, detail=f"无效模块: {module_key}")
    return data_manager_service.get_module_varieties(module_key)


@router.get("/commodity/{commodity}")
async def check_commodity_data(commodity: str) -> Dict[str, bool]:
    """检查品种数据情况"""
    return data_manager_service.check_commodity_data(commodity.upper())


@router.post("/update/{module_key}")
async def update_data(
    module_key: str,
    target_date: Optional[str] = Query(None, description="目标日期 YYYY-MM-DD"),
    varieties: Optional[str] = Query(None, description="品种代码，逗号分隔，不传则更新全部"),
) -> Dict[str, Any]:
    """更新指定模块数据（异步任务：提交后立即返回，由前端轮询进度）。

    说明：新安装无历史数据时一次更新可能持续很久，同步等待会触发前端超时
    （表现为“报错但后端其实成功”）。本接口改为后台任务 + 轮询模型，
    状态通过 GET /api/data/tasks/{task_id} 获取。
    """
    if module_key not in VALID_MODULES:
        raise HTTPException(status_code=400, detail=f"无效模块: {module_key}")

    variety_list = None
    if varieties:
        variety_list = [v.strip().upper() for v in varieties.split(",") if v.strip()]

    task, already = data_task_service.submit_update(
        module_key, target_date=target_date, varieties=variety_list
    )
    message = (
        f"已有任务在运行（{task['module_name']}），已自动跟踪该任务，无需重复提交"
        if already
        else f"更新任务已提交：{task['module_name']}"
    )
    return {"task_id": task["task_id"], "already_running": already, "message": message, "task": task}


@router.get("/tasks")
async def list_update_tasks(
    limit: int = Query(10, ge=1, le=50, description="返回最近任务条数"),
) -> Dict[str, Any]:
    """最近的数据更新/刷新任务列表（新任务在前）"""
    return {"tasks": data_task_service.list(limit=limit)}


@router.get("/tasks/{task_id}")
async def get_update_task(task_id: str) -> Dict[str, Any]:
    """查询单个数据更新任务的实时状态"""
    task = data_task_service.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return task
