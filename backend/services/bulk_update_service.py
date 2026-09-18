"""全品种批量更新编排（【三评 G】，见 memory-system-plan.md 四、阶段 0）。

阶段 0 要跑通"59 品种 × 7 模块"的首轮采集：串行调 akshare + 复权 + 写 CSV 估计数小时，
且单点失败就得重跑全程。本服务在**单品种更新器**（`DataManagerService.run_data_update`）
之上加一层编排：

1. **断点续传**：已成功的品种跳过。判定依据是该模块的 CSV 已存在且行数 > 0
   （与 `data_availability.json` 同源口径），`force=True` 时强制重跑。
2. **并发上限**：默认 6 个品种并行（akshare 有反爬，过高会被限流）。
   每个品种在**独立线程里由 run_data_update 新建自己的 Updater 实例**，
   不共享实例状态，避免线程间串味。
3. **失败重试**：失败品种收集后**单批重试**最多 `retries` 次，仍失败进 `failed_symbols`
   （前端可一键只重试这批）。
4. **进度上报**：`progress_cb(stage, done, total, current)`，与 data_task_service 对齐。

不改动单品种更新器本身，失败时可安全回退到原来的串行 `run_data_update`。
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from loguru import logger

from core.settings import settings
from services.data_service import MODULE_CONFIG, data_manager_service

# 并发上限（akshare 限速：4~8 之间，取 6）
DEFAULT_MAX_WORKERS = 6
MAX_WORKERS_LIMIT = 8

# 失败后单批重试次数上限
DEFAULT_RETRIES = 2


class BulkUpdateService:
    """按品种维度并发更新 + 断点续传 + 失败重试。"""

    def __init__(
        self,
        data_svc: Any = None,
        data_root: Optional[Path] = None,
        module_config: Optional[Dict[str, Any]] = None,
    ):
        self.data_svc = data_svc or data_manager_service
        self.root = Path(data_root) if data_root else Path(settings.DATA_ROOT_DIR)
        self.modules = module_config or MODULE_CONFIG

    # ─── 断点续传判定 ───

    def data_file_path(self, module_key: str, symbol: str) -> Optional[Path]:
        cfg = self.modules.get(module_key)
        if not cfg:
            return None
        return self.root / cfg["subdir"] / symbol.upper() / cfg["data_file"]

    def already_done(self, module_key: str, symbol: str) -> bool:
        """该品种该模块是否已有数据（行数 > 0）→ 断点续传跳过。"""
        path = self.data_file_path(module_key, symbol)
        if not path or not path.exists():
            return False
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as f:
                # 只数行数，不解析整表（59 品种 × 7 模块时开销敏感）
                return sum(1 for _ in f) > 1
        except Exception:
            return False

    # ─── 单品种更新 ───

    def update_one(
        self,
        module_key: str,
        symbol: str,
        target_date: Optional[str] = None,
        progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
    ) -> bool:
        """更新单个品种；返回是否成功。

        经 `DataManagerService.run_data_update(module_key, ..., varieties=[symbol])`，
        每次调用内部新建 Updater 实例，天然线程隔离。
        """
        try:
            result = self.data_svc.run_data_update(
                module_key,
                target_date=target_date,
                varieties=[symbol],
                progress_cb=progress_cb,
            )
        except Exception as e:
            logger.warning(f"[bulk] {module_key} {symbol} 更新异常 {e}")
            return False
        status = getattr(result, "status", None) or (
            result.get("status") if isinstance(result, dict) else None
        )
        return status == "success"

    # ─── 批量编排 ───

    def run_bulk(
        self,
        module_key: str,
        symbols: Optional[List[str]] = None,
        target_date: Optional[str] = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        resume: bool = True,
        force: bool = False,
        retries: int = DEFAULT_RETRIES,
        progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """批量更新一个模块的全部品种。

        Args:
            module_key: MODULE_CONFIG 的 key
            symbols: 品种列表；None = 全部配置品种
            resume: 断点续传（已有数据则跳过）
            force: 强制重跑（无视 resume）
            retries: 失败品种单批重试次数
            max_workers: 并发上限（硬上限 MAX_WORKERS_LIMIT）

        Returns:
            {"module", "target_date", "total", "attempted", "updated": [...],
             "skipped": [...], "failed_symbols": [...], "attempts": {sym: n},
             "elapsed_sec": float}
        """
        if module_key not in self.modules:
            return {
                "module": module_key,
                "status": "error",
                "message": f"未知模块: {module_key}",
                "failed_symbols": [],
            }
        if symbols is None:
            symbols = list(self._all_symbols())
        symbols = [s.upper() for s in symbols if s]
        workers = max(1, min(int(max_workers or 1), MAX_WORKERS_LIMIT))

        pending: List[str] = []
        skipped: List[str] = []
        if resume and not force:
            for s in symbols:
                (skipped if self.already_done(module_key, s) else pending).append(s)
        else:
            pending = list(symbols)

        total = len(symbols)
        attempts: Dict[str, int] = {s: 0 for s in symbols}
        updated: List[str] = []
        failed: List[str] = []
        started = time.time()
        done_count = 0
        lock = threading.Lock()

        def _emit(stage: str) -> None:
            if progress_cb:
                progress_cb(stage, done_count, total, "")

        _emit("断点续传：跳过已完成的品种" if skipped else "开始并发更新")

        for round_idx in range(retries + 1):
            if not pending:
                break
            if round_idx > 0:
                logger.info(f"[bulk] {module_key} 第 {round_idx} 次重试 {len(pending)} 个品种")
            still_failed: List[str] = []
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bulk-upd") as pool:
                futures = {
                    pool.submit(
                        self.update_one, module_key, s, target_date,
                        self._sub_cb(progress_cb, lock, total, s) if progress_cb else None,
                    ): s
                    for s in pending
                }
                for fut in as_completed(futures):
                    sym = futures[fut]
                    try:
                        ok = fut.result()
                    except Exception as e:
                        logger.warning(f"[bulk] {module_key} {sym} 异常 {e}")
                        ok = False
                    attempts[sym] = attempts.get(sym, 0) + 1
                    with lock:
                        done_count += 1
                        if ok:
                            updated.append(sym)
                        else:
                            still_failed.append(sym)
                        if progress_cb:
                            progress_cb(
                                f"并发更新中（{workers} 路）",
                                done_count, total, sym,
                            )
            pending = still_failed
            if not pending:
                break

        failed = pending
        elapsed = round(time.time() - started, 2)
        result = {
            "module": module_key,
            "target_date": target_date,
            "status": "success" if not failed else "partial",
            "total": total,
            "attempted": len(symbols) - len(skipped),
            "updated": sorted(updated),
            "skipped": sorted(skipped),
            "failed_symbols": sorted(failed),
            "attempts": attempts,
            "workers": workers,
            "elapsed_sec": elapsed,
        }
        logger.info(
            f"[bulk] {module_key} 完成: 更新 {len(updated)} / 跳过 {len(skipped)} / "
            f"失败 {len(failed)}，耗时 {elapsed}s"
        )
        return result

    # ─── 多模块 ───

    def run_modules(
        self,
        module_keys: List[str],
        symbols: Optional[List[str]] = None,
        target_date: Optional[str] = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        resume: bool = True,
        force: bool = False,
        retries: int = DEFAULT_RETRIES,
        progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """按模块串行、模块内并发地跑多个模块（阶段 0 全量采集用）。"""
        per_module: Dict[str, Any] = {}
        all_failed: Set[str] = set()
        for i, mk in enumerate(module_keys):
            sub = self.run_bulk(
                mk,
                symbols=symbols,
                target_date=target_date,
                max_workers=max_workers,
                resume=resume,
                force=force,
                retries=retries,
                progress_cb=(
                    (lambda mk=mk: (lambda stage, done, total, cur: progress_cb(
                        f"[{i + 1}/{len(module_keys)}] {mk}: {stage}", done, total, cur
                    )))()
                    if progress_cb
                    else None
                ),
            )
            per_module[mk] = sub
            all_failed.update(sub.get("failed_symbols", []))
        return {
            "modules": per_module,
            "failed_symbols": sorted(all_failed),
            "status": "success" if not all_failed else "partial",
        }

    # ─── 辅助 ───

    def _all_symbols(self) -> List[str]:
        try:
            from services.commodity_service import commodity_service

            return commodity_service.get_symbols()
        except Exception as e:
            logger.warning(f"[bulk] 取品种清单失败 {e}")
            return []

    @staticmethod
    def _sub_cb(
        progress_cb: Callable[[str, int, int, str], None],
        lock: threading.Lock,
        total: int,
        symbol: str,
    ) -> Callable[[str, int, int, str], None]:
        """把单品种 Updater 的进度折算成"已完成品种数"维度的进度。"""

        def _cb(stage: str, done: int, _total: int, current: str = "") -> None:
            # 单品种内部进度不展开，避免 done/total 语义被覆盖成行数
            progress_cb(f"{symbol}: {stage}", 0, total, symbol)

        return _cb


# ─────────────────────────────────────────────────────────────
# 单例
# ─────────────────────────────────────────────────────────────

bulk_update_service = BulkUpdateService()
