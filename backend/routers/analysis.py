"""分析相关 API 路由"""
from datetime import datetime
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Dict, Any, List
from loguru import logger

from models.analysis import AnalysisRequest, AnalysisTask, CacheMeta
from services.analysis_service import analysis_manager
from services.cache_service import cache_service
from services.word_service import word_report_service
from fastapi.responses import StreamingResponse
import io

router = APIRouter(prefix="/api/analysis", tags=["分析管理"])


@router.post("/submit", response_model=AnalysisTask)
async def submit_analysis(request: AnalysisRequest):
    """提交分析任务"""
    if not request.commodities:
        raise HTTPException(status_code=400, detail="请至少选择一个品种")
    if not request.modules:
        raise HTTPException(status_code=400, detail="请至少选择一个分析模块")

    task = analysis_manager.submit_task(request, task_type="manual")
    return task


@router.get("/status")
async def get_analysis_status() -> Dict[str, Any]:
    """获取分析状态"""
    return analysis_manager.get_status()


@router.get("/progress")
async def get_analysis_progress() -> Dict[str, Any]:
    """获取分析进度（轮询用）"""
    status = analysis_manager.get_status()
    if status.get("has_active_task"):
        return {
            "running": True,
            "progress": status.get("progress"),
            "results": status.get("results", {}),
            "status": status.get("status"),
        }
    return {"running": False, "progress": None, "results": {}, "status": "idle"}


@router.get("/results/{commodity}")
async def get_commodity_result(commodity: str) -> Dict[str, Any]:
    """获取指定品种的分析结果"""
    status = analysis_manager.get_status()
    results = status.get("results", {})
    if commodity not in results:
        # 🔧 修复(B7)：先尝试当日缓存，再回退该品种最近一次缓存，避免后端重启后查不到历史结果
        today = datetime.now().strftime("%Y-%m-%d")
        cached = cache_service.load_cache(commodity, today)
        if cached:
            return cached
        cached = cache_service.load_latest_cache(commodity)
        if cached:
            return cached
        raise HTTPException(status_code=404, detail=f"未找到 {commodity} 的分析结果")
    return results[commodity]


@router.get("/cache/list")
async def list_cache() -> List[Dict[str, Any]]:
    """列出所有缓存结果"""
    return cache_service.list_cached_results()


@router.post("/cache/load/{commodity}/{analysis_date}")
async def load_cache(commodity: str, analysis_date: str) -> Dict[str, Any]:
    """加载缓存结果"""
    result = cache_service.load_cache(commodity, analysis_date)
    if not result:
        raise HTTPException(status_code=404, detail="缓存不存在或已过期")
    return result


@router.delete("/cache/{commodity}/{analysis_date}")
async def delete_cache(commodity: str, analysis_date: str) -> Dict[str, str]:
    """删除缓存"""
    success = cache_service.delete_cache(commodity, analysis_date)
    if not success:
        raise HTTPException(status_code=500, detail="删除缓存失败")
    return {"message": "缓存已删除"}


@router.post("/word-report")
async def generate_word_report(
    include_charts: bool = False,
    commodities: str = "",
):
    """生成 Word 报告"""
    if not word_report_service.available:
        raise HTTPException(status_code=503, detail="Word报告功能不可用，请安装 python-docx")

    status = analysis_manager.get_status()
    results = status.get("results", {})

    if commodities:
        commodity_list = [c.strip().upper() for c in commodities.split(",") if c.strip()]
        filtered = {k: v for k, v in results.items() if k in commodity_list}
    else:
        commodity_list = []
        filtered = results

    # 🔧 修复(B7)：内存结果为空时，回退读取请求品种的最近缓存再生成报告
    if not filtered and commodity_list:
        for c in commodity_list:
            if c not in filtered:
                cached = cache_service.load_latest_cache(c)
                if cached:
                    filtered[c] = cached
        if filtered:
            logger.info(f"Word 报告基于最近缓存生成: {list(filtered.keys())}")

    if not filtered:
        raise HTTPException(status_code=400, detail="没有可生成报告的分析结果")

    current_analysis = {
        "analysis_date": list(filtered.values())[0].get("analysis_date", datetime.now().strftime("%Y-%m-%d")),
    } if filtered else None

    buffer = word_report_service.create_report(
        results=filtered,
        current_analysis=current_analysis,
        include_charts=include_charts,
    )

    if not buffer:
        raise HTTPException(status_code=500, detail="报告生成失败")

    filename = f"期货分析报告_{commodities or 'all'}.docx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
