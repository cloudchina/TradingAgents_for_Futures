"""记忆体系数据模型 - 阶段1骨架。

对应 memory-system-plan.md 第二章三层 + 一图谱设计：
- Episode（情景记忆）/ Semantic（语义记忆）/ RelationMetric（关联图谱）
- MemoryInjection（【三评 F】埋点）/ ConsolidationRun（巩固审计）

字段命名与 SQLite 表对应；判据字段与展示字段的拆分见【三评】映射约定。
"""
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum


# ─────────────────────────────────────────────────────────────
# 枚举
# ─────────────────────────────────────────────────────────────

class EpisodeStatus(str, Enum):
    """【三评 A】outcome 列取值与 status 联动：
    - pending: outcome=NULL
    - resolved: outcome={hit,realized_return,MAE,holding_days,rolled,actual_contract}
    - unverifiable: outcome={"unverifiable_reason":"no_csv"}
    - parse_failed: outcome=NULL，不入 compute_stats() 分母
    """
    PENDING = "pending"
    RESOLVED = "resolved"
    UNVERIFIABLE = "unverifiable"
    PARSE_FAILED = "parse_failed"


class SemanticStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class Direction(str, Enum):
    """【三评】归一化方向，取自 final_decision 英文"""
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class ConfidenceLevel(str, Enum):
    """【三评】置信度中文等级"""
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


# ─────────────────────────────────────────────────────────────
# 【三评】中英文 / 数值翻译映射
# ─────────────────────────────────────────────────────────────

DIRECTION_TO_VIEW = {
    Direction.LONG: "看多",
    Direction.SHORT: "看空",
    Direction.NEUTRAL: "中性",
}
VIEW_TO_DIRECTION = {v: k for k, v in DIRECTION_TO_VIEW.items()}


def confidence_to_level(conf: float) -> ConfidenceLevel:
    """【三评】数值 → 中文等级：>=0.7 高 / 0.5~0.7 中 / <0.5 低"""
    if conf >= 0.7:
        return ConfidenceLevel.HIGH
    if conf >= 0.5:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


# ─────────────────────────────────────────────────────────────
# 数据模型（与 SQLite 表对应）
# ─────────────────────────────────────────────────────────────

class Episode(BaseModel):
    """情景记忆 - 单次分析结论"""
    id: Optional[int] = None
    symbol: str
    analysis_date: str                          # YYYY-MM-DD
    run_id: str                                  # 复用 AnalysisTask.task_id + commodity
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    direction: str                               # long|short|neutral，判据
    direction_view: str                          # 看多|看空|中性，展示
    confidence: float                            # 0.0~1.0 数值，判据
    confidence_level: str                        # 高|中|低，展示
    ref_price: Optional[float] = None             # 仅展示，回填禁用（复权漂移）
    contract: Optional[str] = None                # 审计字段，不参与校准判据
    summary: str = ""
    key_evidence: List[str] = Field(default_factory=list)
    decision: Dict[str, Any] = Field(default_factory=dict)
    horizon: int = 10                            # 5 or 10 交易日
    status: str = EpisodeStatus.PENDING.value
    outcome: Optional[Dict[str, Any]] = None
    is_canonical: bool = False
    invoked_count: int = 0
    failed_invocations: int = 0
    # 【三评 F】冗余埋点：本次 episode 关联的总注入 token
    injected_tokens: int = 0


class Semantic(BaseModel):
    """语义记忆 - 巩固 Job 产出的可复用规律"""
    id: Optional[int] = None
    symbol: str
    claim: str
    category: str                                # 规律|季节性|结构|风险
    evidence_count: int = 0
    confidence: float = 0.0                      # 合成
    stat_confidence: float = 0.0                 # 命中率分量
    llm_confidence: float = 0.0                 # LLM 自评分量
    first_seen: str = Field(default_factory=lambda: datetime.now().isoformat())
    last_seen: str = Field(default_factory=lambda: datetime.now().isoformat())
    sources: List[int] = Field(default_factory=list)  # episode_ids
    status: str = SemanticStatus.DRAFT.value
    invoked_count: int = 0
    failed_invocations: int = 0
    auto_activated_at: Optional[str] = None
    reviewed_by: Optional[str] = None


class RelationMetric(BaseModel):
    """关联图谱动态相关"""
    pair_key: str                                # "-".join(sorted([a,b]))
    a: str
    b: str
    window: int                                  # 60 or 120
    corr: Optional[float] = None
    lead_symbol: Optional[str] = None
    lag_days: Optional[int] = None
    corr_source: str = "static_only"             # static_only | dynamic
    sample_size: int = 0
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class MemoryInjection(BaseModel):
    """【三评 F】记忆注入埋点 - 每次 build_context 写一条"""
    id: Optional[int] = None
    run_id: str
    symbol: str
    injected_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    segment: str                                 # stm | ltm | relation
    episode_ids: List[int] = Field(default_factory=list)
    semantic_ids: List[int] = Field(default_factory=list)
    token_count: int = 0
    char_count: int = 0


class ConsolidationRun(BaseModel):
    """巩固 Job 审计"""
    run_id: str
    started_at: str
    finished_at: Optional[str] = None
    input_episode_ids: List[int] = Field(default_factory=list)
    last_episode_id: int = 0                     # 水位线，防重复触发
    output_semantic_ids: List[int] = Field(default_factory=list)
    model: str = ""
    token_usage: int = 0
    status: str = "running"
    reviewer: Optional[str] = None
