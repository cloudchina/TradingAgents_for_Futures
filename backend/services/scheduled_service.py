"""定时自动分析服务

设计背景：该功能面向“Linux/Windows 后台无人值守运行”场景——
每天到点后 自动更新数据 → 自动分析 → 自动生成/保存 Word 报告 → 自动发送邮件，
全程不依赖前端页面。可通过 /api/scheduled/* 接口控制，也可用
backend/run_scheduled_task.py 单次运行交由系统计划任务(cron/任务计划程序)调用。
"""
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Callable, List
from loguru import logger

from core.settings import settings
from models.analysis import ScheduledConfig, AnalysisRequest, AnalysisMode, AnalysisModule
from services.analysis_service import analysis_manager
from services.data_service import data_manager_service
from services.word_service import word_report_service
from services.email_service import email_service

# 分析模块 → 本地数据模块 key（news 无本地 CSV，不触发数据更新）
_MODULE_TO_DATA_MODULE = {
    "inventory": "inventory",
    "positioning": "positioning",
    "term_structure": "term_structure",
    "technical": "technical_analysis",
    "technical_analysis": "technical_analysis",
    "basis": "basis",
    "receipt": "receipt",
    "news": None,
}


def update_analysis_data(commodities: List[str], analysis_modules: List[str], target_date: str) -> Dict[str, Any]:
    """自动更新本次分析所需模块的本地数据（update_data_before_analysis 的后端实现）。

    只更新配置了 analysis_modules 中对应的数据模块，并限定到本次分析的品种，
    避免无关品种的全量拉取耗时。
    """
    summary: Dict[str, Any] = {"modules": [], "failed": []}
    data_modules = []
    for m in analysis_modules:
        dm = _MODULE_TO_DATA_MODULE.get(str(m))
        if dm and dm not in data_modules:
            data_modules.append(dm)
    if not data_modules:
        return summary

    for dm in data_modules:
        try:
            logger.info(f"[自动更新数据] 开始更新模块 {dm} ...")
            result = data_manager_service.run_data_update(
                dm, target_date=target_date, varieties=commodities or None
            )
            summary["modules"].append(dm)
            if result and getattr(result, "failed_count", 0):
                summary["failed"].append(f"{dm}: {result.failed_count} 个品种失败")
            logger.info(f"[自动更新数据] 模块 {dm} 更新完成")
        except Exception as e:
            summary["failed"].append(f"{dm}: {e}")
            logger.error(f"[自动更新数据] 模块 {dm} 更新失败: {e}")
    return summary


def save_analysis_word_report(results: Dict[str, Any], analysis_date: str) -> Optional[Path]:
    """生成并落盘 Word 分析报告，返回文件路径（供 auto_word 与邮件附件共用）。"""
    if not word_report_service.available:
        logger.warning("python-docx 未安装，无法生成 Word 报告")
        return None
    if not results:
        logger.warning("无可生成报告的分析结果")
        return None

    buffer = word_report_service.create_report(
        results=results,
        current_analysis={"analysis_date": analysis_date},
        include_charts=False,
    )
    if buffer is None:
        return None

    report_dir = Path(settings.RESULTS_DIR) / "auto_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = report_dir / f"商品期货AI分析报告_{analysis_date}_{ts}.docx"
    with open(path, "wb") as f:
        f.write(buffer.getvalue())
    logger.info(f"Word 报告已保存: {path}")
    return path


class ScheduledAnalysisRunner:
    """定时分析运行器"""

    def __init__(self, config: ScheduledConfig, callback: Optional[Callable] = None):
        self.config = config
        self.callback = callback
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        # 最近一次由本定时器触发的任务 id（用于在完成回调中精确匹配）
        self._last_triggered_task_id: Optional[str] = None

    def start(self):
        """启动定时任务"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # 注册任务完成回调（幂等）
        analysis_manager.register_completion_hook(self._on_task_completed)
        logger.info(f"定时分析已启动: 每天 {self.config.schedule_time}")

    def stop(self):
        """停止定时任务"""
        self._running = False
        self._stop_event.set()
        analysis_manager.unregister_completion_hook(self._on_task_completed)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("定时分析已停止")

    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def wait_for_completion(self, timeout: Optional[float] = None) -> bool:
        """等待最近一次触发的任务全流程结束（含报告生成/邮件发送）。"""
        deadline = (time.time() + timeout) if timeout else None
        while self._last_triggered_task_id is not None:
            if deadline is not None and time.time() > deadline:
                return False
            time.sleep(5)
        return True

    def _run(self):
        """主循环"""
        while not self._stop_event.is_set():
            now = datetime.now()
            schedule_time = self.config.schedule_time
            target_hour, target_minute = map(int, schedule_time.split(":"))

            # 计算下次执行时间
            target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
            if now > target:
                # 今天已过，等到明天（用 timedelta 跨月/跨年安全）
                target = (now + timedelta(days=1)).replace(
                    hour=target_hour, minute=target_minute, second=0, microsecond=0
                )

            wait_seconds = (target - now).total_seconds()
            logger.info(f"下次定时分析: {target.strftime('%Y-%m-%d %H:%M')} (等待 {wait_seconds/3600:.1f} 小时)")

            # 分段等待，以便能及时响应停止
            while wait_seconds > 0 and not self._stop_event.is_set():
                sleep_time = min(wait_seconds, 60)
                self._stop_event.wait(sleep_time)
                wait_seconds -= sleep_time

            if self._stop_event.is_set():
                break

            # 执行分析
            try:
                self._on_trigger()
            except Exception as e:
                logger.error(f"定时分析执行失败: {e}")

    def _on_trigger(self):
        """触发一次自动分析：先可选更新数据，再提交分析任务。"""
        logger.info(f"定时分析触发: {self.config.commodities}")
        analysis_date = datetime.now().strftime("%Y-%m-%d")

        # 1) update_data_before_analysis —— 分析前自动更新数据（无人值守的关键环节）
        if self.config.update_data_before_analysis:
            logger.info("[定时分析] update_data_before_analysis=True，开始自动更新数据...")
            update_analysis_data(
                commodities=self.config.commodities,
                analysis_modules=self.config.analysis_modules,
                target_date=analysis_date,
            )

        # 2) 提交分析任务
        request = AnalysisRequest(
            commodities=self.config.commodities,
            analysis_date=analysis_date,
            modules=[AnalysisModule(m) for m in self.config.analysis_modules],
            analysis_mode=AnalysisMode(self.config.analysis_mode),
            ai_model=self.config.ai_model,
            use_realtime=self.config.use_realtime,
            debate_rounds=self.config.debate_rounds,
        )

        task = analysis_manager.submit_task(request, task_type="scheduled")
        self._last_triggered_task_id = task.task_id

        if self.callback:
            self.callback(task)

    def _on_task_completed(self, task):
        """分析任务完成回调：匹配本次触发的任务，执行保存 Word / 发送邮件。"""
        if not self._last_triggered_task_id or task.task_id != self._last_triggered_task_id:
            return
        self._last_triggered_task_id = None

        if task.status != "completed":
            logger.error(f"定时分析任务 {task.task_id} 未完成(status={task.status})，跳过自动报告")
            return

        # 快照当前任务的结果（此刻 results 尚未被下一任务覆盖）
        results = {
            k: v for k, v in analysis_manager.results.items()
            if k in task.commodities and v is not None
        }
        analysis_date = task.analysis_date

        # 3) auto_word / auto_email —— 生成 Word 报告并保存，随后作为邮件附件发送
        report_path: Optional[Path] = None
        if self.config.auto_word or self.config.auto_email:
            report_path = save_analysis_word_report(results, analysis_date)
            if report_path is None:
                logger.warning("Word 报告生成失败，跳过 auto_word / auto_email 附件")

        if self.config.auto_word and report_path:
            logger.info(f"[定时分析] auto_word 已保存: {report_path}")

        if self.config.auto_email:
            subject = f"[期货AI自动分析] {analysis_date} 交易日报"
            body = (
                f"分析日期: {analysis_date}\n"
                f"分析品种: {', '.join(task.commodities)}\n"
                f"分析模块: {', '.join(task.modules)}\n"
                f"报告已由系统自动生成，详见附件。\n"
                f"—— 商品期货 AI 交易分析系统"
            )
            sent = email_service.send_analysis_report(
                subject=subject, body=body, attachment_path=report_path
            )
            if not sent:
                logger.error("[定时分析] auto_email 发送失败，请检查 SMTP_* 环境变量配置")

        # 更新最后运行日期（放在所有附加动作完成后）
        self.config.last_run_date = analysis_date
        logger.info(f"定时分析任务 {task.task_id} 全流程完成 (auto_word={self.config.auto_word}, auto_email={self.config.auto_email})")


# 全局单例
_scheduled_runner: Optional[ScheduledAnalysisRunner] = None


def get_scheduled_runner() -> Optional[ScheduledAnalysisRunner]:
    return _scheduled_runner


def set_scheduled_runner(runner: Optional[ScheduledAnalysisRunner]):
    global _scheduled_runner
    _scheduled_runner = runner


def run_scheduled_cycle_once(config: ScheduledConfig, timeout: Optional[float] = None) -> bool:
    """立即执行一轮完整的“更新数据→分析→报告→邮件”流程（供后台脚本调用）。

    由 cron / Windows 任务计划程序定时触发 backend/run_scheduled_task.py。
    会阻塞至本轮分析全部结束（或超时）。
    """
    logger.info("单次定时分析任务开始执行...")
    runner = ScheduledAnalysisRunner(config)
    analysis_manager.register_completion_hook(runner._on_task_completed)
    try:
        runner._on_trigger()
        return runner.wait_for_completion(timeout=timeout)
    except Exception as e:
        logger.error(f"单次定时分析执行失败: {e}")
        return False
    finally:
        analysis_manager.unregister_completion_hook(runner._on_task_completed)
