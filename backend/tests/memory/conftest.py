"""memory 子包 conftest - 提供 in-memory / 临时文件 SQLite fixture。

注意 sqlite3 :memory: 在多连接场景下各自独立，无法测 WAL；
测试统一用 tmp_path 临时文件 DB，更接近生产路径，且能验证 WAL 与部分唯一索引。
"""
import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

from services.memory_service import MemoryService


@pytest.fixture
def memory_svc(tmp_path: Path) -> Iterator[MemoryService]:
    """每个测试一个独立临时 DB；上下文管理 + migrate 一次。"""
    db_path = tmp_path / "memory_test.db"
    svc = MemoryService(db_path=db_path)
    svc.init_if_needed()
    yield svc
    # tmp_path 由 pytest 自动清理


@pytest.fixture
def memory_conn(memory_svc: MemoryService):
    """直接拿一个原始 sqlite3 连接做 PRAGMA 检查。"""
    with memory_svc._connect() as conn:
        yield conn
