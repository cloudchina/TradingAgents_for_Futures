"""缓存服务 - 管理分析结果的本地缓存"""
import pickle
import os
import json
import time
import tempfile
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List, Any
from loguru import logger

from core.settings import settings


CACHE_VERSION = 1
MAX_CACHE_SIZE_BYTES = 100 * 1024 * 1024  # 100MB


class CacheService:
    """分析结果缓存服务"""

    def __init__(self):
        self.cache_dir = Path(settings.CACHE_DIR)
        self._lock_registry: Dict[str, threading.Lock] = {}
        self._lock_registry_lock = threading.Lock()
        self._ensure_cache_dir()

    def _ensure_cache_dir(self):
        """确保缓存目录存在"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_lock(self, commodity: str, analysis_date: str) -> threading.Lock:
        """获取品种级别的线程锁"""
        key = f"{commodity}_{analysis_date}"
        with self._lock_registry_lock:
            if key not in self._lock_registry:
                self._lock_registry[key] = threading.Lock()
            return self._lock_registry[key]

    def _cache_path(self, commodity: str, analysis_date: str) -> Path:
        return self.cache_dir / f"{commodity}_{analysis_date}.pkl"

    def _meta_path(self, commodity: str, analysis_date: str) -> Path:
        return self.cache_dir / f"{commodity}_{analysis_date}.meta.json"

    def save_cache(self, commodity: str, analysis_date: str, result: Dict[str, Any]) -> bool:
        """保存分析结果到缓存"""
        lock = self._get_lock(commodity, analysis_date)
        with lock:
            try:
                self._ensure_cache_dir()
                cache_path = self._cache_path(commodity, analysis_date)
                meta_path = self._meta_path(commodity, analysis_date)

                # 原子写入
                data = {
                    "version": CACHE_VERSION,
                    "commodity": commodity,
                    "analysis_date": analysis_date,
                    "result": result,
                    "saved_at": datetime.now().isoformat(),
                }

                # 写入临时文件再rename
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=str(self.cache_dir), suffix=".tmp", delete=False
                ) as tmp:
                    pickle.dump(data, tmp)
                    tmp_path = tmp.name

                os.replace(tmp_path, cache_path)

                # 写入元数据
                meta = {
                    "commodity": commodity,
                    "analysis_date": analysis_date,
                    "status": result.get("status", "completed"),
                    "created_at": datetime.now().isoformat(),
                    "file_size": cache_path.stat().st_size,
                }
                with open(meta_path, "w") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)

                logger.info(f"缓存已保存: {commodity}_{analysis_date}")
                return True

            except Exception as e:
                logger.error(f"保存缓存失败 {commodity}_{analysis_date}: {e}")
                return False

    def load_cache(self, commodity: str, analysis_date: str) -> Optional[Dict[str, Any]]:
        """加载缓存"""
        try:
            cache_path = self._cache_path(commodity, analysis_date)
            if not cache_path.exists():
                return None

            with open(cache_path, "rb") as f:
                data = pickle.load(f)

            if data.get("version") != CACHE_VERSION:
                logger.warning(f"缓存版本不匹配: {commodity}_{analysis_date}")
                return None

            return data.get("result")

        except Exception as e:
            logger.error(f"加载缓存失败 {commodity}_{analysis_date}: {e}")
            return None

    def load_latest_cache(self, commodity: str) -> Optional[Dict[str, Any]]:
        """加载某品种“最近一个日期”的可用缓存（结果接口/Word 报告的兜底逻辑）。

        当内存中无当日结果、且今日无缓存时，回退到最近一次分析缓存，
        保证重启后端后仍能查看历史结果、导出历史 Word 报告。
        """
        try:
            if not self.cache_dir.exists():
                return None
            candidates = []
            for meta_file in self.cache_dir.glob(f"{commodity}_*.meta.json"):
                try:
                    with open(meta_file, "r") as f:
                        meta = json.load(f)
                    candidates.append(meta)
                except Exception:
                    continue
            if not candidates:
                return None
            candidates.sort(key=lambda m: str(m.get("analysis_date", "")), reverse=True)
            for meta in candidates:
                result = self.load_cache(commodity, meta.get("analysis_date", ""))
                if result is not None:
                    return result
            return None
        except Exception as e:
            logger.error(f"加载最近缓存失败 {commodity}: {e}")
            return None

    def list_cached_results(self) -> List[Dict[str, Any]]:
        """列出所有缓存元数据"""
        results = []
        try:
            if not self.cache_dir.exists():
                return results

            for meta_file in sorted(self.cache_dir.glob("*.meta.json")):
                try:
                    with open(meta_file, "r") as f:
                        meta = json.load(f)
                    results.append(meta)
                except Exception:
                    continue

        except Exception as e:
            logger.error(f"列出缓存失败: {e}")

        return results

    def delete_cache(self, commodity: str, analysis_date: str) -> bool:
        """删除缓存"""
        try:
            cache_path = self._cache_path(commodity, analysis_date)
            meta_path = self._meta_path(commodity, analysis_date)

            if cache_path.exists():
                cache_path.unlink()
            if meta_path.exists():
                meta_path.unlink()

            return True
        except Exception as e:
            logger.error(f"删除缓存失败: {e}")
            return False

    def get_cache_count(self) -> int:
        """获取缓存数量"""
        try:
            return len(list(self.cache_dir.glob("*.pkl")))
        except Exception:
            return 0


# 全局单例
cache_service = CacheService()
