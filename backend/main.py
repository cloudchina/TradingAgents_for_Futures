#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
商品期货 Trading Agents 系统 - FastAPI 后端
"""
import sys
import os
from pathlib import Path
from contextlib import asynccontextmanager
from loguru import logger

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.settings import settings
from routers import analysis, data, scheduled, system


# Windows 下若 stdout/stderr 非 UTF-8（如管道/重定向运行且代码页为 GBK），
# print/日志含 emoji(🚀✅❌等) 会抛 UnicodeEncodeError，这里统一重配置为 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 配置日志
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")
log_dir = Path(settings.LOGS_DIR)
log_dir.mkdir(parents=True, exist_ok=True)
logger.add(str(log_dir / "backend.log"), rotation="10 MB", retention="7 days", level="DEBUG")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    logger.info("🚀 商品期货 Trading Agents 后端启动中...")
    logger.info(f"  数据目录: {settings.DATA_ROOT_DIR}")
    logger.info(f"  缓存目录: {settings.CACHE_DIR}")
    logger.info(f"  支持品种数: {len(settings.SUPPORTED_COMMODITIES)}")

    # 确保目录存在
    for d in [settings.DATA_ROOT_DIR, settings.CACHE_DIR, settings.LOGS_DIR, settings.RESULTS_DIR]:
        Path(d).mkdir(parents=True, exist_ok=True)

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
