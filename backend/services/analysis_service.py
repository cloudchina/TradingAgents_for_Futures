"""分析服务 - 核心分析流程管理"""
import asyncio
import threading
import time
import uuid
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable

import pandas as pd
from loguru import logger

from core.settings import settings
from models.analysis import (
    AnalysisRequest, AnalysisTask, AnalysisProgress,
    AnalysisStatus, AnalysisMode, CacheMeta
)
from services.cache_service import cache_service
from agents.debate.orchestrator import DebateOrchestrator
from agents.react_agent import ReActAgent
from agents.tools.tool_specs import ALL_TOOLS
from agents.tools.data_reader import read_module_data, MODULE_NAME_MAP, _resolve_path
from agents.tools.news_tool import search_news_data
from agents.prompts.system_prompts import (
    TRADER_PROMPT, RISK_MANAGER_PROMPT, EXECUTIVE_PROMPT,
)


def _pick_date_col(df: pd.DataFrame) -> Optional[str]:
    """按列名找出 CSV 中的日期列，而非默认第一列。

    历史原因：technical_analysis 部分品种 ohlc_data.csv 首列曾混入 symbol/date，
    必须按“时间/日期/date”等列名识别真正的日期列。
    """
    if df is None or len(df.columns) == 0:
        return None
    for name in ("时间", "日期", "trade_date", "date"):
        if name in df.columns:
            return name
    for col in df.columns:
        low = str(col).lower()
        if "date" in low or "时间" in str(col) or "日期" in str(col):
            return col
    return df.columns[0]


def _normalize_date_value(value: Any) -> str:
    s = str(value).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


class AnalysisManager:
    """分析管理器 - 管理分析队列和执行"""

    def __init__(self):
        self.current_task: Optional[AnalysisTask] = None
        self.progress: Optional[AnalysisProgress] = None
        self.results: Dict[str, Any] = {}
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._task_queue: List[AnalysisTask] = []
        # 任务完成监听（供定时自动分析在结束后自动生成 Word/发送邮件）
        self._completion_hooks: List[Callable[[AnalysisTask], None]] = []

    def register_completion_hook(self, hook: Callable[[AnalysisTask], None]) -> Callable[[AnalysisTask], None]:
        """注册任务完成回调，返回原 hook 便于注销。"""
        if hook not in self._completion_hooks:
            self._completion_hooks.append(hook)
        return hook

    def unregister_completion_hook(self, hook: Callable[[AnalysisTask], None]):
        """注销任务完成回调。"""
        if hook in self._completion_hooks:
            self._completion_hooks.remove(hook)

    def submit_task(self, request: AnalysisRequest, task_type: str = "manual") -> AnalysisTask:
        """提交分析任务"""
        task = AnalysisTask(
            task_id=str(uuid.uuid4())[:8],
            commodities=request.commodities,
            analysis_date=request.analysis_date,
            modules=[m.value for m in request.modules],
            analysis_mode=request.analysis_mode.value,
            config={
                "ai_model": request.ai_model,
                "use_realtime": request.use_realtime,
                "debate_rounds": request.debate_rounds,
                "force_refresh": request.force_refresh,
            },
            task_type=task_type,
        )

        with self._lock:
            if self.current_task and self.current_task.status == "running":
                # 加入队列
                task.status = "queued"
                self._task_queue.append(task)
                logger.info(f"任务 {task.task_id} 加入队列 (队列长度: {len(self._task_queue)})")
            else:
                self._start_task(task)

        return task

    def _start_task(self, task: AnalysisTask):
        """启动分析任务"""
        self.current_task = task
        task.status = "running"
        self.progress = AnalysisProgress(
            task_id=task.task_id,
            total_commodities=len(task.commodities),
            completed_commodities=0,
        )
        self.results = {}

        # 启动后台线程
        self._thread = threading.Thread(
            target=self._run_analysis,
            args=(task,),
            daemon=True,
        )
        self._thread.start()
        logger.info(f"分析任务 {task.task_id} 已启动: {task.commodities}")

    def _run_analysis(self, task: AnalysisTask):
        """在后台线程中执行分析"""
        try:
            for i, commodity in enumerate(task.commodities):
                if self.progress:
                    self.progress.current_commodity = commodity

                logger.info(f"开始分析 {commodity} ({i+1}/{len(task.commodities)})")

                # 检查缓存
                if not task.config.get("force_refresh", False):
                    cached = cache_service.load_cache(commodity, task.analysis_date)
                    if cached:
                        self.results[commodity] = cached
                        if self.progress:
                            self.progress.completed_commodities = i + 1
                        logger.info(f"使用缓存结果: {commodity}")
                        continue

                # 执行分析（这里调用实际的分析模块）
                result = self._analyze_commodity(commodity, task)
                self.results[commodity] = result

                # 保存缓存
                cache_service.save_cache(commodity, task.analysis_date, result)

                if self.progress:
                    self.progress.completed_commodities = i + 1

            task.status = "completed"
            if self.progress:
                self.progress.status = "completed"

            logger.info(f"分析任务 {task.task_id} 完成")

        except Exception as e:
            logger.error(f"分析任务 {task.task_id} 失败: {e}")
            task.status = "failed"
            if self.progress:
                self.progress.status = "failed"
                self.progress.errors.append(str(e))
        finally:
            # 通知任务完成监听（必须在此处、且在取出下一个任务之前触发，
            # 否则队列中后续任务启动时会覆盖 self.results，导致快照丢失）
            for hook in list(self._completion_hooks):
                try:
                    hook(task)
                except Exception as e:
                    logger.error(f"任务完成回调执行失败: {e}")
            # 处理队列中的下一个任务
            with self._lock:
                if self._task_queue:
                    next_task = self._task_queue.pop(0)
                    self._start_task(next_task)

    def _analyze_commodity(self, commodity: str, task: AnalysisTask) -> Dict[str, Any]:
        """分析单个品种 - 调用实际分析模块"""
        result = {
            "commodity": commodity,
            "analysis_date": task.analysis_date,
            "modules": {},
            "executive_decision": {},
            "status": "completed",
            "timestamp": datetime.now().isoformat(),
        }

        # 按模块执行分析
        for module_name in task.modules:
            if self.progress:
                self.progress.current_module = module_name
            try:
                module_result = self._run_module_analysis(commodity, module_name, task)
                result["modules"][module_name] = module_result
            except Exception as e:
                logger.error(f"模块 {module_name} 分析失败: {e}")
                result["modules"][module_name] = {
                    "status": "failed",
                    "error": str(e),
                }

        # 完整流程模式：执行辩论、交易员、风控、决策
        if task.analysis_mode == "complete_flow":
            try:
                result["debate"] = self._run_debate(commodity, result["modules"], task)
                result["trader"] = self._run_trader(commodity, result["modules"], result["debate"], task)
                result["risk_management"] = self._run_risk_management(commodity, result["trader"], task)
                result["executive_decision"] = self._run_executive_decision(
                    commodity, result["trader"], result["risk_management"], task
                )
            except Exception as e:
                logger.error(f"决策流程失败: {e}")
                result["executive_decision"] = {"status": "failed", "error": str(e)}

        return result

    def _run_module_analysis(self, commodity: str, module_name: str, task: AnalysisTask) -> Dict[str, Any]:
        """运行单个模块分析 - 只读 CSV 元信息（不调 LLM）。

        真正的 LLM 推理统一放到辩论阶段做，避免重复调用。
        本阶段只确认数据可用性 + 行数 + 最新日期。
        """
        logger.info(f"运行模块分析: {commodity}/{module_name}")

        # news 模块无本地 CSV，单独标记
        if module_name == "news":
            return {
                "module_name": module_name,
                "status": "skipped",
                "reason": "news 模块在辩论阶段通过 search_news_data 工具动态查询",
            }

        # 归一模块名并查路径
        real_module = MODULE_NAME_MAP.get(module_name, module_name)
        path = _resolve_path(real_module, commodity)
        if path is None:
            return {
                "module_name": module_name,
                "status": "skipped",
                "reason": f"未知模块: {module_name}",
            }
        if not path.exists():
            return {
                "module_name": module_name,
                "status": "no_data",
                "error": f"无 CSV: {path}",
            }

        try:
            df = pd.read_csv(path)
            latest_date = ""
            if not df.empty and len(df.columns) > 0:
                date_col = _pick_date_col(df)
                if date_col is not None:
                    latest_date = _normalize_date_value(df.iloc[-1].get(date_col, ""))
            return {
                "module_name": module_name,
                "status": "completed",
                "rows": len(df),
                "latest_date": latest_date,
                "data_path": str(path),
            }
        except Exception as e:
            logger.error(f"模块 {module_name} 读 CSV 失败: {e}")
            return {
                "module_name": module_name,
                "status": "failed",
                "error": str(e),
            }

    def _make_agent(self, role_name: str, prompt: str, model: str) -> ReActAgent:
        """构造单 agent ReAct（trader/risk/executive 共用）。"""
        return ReActAgent(
            role_name=role_name,
            system_prompt=prompt,
            tools=ALL_TOOLS,
            tool_map={
                "read_module_data": read_module_data,
                "search_news_data": search_news_data,
            },
            model=model,
        )

    def _run_debate(self, commodity: str, modules: Dict, task: AnalysisTask) -> Dict[str, Any]:
        """运行多空辩论 - 调用 DebateOrchestrator"""
        max_rounds = task.config.get("debate_rounds", 3)
        model = task.config.get("ai_model", "qwen-plus")
        orch = DebateOrchestrator(
            model=model,
            max_rounds=max_rounds,
            symbol=commodity,
            modules=list(modules.keys()) if isinstance(modules, dict) else list(modules),
        )
        try:
            return orch.run()
        except Exception as e:
            logger.error(f"辩论编排失败: {e}")
            return {
                "rounds": max_rounds,
                "actual_rounds": 0,
                "terminated_reason": "failed",
                "error": str(e),
                "history": [],
                "winner": "split",
            }

    def _run_trader(self, commodity: str, modules: Dict, debate: Dict, task: AnalysisTask) -> Dict[str, Any]:
        """运行交易员分析 - 单 agent ReAct"""
        model = task.config.get("ai_model", "qwen-plus")
        agent = self._make_agent("Trader", TRADER_PROMPT, model)
        # 给 trader 看辩论摘要（避免 prompt 过长）
        debate_summary = {
            "winner": debate.get("winner"),
            "terminated_reason": debate.get("terminated_reason"),
            "bull_final": debate.get("bull_final", {}),
            "bear_final": debate.get("bear_final", {}),
        }
        user_msg = (
            f"品种: {commodity}\n"
            f"=== 辩论结论 ===\n{json.dumps(debate_summary, ensure_ascii=False)}\n"
            f"=== 请基于辩论结论和 technical_analysis 数据，给出交易方案 ==="
        )
        result = agent.run(user_msg)
        parsed = result.get("parsed", {})
        parsed["trace"] = result.get("trace", [])
        return parsed

    def _run_risk_management(self, commodity: str, trader_result: Dict, task: AnalysisTask) -> Dict[str, Any]:
        """运行风控管理 - 单 agent ReAct"""
        model = task.config.get("ai_model", "qwen-plus")
        agent = self._make_agent("RiskManager", RISK_MANAGER_PROMPT, model)
        user_msg = (
            f"品种: {commodity}\n"
            f"=== 交易员方案 ===\n{json.dumps(trader_result, ensure_ascii=False)}\n"
            f"=== 请审核风险并给风控意见，可复查 technical_analysis 的 ATR/波动率 ==="
        )
        result = agent.run(user_msg)
        parsed = result.get("parsed", {})
        parsed["trace"] = result.get("trace", [])
        return parsed

    def _run_executive_decision(self, commodity: str, trader: Dict, risk: Dict, task: AnalysisTask) -> Dict[str, Any]:
        """运行CIO最终决策 - 单 agent ReAct（不调工具，纯推理）"""
        model = task.config.get("ai_model", "qwen-plus")
        # CIO 不需要工具，纯推理
        agent = ReActAgent(
            role_name="Executive",
            system_prompt=EXECUTIVE_PROMPT,
            tools=None,
            tool_map={},
            model=model,
            max_iterations=2,  # CIO 只需一轮综合拍板
        )
        user_msg = (
            f"品种: {commodity}\n"
            f"=== 交易员方案 ===\n{json.dumps(trader, ensure_ascii=False)}\n\n"
            f"=== 风控意见 ===\n{json.dumps(risk, ensure_ascii=False)}\n"
            f"=== 请综合给出最终决策 ==="
        )
        result = agent.run(user_msg)
        parsed = result.get("parsed", {})
        parsed["trace"] = result.get("trace", [])
        return parsed

    def get_status(self) -> Dict[str, Any]:
        """获取当前分析状态"""
        with self._lock:
            if not self.current_task:
                return {
                    "has_active_task": False,
                    "queue_length": len(self._task_queue),
                }

            return {
                "has_active_task": True,
                "task_id": self.current_task.task_id,
                "status": self.current_task.status,
                "commodities": self.current_task.commodities,
                "progress": self.progress.model_dump() if self.progress else None,
                "results": self.results,
                "queue_length": len(self._task_queue),
            }

    def is_running(self) -> bool:
        """是否有分析正在运行"""
        return (
            self.current_task is not None
            and self.current_task.status == "running"
            and self._thread is not None
            and self._thread.is_alive()
        )


# 全局单例
analysis_manager = AnalysisManager()
