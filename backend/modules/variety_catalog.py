"""品种清单单一数据源

历史问题：各更新器各自维护一份写死的品种清单 / 交易所归属 / 中文名，
technical 甚至固化过一批 rb2410 这样的交割合约，导致：
  1. 新增或退市品种必须改 Python 代码；
  2. 各模块覆盖范围不一致（如持仓模块一度漏掉 9 个品种，前端却显示支持）；
  3. 清单外的品种被静默过滤，UI 报“更新成功”实际从未取数。

本模块统一读取 backend/config/commodities.yaml（用户可直接编辑）。
各更新器的内置字典仅作为“数据源接口参数名”的兜底，不再决定品种范围。
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Set

from loguru import logger


_CONFIG_CANDIDATES = (
    Path(__file__).resolve().parents[1] / "config" / "commodities.yaml",
    Path(__file__).resolve().parents[2] / "config" / "commodities.yaml",
)


@dataclass(frozen=True)
class Variety:
    """单个品种的配置项"""
    symbol: str
    name: str = ""
    exchange: str = ""
    category: str = ""


def _config_path() -> Optional[Path]:
    for path in _CONFIG_CANDIDATES:
        if path.exists():
            return path
    logger.warning("未找到 config/commodities.yaml，品种清单将依赖各模块内置兜底")
    return None


@lru_cache(maxsize=1)
def load_commodities() -> tuple:
    """加载全部品种配置（带缓存）；读取失败返回空元组，由调用方降级。"""
    path = _config_path()
    if not path:
        return ()
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning(f"读取品种配置失败，回退内置清单 - {str(e)[:60]}")
        return ()

    items: List[Variety] = []
    seen: Set[str] = set()
    for row in data.get("commodities", []) or []:
        symbol = str(row.get("symbol", "")).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        items.append(Variety(
            symbol=symbol,
            name=str(row.get("name", "")).strip(),
            exchange=str(row.get("exchange", "")).strip().upper(),
            category=str(row.get("category", "")).strip(),
        ))
    return tuple(items)


def reload() -> None:
    """配置热重载（用户编辑 commodities.yaml 后调用）"""
    load_commodities.cache_clear()


def symbols() -> List[str]:
    """全部品种代码；为空表示配置不可用，调用方应回退内置清单"""
    return [v.symbol for v in load_commodities()]


def name_map() -> Dict[str, str]:
    """品种代码 -> 中文名"""
    return {v.symbol: v.name for v in load_commodities() if v.name}


def exchange_map() -> Dict[str, str]:
    """品种代码 -> 交易所代码（SHFE/DCE/CZCE/GFEX/INE...）"""
    return {v.symbol: v.exchange for v in load_commodities() if v.exchange}


def symbols_of_exchange(exchange: str) -> Set[str]:
    """指定交易所下的品种集合"""
    target = str(exchange).strip().upper()
    return {v.symbol for v in load_commodities() if v.exchange == target}
