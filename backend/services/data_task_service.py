#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据更新任务服务（异步后台执行 + 轮询状态）

背景：
- 手动“数据更新/主力合约刷新”在无历史数据时会长时间联网，单次 HTTP 同步等待
  极易触发前端超时（表现为“报错但后端其实成功了”）。
- 本服务把更新放入后台 daemon 线程，HTTP 端立即拿到 task_id，前端通过
  GET /data/tasks/{task_id} 轮询进度（任务式更新场景下，各 Updater 会上报
  精确的逐品种/逐阶段进度）。

约束：
- 同一时刻只允许 1 个任务运行（避免多端重复触发把数据源打爆）；重复提交时
  返回正在运行的任务并标记 already_running=True。
- 任务状态保存在进程内内存中（当前 uvicorn 为单进程）；进程重启后进度记录
  丢失，但已完成并落盘的数据不受影响。
"""
import threading
import uuid
from datetime import datetime
from typing import Callable, Dict, List, Optional

from loguru import logger

from services.data_service import MODULE_CONFIG, data_manager_service
from services.commodity_service import commodity_service
from services.bulk_update_service import bulk_update_service

# 任务保留条数（超出后清理最早的历史任务）
MAX_KEEP_TASKS = 50


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DataTaskService:
    """数据更新后台任务管理器"""

    def __init__(self) -> None:
        self._tasks: Dict[str, dict] = {}
        self._active: Optional[str] = None  # 正在运行任务的 task_id
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 提交

    def submit_update(
        self,
        module_key: str,
        target_date: str = None,
        varieties: Optional[List[str]] = None,
    ) -> tuple[dict, bool]:
        """
        提交数据更新任务。

        Returns:
            (task_dict, already_running)：
            有任务在运行且并非空转时返回正在运行的任务并置 already_running=True。
        """
        cfg = MODULE_CONFIG.get(module_key, {})
        with self._lock:
            active_task = self._get_active_unlocked()
            if active_task is not None:
                return active_task, True
            task = self._new_task(
                kind="update",
                module_key=module_key,
                module_name=cfg.get("display_name", module_key),
                target_date=target_date or _now()[:10],
                variety_count=len(varieties) if varieties else None,
            )
            self._tasks[task["task_id"]] = task
            self._active = task["task_id"]
            self._prune_unlocked()
        threading.Thread(
            target=self._execute_update,
            args=(task["task_id"], module_key, target_date, varieties),
            daemon=True,
            name=f"data-update-{module_key}",
        ).start()
        return task, False

    def submit_bulk_update(
        self,
        module_keys: List[str],
        varieties: Optional[List[str]] = None,
        target_date: str = None,
        max_workers: int = 6,
        resume: bool = True,
        force: bool = False,
        retries: int = 2,
    ) -> tuple[dict, bool]:
        """【三评 G】全品种批量更新：按品种并发 + 断点续传 + 失败重试。

        与 submit_update 的差异：单品种 update_to_date 是串行遍历，59 品种要跑几小时；
        本方法按品种维度并发（默认 6 路），已成功的品种自动跳过，失败的集中重试。
        """
        with self._lock:
            active_task = self._get_active_unlocked()
            if active_task is not None:
                return active_task, True
            task = self._new_task(
                kind="bulk_update",
                module_key=",".join(module_keys),
                module_name="批量更新(" + ",".join(MODULE_CONFIG.get(m, {}).get("display_name", m)
                                                   for m in module_keys) + ")",
                target_date=target_date or _now()[:10],
                variety_count=len(varieties) if varieties else None,
            )
            task["modules"] = list(module_keys)
            task["max_workers"] = max_workers
            task["failed_symbols"] = []
            self._tasks[task["task_id"]] = task
            self._active = task["task_id"]
            self._prune_unlocked()
        threading.Thread(
            target=self._execute_bulk,
            args=(task["task_id"], module_keys, varieties, target_date,
                  max_workers, resume, force, retries),
            daemon=True,
            name="data-bulk-update",
        ).start()
        return task, False

    def submit_refresh_contracts(self) -> tuple[dict, bool]:
        """提交“刷新主力合约”任务。"""
        with self._lock:
            active_task = self._get_active_unlocked()
            if active_task is not None:
                return active_task, True
            task = self._new_task(
                kind="refresh_contracts",
                module_key="dominant_contracts",
                module_name="主力合约",
                target_date=_now()[:10],
                variety_count=None,
            )
            self._tasks[task["task_id"]] = task
            self._active = task["task_id"]
            self._prune_unlocked()
        threading.Thread(
            target=self._execute_refresh,
            args=(task["task_id"],),
            daemon=True,
            name="data-refresh-contracts",
        ).start()
        return task, False

    # ---------------------------------------------------------------- 查询

    def get(self, task_id: str) -> Optional[dict]:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def list(self, limit: int = 20) -> List[dict]:
        with self._lock:
            items = list(self._tasks.values())[-limit:]
            return [dict(t) for t in reversed(items)]

    # ---------------------------------------------------------------- 内部

    def _new_task(
        self,
        kind: str,
        module_key: str,
        module_name: str,
        target_date: str,
        variety_count: Optional[int],
    ) -> dict:
        return {
            "task_id": uuid.uuid4().hex[:12],
            "kind": kind,  # update | refresh_contracts
            "module_key": module_key,
            "module_name": module_name,
            "target_date": target_date,
            "variety_count": variety_count,
            "status": "running",
            "stage": "启动中…",
            "done": 0,
            "total": 0,
            "percent": None,  # None => 前端展示不定进度
            "current": "",
            "message": "",
            "details": "",
            "submitted_at": _now(),
            "finished_at": None,
        }

    def _get_active_unlocked(self) -> Optional[dict]:
        if self._active is None:
            return None
        task = self._tasks.get(self._active)
        if task is not None and task.get("status") == "running":
            return task
        return None

    def _prune_unlocked(self) -> None:
        """保留最近 MAX_KEEP_TASKS 条（正在运行的任务不受影响）。"""
        while len(self._tasks) > MAX_KEEP_TASKS:
            oldest_id = next(iter(self._tasks))
            if oldest_id == self._active:
                break
            self._tasks.pop(oldest_id, None)

    def _progress_fn(self, task: dict) -> Callable:
        def _cb(stage: str, done: int, total: int, current: str = "") -> None:
            task["stage"] = str(stage)
            task["done"] = int(done)
            task["total"] = int(total)
            task["current"] = str(current)
            task["percent"] = round(done / total * 100) if total else None

        return _cb

    def _execute_update(
        self,
        task_id: str,
        module_key: str,
        target_date: str,
        varieties: Optional[List[str]],
    ) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        try:
            task["stage"] = f"正在更新 {task['module_name']}…"
            result = data_manager_service.run_data_update(
                module_key,
                target_date=target_date,
                varieties=varieties,
                progress_cb=self._progress_fn(task),
            )
            task["status"] = getattr(result, "status", "success")
            task["message"] = getattr(result, "message", "")
            task["details"] = getattr(result, "details", "") or ""
            if task["status"] != "success":
                task["stage"] = "处理结束"
            logger.info(f"[task {task_id}] update {module_key} -> {task['status']}: {task['message']}")
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[task {task_id}] update {module_key} 异常")
            task["status"] = "failed"
            task["message"] = "数据更新异常"
            task["details"] = str(exc)[:1000]
            task["stage"] = "异常"
        finally:
            self._finalize(task_id, task)

    def _execute_bulk(
        self,
        task_id: str,
        module_keys: List[str],
        varieties: Optional[List[str]],
        target_date: str,
        max_workers: int,
        resume: bool,
        force: bool,
        retries: int,
    ) -> None:
        """批量更新执行体（在后台线程中运行）。"""
        task = self._tasks.get(task_id)
        if task is None:
            return
        cb = self._progress_fn(task)
        try:
            task["stage"] = "批量更新启动（并发 + 断点续传）…"
            result = bulk_update_service.run_modules(
                module_keys,
                symbols=varieties,
                target_date=target_date,
                max_workers=max_workers,
                resume=resume,
                force=force,
                retries=retries,
                progress_cb=cb,
            )
            failed = result.get("failed_symbols", [])
            task["failed_symbols"] = failed
            task["status"] = "success" if not failed else "partial"
            ok = sum(len(m.get("updated", [])) for m in result["modules"].values())
            skip = sum(len(m.get("skipped", [])) for m in result["modules"].values())
            task["message"] = f"更新 {ok} 个品种次，跳过 {skip} 个（已有数据）"
            task["details"] = (
                "失败品种: " + ", ".join(failed[:20]) + ("…" if len(failed) > 20 else "")
                if failed
                else "无失败品种"
            )
            logger.info(f"[task {task_id}] bulk update -> {task['status']}: {task['message']}")
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[task {task_id}] bulk update 异常")
            task["status"] = "failed"
            task["message"] = "批量更新异常"
            task["details"] = str(exc)[:1000]
            task["stage"] = "异常"
        finally:
            self._finalize(task_id, task)

    def _execute_refresh(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        try:
            task["stage"] = "正在刷新主力合约（生意社快照 + 新浪补种）…"
            contracts = commodity_service.refresh_dominant_contracts()
            task["status"] = "success"
            task["message"] = f"已刷新 {len(contracts)} 个品种的主力合约"
            symbols = sorted(contracts.keys())
            task["details"] = "覆盖品种: " + ", ".join(symbols[:15]) + ("…" if len(symbols) > 15 else "")
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[task {task_id}] refresh dominant contracts 异常")
            task["status"] = "failed"
            task["message"] = "刷新主力合约异常"
            task["details"] = str(exc)[:1000]
            task["stage"] = "异常"
        finally:
            self._finalize(task_id, task)

    def _finalize(self, task_id: str, task: dict) -> None:
        task["finished_at"] = _now()
        task["done"] = task.get("total") or 1
        task["total"] = task.get("total") or 1
        task["percent"] = 100
        task["stage"] = task["stage"] or "完成"
        with self._lock:
            if self._active == task_id:
                self._active = None


# 模块级单例：与 data_manager_service / commodity_service 对齐
data_task_service = DataTaskService()
