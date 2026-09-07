"""数据管理 API 路由"""
from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, List, Optional
from loguru import logger

from services.data_service import data_manager_service
from services.commodity_service import commodity_service
from models.data import DataStatusResponse, UpdateResult

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
    """从 akshare 刷新主力合约映射"""
    contracts = commodity_service.refresh_dominant_contracts()
    return {
        "message": f"已刷新 {len(contracts)} 个主力合约",
        "contracts": contracts,
    }


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


@router.post("/update/{module_key}", response_model=UpdateResult)
async def update_data(
    module_key: str,
    target_date: Optional[str] = Query(None, description="目标日期 YYYY-MM-DD"),
    varieties: Optional[str] = Query(None, description="品种代码，逗号分隔，不传则更新全部"),
) -> UpdateResult:
    """更新指定模块数据"""
    if module_key not in VALID_MODULES:
        raise HTTPException(status_code=400, detail=f"无效模块: {module_key}")

    variety_list = None
    if varieties:
        variety_list = [v.strip().upper() for v in varieties.split(",") if v.strip()]

    return data_manager_service.run_data_update(
        module_key, target_date=target_date, varieties=variety_list
    )
