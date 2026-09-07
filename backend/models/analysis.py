"""Pydantic 数据模型 - 分析相关"""
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum


class AnalysisStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CACHED = "cached"


class AnalysisMode(str, Enum):
    ANALYST_ONLY = "analyst_only"
    COMPLETE_FLOW = "complete_flow"


class AnalysisModule(str, Enum):
    INVENTORY = "inventory"
    POSITIONING = "positioning"
    TERM_STRUCTURE = "term_structure"
    TECHNICAL = "technical"
    BASIS = "basis"
    NEWS = "news"


class ModuleResult(BaseModel):
    """单个模块分析结果"""
    module_name: str
    commodity: str
    analysis_date: str
    status: AnalysisStatus = AnalysisStatus.NOT_STARTED
    result_data: Dict[str, Any] = Field(default_factory=dict)
    confidence_score: float = 0.0
    execution_time: float = 0.0
    error_message: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class AnalysisRequest(BaseModel):
    """分析请求"""
    commodities: List[str] = Field(..., description="品种代码列表", example=["AU", "RB"])
    analysis_date: str = Field(..., description="分析日期 YYYY-MM-DD")
    modules: List[AnalysisModule] = Field(
        default=[m for m in AnalysisModule],
        description="选择的分析模块"
    )
    analysis_mode: AnalysisMode = Field(
        default=AnalysisMode.COMPLETE_FLOW,
        description="分析模式"
    )
    ai_model: str = Field(default="qwen-plus", description="AI模型")
    use_realtime: bool = Field(default=True, description="使用实时数据")
    debate_rounds: int = Field(default=3, ge=0, le=5, description="辩论轮数")
    force_refresh: bool = Field(default=False, description="强制刷新缓存")


class AnalysisTask(BaseModel):
    """分析任务"""
    task_id: str
    commodities: List[str]
    analysis_date: str
    modules: List[str]
    analysis_mode: str
    config: Dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    task_type: str = "manual"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class AnalysisProgress(BaseModel):
    """分析进度"""
    task_id: str
    total_commodities: int
    completed_commodities: int
    current_commodity: Optional[str] = None
    current_module: Optional[str] = None
    status: str = "running"
    results: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class CommodityDataStatus(BaseModel):
    """品种数据状态"""
    technical: bool = False
    basis: bool = False
    inventory: bool = False
    positioning: bool = False
    term_structure: bool = False
    receipt: bool = False


class CacheMeta(BaseModel):
    """缓存元数据"""
    commodity: str
    analysis_date: str
    status: str
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ScheduledConfig(BaseModel):
    """定时分析配置"""
    enabled: bool = False
    schedule_time: str = "20:00"
    commodities: List[str] = Field(default_factory=list)
    analysis_modules: List[str] = Field(default_factory=lambda: [m.value for m in AnalysisModule])
    analysis_mode: str = "complete_flow"
    debate_rounds: int = 3
    ai_model: str = "qwen-plus"
    use_realtime: bool = True
    auto_email: bool = False
    auto_word: bool = False
    update_data_before_analysis: bool = True
    last_run_date: Optional[str] = None
