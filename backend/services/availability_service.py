"""数据可用性服务 - 阶段0 骨架。

对应 memory-system-plan.md 第四章阶段 0：
- 扫描所有品种的 ohlc_data.csv 行数、起止日期、是否有主力合约历史
- 写入 <MEMORY_DIR>/data_availability.json
- 供 GET /api/memory/availability 查询；阶段 2/4 与第七章验收以此为基线
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from core.settings import get_data_path, get_memory_path
from services.memory_service import MIN_SAMPLE_60D, MIN_SAMPLE_120D


class AvailabilityService:
    """数据可用性清单服务。

    数据源路径：settings.DATA_ROOT_DIR / technical_analysis / <SYMBOL> / ohlc_data.csv
    （与 [data_service.py#L66-L72] MODULE_CONFIG 路径一致）
    """

    def __init__(self) -> None:
        self._availability_path = get_memory_path("data_availability.json")

    # ─── 扫描 ───

    def scan_all(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        """扫描全部品种；symbols 为空时调用 commodity_service.get_symbols()。

        返回结构：
            {
              "scanned_at": ISO,
              "symbols": [
                {"symbol":"RB","rows":1234,"start_date":"...","end_date":"...",
                 "main_contract_rows": 56, "eligible_60d": true, "eligible_120d": false,
                 "csv_present": true, "main_contract_present": false},
                ...
              ],
              "failed_symbols": [...]
            }
        """
        if symbols is None:
            try:
                from services.commodity_service import commodity_service
                symbols = commodity_service.get_symbols()
            except Exception as e:
                logger.warning(f"availability: 取品种清单失败 {e}，symbols=[]")
                symbols = []

        items: List[Dict[str, Any]] = []
        failed: List[str] = []
        for s in symbols:
            try:
                items.append(self._scan_one(s))
            except Exception as e:
                logger.debug(f"availability: 扫描 {s} 失败 {e}")
                failed.append(s)

        result = {
            "scanned_at": datetime.now().isoformat(),
            "symbols": items,
            "failed_symbols": failed,
            "total": len(items),
            "eligible_60d": sum(1 for x in items if x.get("eligible_60d")),
            "eligible_120d": sum(1 for x in items if x.get("eligible_120d")),
        }
        return result

    def _scan_one(self, symbol: str) -> Dict[str, Any]:
        """扫描单个品种。"""
        csv_path = get_data_path("technical_analysis", symbol, "ohlc_data.csv")
        record: Dict[str, Any] = {
            "symbol": symbol,
            "csv_present": False,
            "rows": 0,
            "start_date": None,
            "end_date": None,
            "main_contract_present": False,
            "main_contract_rows": 0,
            "eligible_60d": False,
            "eligible_120d": False,
        }

        if csv_path.exists():
            record["csv_present"] = True
            try:
                df = pd.read_csv(csv_path, encoding="utf-8")
                record["rows"] = int(len(df))
                # 复用 _pick_date_col 逻辑（analysis_service 同款），保持口径一致
                date_col = self._pick_date_col(df)
                if date_col is not None and len(df):
                    series = df[date_col].astype(str)
                    record["start_date"] = str(series.iloc[0])
                    record["end_date"] = str(series.iloc[-1])
                record["eligible_60d"] = record["rows"] >= MIN_SAMPLE_60D
                record["eligible_120d"] = record["rows"] >= MIN_SAMPLE_120D
            except Exception as e:
                logger.debug(f"availability: 读 {csv_path} 失败 {e}")
                record["csv_present"] = False

        # 主力合约历史库：<DATA_ROOT_DIR>/main_contract/<SYMBOL>/dominant_contract.csv
        mc_path = get_data_path("main_contract", symbol, "dominant_contract.csv")
        if mc_path.exists():
            record["main_contract_present"] = True
            try:
                mc_df = pd.read_csv(mc_path, encoding="utf-8")
                record["main_contract_rows"] = int(len(mc_df))
            except Exception:
                record["main_contract_rows"] = 0

        return record

    @staticmethod
    def _pick_date_col(df: pd.DataFrame) -> Optional[str]:
        """与 analysis_service._pick_date_col 同款逻辑，保持口径一致。"""
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

    # ─── 落盘 + 查询 ───

    def save_scan(self, result: Dict[str, Any]) -> Path:
        """写入 <MEMORY_DIR>/data_availability.json。"""
        self._availability_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._availability_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(
            f"availability: 已写入 {self._availability_path} "
            f"({result.get('total', 0)} 品种)"
        )
        return self._availability_path

    def load_scan(self) -> Optional[Dict[str, Any]]:
        """读取已落盘的清单；无文件返回 None。"""
        if not self._availability_path.exists():
            return None
        try:
            with open(self._availability_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"availability: 读 {self._availability_path} 失败 {e}")
            return None

    def get_or_scan(self, force_refresh: bool = False) -> Dict[str, Any]:
        """查询接口：有缓存就返回缓存，否则现场扫一次并落盘。"""
        if not force_refresh:
            cached = self.load_scan()
            if cached is not None:
                return cached
        result = self.scan_all()
        try:
            self.save_scan(result)
        except Exception as e:
            logger.warning(f"availability: 落盘失败 {e}")
        return result


# ─────────────────────────────────────────────────────────────
# 单例
# ─────────────────────────────────────────────────────────────

availability_service = AvailabilityService()
