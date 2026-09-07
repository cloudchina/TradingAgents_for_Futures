"""Pydantic 数据模型 - 数据管理相关"""
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
from enum import Enum


class DataModuleKey(str, Enum):
    INVENTORY = "inventory"
    POSITIONING = "positioning"
    TERM_STRUCTURE = "term_structure"
    TECHNICAL_ANALYSIS = "technical_analysis"
    BASIS = "basis"
    RECEIPT = "receipt"


class ModuleStatus(BaseModel):
    """模块数据状态"""
    status: str = "unknown"
    commodities_count: int = 0
    total_records: int = 0
    last_update: str = "未知"
    error: str = ""
    path: str = ""


class DataStatusResponse(BaseModel):
    """数据状态响应"""
    modules: Dict[str, ModuleStatus] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)


class CommodityVarietyInfo(BaseModel):
    """品种数据详情"""
    variety: str
    start_date: str = "未知"
    end_date: str = "未知"
    record_count: int = 0


class UpdateResult(BaseModel):
    """数据更新结果"""
    status: str = "success"
    message: str = ""
    details: str = ""


class WordReportRequest(BaseModel):
    """Word报告生成请求"""
    include_charts: bool = False
    commodities: Optional[List[str]] = None


class SystemStatusResponse(BaseModel):
    """系统状态响应"""
    bailian_api_configured: bool = False
    serper_api_configured: bool = False
    docx_available: bool = False
    cache_count: int = 0
    scheduler_running: bool = False
    scheduler_config: Optional[Dict[str, Any]] = None
