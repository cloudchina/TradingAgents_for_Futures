"""【三评 G】全品种批量更新：并发上限 / 断点续传 / 失败重试。"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Dict, List

import pytest

from services.bulk_update_service import (
    BulkUpdateService,
    DEFAULT_MAX_WORKERS,
    MAX_WORKERS_LIMIT,
)


MODULES = {
    "inventory": {"subdir": "inventory", "data_file": "inventory.csv"},
}


class FakeDataService:
    """假的 run_data_update：可指定失败品种、记录并发峰值与调用次数。"""

    def __init__(self, fail_for: List[str] = None, delay: float = 0.02):
        self.fail_for = set(fail_for or [])
        self.delay = delay
        self.calls: Dict[str, int] = {}
        self.lock = threading.Lock()
        self._cur = 0
        self.peak = 0

    def run_data_update(self, module_key, target_date=None, varieties=None, progress_cb=None):
        sym = (varieties or ["?"])[0]
        with self.lock:
            self.calls[sym] = self.calls.get(sym, 0) + 1
            self._cur += 1
            self.peak = max(self.peak, self._cur)
        try:
            if progress_cb:
                progress_cb("更新中", 1, 1, sym)
            time.sleep(self.delay)
            status = "error" if sym in self.fail_for else "success"
        finally:
            with self.lock:
                self._cur -= 1

        class R:
            pass

        r = R()
        r.status = status
        r.message = ""
        r.details = ""
        return r


def _seed(root: Path, module: str, symbol: str, lines: int = 5) -> None:
    d = root / MODULES[module]["subdir"] / symbol
    d.mkdir(parents=True, exist_ok=True)
    (d / MODULES[module]["data_file"]).write_text(
        "header\n" + "\n".join(str(i) for i in range(max(0, lines - 1))), encoding="utf-8"
    )


@pytest.fixture
def svc(tmp_path: Path) -> BulkUpdateService:
    return BulkUpdateService(
        data_svc=FakeDataService(), data_root=tmp_path, module_config=MODULES
    )


def test_resume_skips_existing(svc: BulkUpdateService, tmp_path: Path):
    """断点续传：已有数据的品种跳过，force=True 时强制重跑。"""
    _seed(tmp_path, "inventory", "RB")
    res = svc.run_bulk("inventory", symbols=["RB", "HC"], resume=True)
    assert res["skipped"] == ["RB"]
    assert res["updated"] == ["HC"]
    assert svc.data_svc.calls.get("RB") is None

    res2 = svc.run_bulk("inventory", symbols=["RB", "HC"], resume=True, force=True)
    assert res2["skipped"] == []
    assert res2["updated"] == ["HC", "RB"]


def test_resume_disabled_runs_all(svc: BulkUpdateService, tmp_path: Path):
    _seed(tmp_path, "inventory", "RB")
    res = svc.run_bulk("inventory", symbols=["RB"], resume=False)
    assert res["skipped"] == []
    assert res["updated"] == ["RB"]


def test_retry_then_failed_symbols(tmp_path: Path):
    """失败品种：1 次首发 + retries 次重试，仍失败进 failed_symbols。"""
    fake = FakeDataService(fail_for=["J"])
    svc = BulkUpdateService(data_svc=fake, data_root=tmp_path, module_config=MODULES)
    res = svc.run_bulk("inventory", symbols=["J", "JM"], retries=2)
    assert res["failed_symbols"] == ["J"]
    assert res["updated"] == ["JM"]
    assert fake.calls["J"] == 3          # 1 + 2 次重试
    assert res["attempts"]["J"] == 3


def test_failed_symbols_can_be_retried_alone(tmp_path: Path):
    """前端一键重试 failed_symbols：这批单独再跑即可。"""
    fake = FakeDataService(fail_for=["J"], delay=0)
    svc = BulkUpdateService(data_svc=fake, data_root=tmp_path, module_config=MODULES)
    first = svc.run_bulk("inventory", symbols=["J", "JM"], retries=0)
    assert first["failed_symbols"] == ["J"]

    fake.fail_for.clear()
    second = svc.run_bulk("inventory", symbols=first["failed_symbols"], retries=0)
    assert second["failed_symbols"] == []
    assert second["updated"] == ["J"]


def test_concurrency_limit_respected(tmp_path: Path):
    """并发峰值不超过 max_workers（akshare 限速保护）。"""
    fake = FakeDataService(delay=0.05)
    svc = BulkUpdateService(data_svc=fake, data_root=tmp_path, module_config=MODULES)
    symbols = [f"S{i}" for i in range(12)]
    res = svc.run_bulk("inventory", symbols=symbols, max_workers=4)
    assert res["workers"] == 4
    assert fake.peak <= 4
    assert len(res["updated"]) == 12


def test_workers_capped(tmp_path: Path):
    """并发上限硬顶 8，防止被前端传大值把数据源打爆。"""
    fake = FakeDataService(delay=0)
    svc = BulkUpdateService(data_svc=fake, data_root=tmp_path, module_config=MODULES)
    res = svc.run_bulk("inventory", symbols=["RB"], max_workers=100)
    assert res["workers"] == MAX_WORKERS_LIMIT
    assert DEFAULT_MAX_WORKERS == 6


def test_progress_reaches_total(svc: BulkUpdateService):
    """进度回调最终 done == total。"""
    seen = []
    res = svc.run_bulk(
        "inventory", symbols=["A", "B", "C"],
        progress_cb=lambda stage, done, total, cur: seen.append((done, total)),
    )
    assert res["total"] == 3
    assert seen and seen[-1][0] == 3 and seen[-1][1] == 3


def test_unknown_module(svc: BulkUpdateService):
    res = svc.run_bulk("not_a_module", symbols=["RB"])
    assert res["status"] == "error"


def test_run_modules_aggregates(tmp_path: Path):
    """多模块：模块串行、模块内并发，失败品种汇总去重。"""
    fake = FakeDataService(fail_for=["J"], delay=0)
    svc = BulkUpdateService(data_svc=fake, data_root=tmp_path, module_config=MODULES)
    res = svc.run_modules(["inventory", "inventory"], symbols=["J", "JM"], retries=0)
    assert set(res["modules"]) == {"inventory"}
    assert res["failed_symbols"] == ["J"]
    assert res["status"] == "partial"
