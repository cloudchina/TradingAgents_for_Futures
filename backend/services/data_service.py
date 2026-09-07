"""数据管理服务 - 管理本地数据状态和更新"""
import os
import sys
import csv
import importlib
import inspect
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from loguru import logger

from core.settings import settings
from models.data import ModuleStatus, DataStatusResponse, UpdateResult
from services.commodity_service import commodity_service

# 各模块 CSV 日期列候选名（按优先级）。历史上 technical_analysis 模块部分文件
# 首列是品种代码(symbol)、第二列才是 date(YYYYMMDD)，因此不能简单假定“第一列=日期”。
_DATE_COL_NAMES = ("时间", "日期", "date", "trade_date")


def _detect_date_column(header: List[str]) -> Optional[int]:
    """从 CSV 表头中找出日期列下标；找不到返回 None。"""
    if not header:
        return None
    for name in _DATE_COL_NAMES:
        if name in header:
            return header.index(name)
    for i, name in enumerate(header):
        low = str(name).lower()
        if "date" in low or "时间" in str(name) or "日期" in str(name):
            return i
    return None


def _normalize_date_cell(value: Any) -> str:
    """把日期单元格统一成 YYYY-MM-DD 展示格式（兼容 YYYYMMDD）。"""
    s = str(value).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


# 模块配置映射
MODULE_CONFIG = {
    "inventory": {
        "display_name": "库存数据",
        "subdir": "inventory",
        "data_file": "inventory.csv",
        "updater_class": "InventoryDataUpdater",
        "updater_module": "modules.inventory_updater",
    },
    "positioning": {
        "display_name": "持仓席位",
        "subdir": "positioning",
        "data_file": "long_position_ranking.csv",
        "updater_class": "PositioningDataUpdater",
        "updater_module": "modules.positioning_updater",
    },
    "term_structure": {
        "display_name": "期限结构",
        "subdir": "term_structure",
        "data_file": "term_structure.csv",
        "updater_class": "TermStructureUpdater",
        "updater_module": "modules.term_structure_updater",
    },
    "technical_analysis": {
        "display_name": "技术面指标",
        "subdir": "technical_analysis",
        "data_file": "ohlc_data.csv",
        "updater_class": "TechnicalDataUpdater",
        "updater_module": "modules.technical_updater",
    },
    "basis": {
        "display_name": "基差数据",
        "subdir": "basis",
        "data_file": "basis_data.csv",
        "updater_class": "BasisDataUpdater",
        "updater_module": "modules.basis_updater",
    },
    "receipt": {
        "display_name": "仓单数据",
        "subdir": "receipt",
        "data_file": "receipt.csv",
        "updater_class": "ReceiptDataUpdater",
        "updater_module": "modules.receipt_updater",
    },
}


class DataManagerService:
    """数据管理服务"""

    def __init__(self):
        self.data_root = Path(settings.DATA_ROOT_DIR)
        self.project_root = Path(__file__).resolve().parent.parent
        # 启动时确保目录存在
        self._ensure_directories()

    def _ensure_directories(self):
        """确保所有模块和品种的数据目录存在"""
        try:
            commodity_service.ensure_data_directories(self.data_root)
        except Exception as e:
            logger.warning(f"创建数据目录时出错: {e}")

    def _module_path(self, config: Dict) -> Path:
        return self.data_root / config["subdir"]

    def get_data_status(self) -> DataStatusResponse:
        """获取所有模块的数据状态"""
        modules = {}
        all_commodities = set()

        for module_key, config in MODULE_CONFIG.items():
            status = self._get_module_status(module_key, config)
            modules[module_key] = status
            if status.status in ("success", "empty"):
                module_path = self._module_path(config)
                if module_path.exists():
                    all_commodities.update(
                        d.name for d in module_path.iterdir() if d.is_dir()
                    )

        # 合并配置文件中的品种
        configured_symbols = set(commodity_service.get_symbols())
        all_commodities.update(configured_symbols)

        summary = {
            "total_modules": len(modules),
            "success_modules": sum(1 for m in modules.values() if m.status == "success"),
            "common_commodities": sorted(list(all_commodities)),
        }

        return DataStatusResponse(modules=modules, summary=summary)

    def _get_module_status(self, module_key: str, config: Dict) -> ModuleStatus:
        status = ModuleStatus()
        module_path = self._module_path(config)

        if not module_path.exists():
            status.status = "error"
            status.error = f"数据目录不存在: {module_path}"
            status.path = str(module_path)
            return status

        try:
            commodities = [d for d in module_path.iterdir() if d.is_dir()]
            status.commodities_count = len(commodities)

            total_records = 0
            last_update = None
            for commodity_dir in commodities:
                data_file = commodity_dir / config["data_file"]
                if data_file.exists():
                    try:
                        with open(data_file, "r", encoding="utf-8-sig", errors="ignore") as f:
                            lines = sum(1 for _ in f) - 1
                            total_records += max(0, lines)
                    except Exception:
                        pass
                    mtime = datetime.fromtimestamp(data_file.stat().st_mtime)
                    if last_update is None or mtime > last_update:
                        last_update = mtime

            status.total_records = total_records
            status.last_update = last_update.strftime("%Y-%m-%d %H:%M") if last_update else "未知"
            status.path = str(module_path)
            status.status = "success" if len(commodities) > 0 else "empty"

        except Exception as e:
            status.status = "error"
            status.error = str(e)
            logger.error(f"获取模块 {module_key} 状态失败: {e}")

        return status

    def get_module_varieties(self, module_key: str) -> List[Dict[str, Any]]:
        config = MODULE_CONFIG.get(module_key)
        if not config:
            return []

        module_path = self._module_path(config)
        if not module_path.exists():
            return []

        varieties = []
        for commodity_dir in sorted(module_path.iterdir()):
            if not commodity_dir.is_dir():
                continue
            data_file = commodity_dir / config["data_file"]
            info = {"variety": commodity_dir.name}
            if data_file.exists():
                try:
                    with open(data_file, "r", encoding="utf-8-sig", errors="ignore", newline="") as f:
                        reader = csv.reader(f)
                        try:
                            header = next(reader)
                        except StopIteration:
                            header = []
                        rows = list(reader)
                        info["record_count"] = len(rows)
                        if rows and header:
                            date_idx = _detect_date_column(header)
                            if date_idx is not None:
                                first = rows[0][date_idx].strip() if len(rows[0]) > date_idx else ""
                                last = rows[-1][date_idx].strip() if len(rows[-1]) > date_idx else ""
                                if first:
                                    info["start_date"] = _normalize_date_cell(first)
                                if last:
                                    info["end_date"] = _normalize_date_cell(last)
                except Exception:
                    info["record_count"] = 0
            varieties.append(info)

        return varieties

    def check_commodity_data(self, commodity: str) -> Dict[str, bool]:
        result = {}
        for module_key, config in MODULE_CONFIG.items():
            data_path = self._module_path(config) / commodity / config["data_file"]
            result[module_key] = data_path.exists() and data_path.stat().st_size > 0
        return result

    def get_commodities(self) -> List[Dict[str, Any]]:
        """获取所有品种配置（含主力合约和数据状态）"""
        commodities = commodity_service.get_all_commodities()
        for c in commodities:
            c["data_status"] = self.check_commodity_data(c["symbol"])
        return commodities

    def run_data_update(
        self,
        module_key: str,
        target_date: str = None,
        varieties: List[str] = None,
    ) -> UpdateResult:
        """运行数据更新（非交互式，直接调用 Updater 类）"""
        config = MODULE_CONFIG.get(module_key)
        if not config:
            return UpdateResult(status="error", message=f"未知模块: {module_key}")

        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")

        # 如果未指定品种，使用配置文件中的全部品种
        if varieties is None:
            varieties = commodity_service.get_symbols()

        try:
            logger.info(f"启动数据更新: {config['display_name']} (date={target_date}, varieties={len(varieties)})")

            # 确保目录存在
            self._ensure_directories()

            sys.path.insert(0, str(self.project_root))

            updater_module = importlib.import_module(config["updater_module"])
            updater_class = getattr(updater_module, config["updater_class"])

            db_path = str(self._module_path(config))
            updater = updater_class(database_path=db_path)

            if not hasattr(updater, "update_to_date"):
                return UpdateResult(
                    status="error",
                    message=f"{config['display_name']}更新器缺少 update_to_date 方法",
                )

            # 根据签名构造参数
            sig = inspect.signature(updater.update_to_date)
            params = list(sig.parameters.keys())

            if len(params) >= 3 and params[1] == "update_days":
                result = updater.update_to_date(target_date, None, varieties)
            elif len(params) >= 3 and params[1] == "start_date_str":
                result = updater.update_to_date(target_date, None, varieties)
            else:
                result = updater.update_to_date(target_date, varieties)

            if isinstance(result, dict):
                updated = len(result.get("updated_varieties", []))
                failed = len(result.get("failed_varieties", []))
                skipped = len(result.get("skipped_varieties", []))
                total_new = result.get("total_new_records", 0)
                errors = result.get("error_messages", [])

                if failed > 0 or errors:
                    return UpdateResult(
                        status="error",
                        message=f"{config['display_name']}更新完成（有失败）",
                        details=f"成功: {updated}, 失败: {failed}, 跳过: {skipped}, 新增记录: {total_new}"
                                + (f", 错误: {'; '.join(errors[:3])}" if errors else ""),
                    )
                else:
                    return UpdateResult(
                        status="success",
                        message=f"{config['display_name']}更新成功",
                        details=f"更新品种: {updated}, 跳过: {skipped}, 新增记录: {total_new}, 目标日期: {target_date}",
                    )
            else:
                return UpdateResult(
                    status="success",
                    message=f"{config['display_name']}更新完成",
                    details=f"目标日期: {target_date}",
                )

        except ImportError as e:
            logger.error(f"导入更新模块失败: {e}")
            return UpdateResult(
                status="error",
                message=f"更新模块导入失败: {e}",
                details="请确认 backend/modules/ 目录存在且 akshare 已安装",
            )
        except Exception as e:
            logger.error(f"数据更新失败 {module_key}: {e}")
            return UpdateResult(
                status="error",
                message=f"{config['display_name']}更新失败",
                details=str(e)[:500],
            )


# 全局单例
data_manager_service = DataManagerService()
