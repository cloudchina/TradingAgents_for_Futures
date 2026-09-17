#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
商品期货 Trading Agents 系统 - FastAPI 后端
"""
import sys
from pathlib import Path
from contextlib import asynccontextmanager

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.logging import setup_logging  # noqa: E402
from core.settings import settings  # noqa: E402

# 统一日志：控制台 INFO + 文件 DEBUG（内部已处理 Windows GBK 控制台导致的编码问题）
# 需在业务模块导入前完成，否则导入期的日志会走 loguru 默认格式
setup_logging(logs_dir=settings.LOGS_DIR, console_level="INFO", file_level="DEBUG")

from loguru import logger  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from core.llm_config import llm_config  # noqa: E402
from core.net import install_default_timeout  # noqa: E402

# 取数模块大多经由 akshare 请求第三方数据源且不设超时，统一注入默认超时避免任务被永久阻塞
# 必须在 import 之后调用（原来写在 import 之前会 NameError，后端直接起不来）
install_default_timeout()
from routers import analysis, data, llm, memory, scheduled, system  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    logger.info("🚀 商品期货 Trading Agents 后端启动中...")
    logger.info(f"  数据目录: {settings.DATA_ROOT_DIR}")
    logger.info(f"  缓存目录: {settings.CACHE_DIR}")
    # 🔧 修复(B5)：支持品种数以 commodities.yaml 为唯一来源（与 /api/data、/api/system
    # 一致），此前 core/settings.py 曾硬编码 36 个，该字段已在 B5 中一并删除。
    from services.commodity_service import commodity_service
    logger.info(f"  支持品种数: {len(commodity_service.get_symbols())}（commodities.yaml）")
    logger.info(f"  LLM 配置: {llm_config.describe()}（可在前端「LLM 配置」页面热更新）")
    logger.info(f"  LLM 配置文件: {llm_config.config_path}")

    # 确保目录存在
    for d in [settings.DATA_ROOT_DIR, settings.CACHE_DIR, settings.LOGS_DIR, settings.RESULTS_DIR]:
        Path(d).mkdir(parents=True, exist_ok=True)

    # 【三评 / 阶段1】记忆库初始化：建目录 + schema migrate
    # 失败不阻塞启动，routers/memory.py 仍可被调用并按需重试
    try:
        from services.memory_service import memory_service
        from core.settings import get_memory_dir
        Path(get_memory_dir()).mkdir(parents=True, exist_ok=True)
        memory_service.init_if_needed()
        logger.info(f"  记忆库已初始化: {memory_service._resolve_db_path()}")
    except Exception as e:
        logger.error(f"  记忆库初始化失败（不阻塞启动）: {e}")

    # 【阶段4】复盘回填调度：独立 daemon 线程，默认每日 20:00 触发
    # 与定时分析解耦（独立 thread + Event，不抢主线程，也不受分析任务阻塞影响）
    if settings.MEMORY_BACKFILL_ENABLED:
        try:
            from services.backfill_scheduler import backfill_runner

            backfill_runner.start()
            logger.info(f"  复盘回填调度已启动: 每天 {backfill_runner.schedule_time}")
        except Exception as e:
            logger.error(f"  复盘回填调度启动失败（不阻塞启动）: {e}")

    yield

    logger.info("👋 后端服务关闭")


app = FastAPI(
    title="商品期货 Trading Agents API",
    description="AI驱动的期货量化交易分析系统",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制为前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(system.router)
app.include_router(data.router)
app.include_router(analysis.router)
app.include_router(scheduled.router)
app.include_router(llm.router)
app.include_router(memory.router)


@app.get("/")
async def root():
    return {
        "name": "商品期货 Trading Agents API",
        "version": "2.0.0",
        "docs": "/docs",
        "status": "running",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="info",
    )
