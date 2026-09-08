"""品种配置服务 - 管理品种列表和主力合约映射"""
import os
import yaml
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from loguru import logger


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "commodities.yaml"
DOMINANT_CACHE_PATH = Path(__file__).resolve().parent.parent / "config" / "dominant_contracts.yaml"


class CommodityService:
    """品种配置服务"""

    def __init__(self):
        self._config = None
        self._dominant_cache = None

    def _load_config(self) -> Dict[str, Any]:
        """加载品种配置"""
        if self._config is None:
            if not CONFIG_PATH.exists():
                logger.error(f"品种配置文件不存在: {CONFIG_PATH}")
                self._config = {"commodities": []}
            else:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    self._config = yaml.safe_load(f) or {"commodities": []}
        return self._config

    def _load_dominant_cache(self) -> Dict[str, str]:
        """加载主力合约缓存"""
        if self._dominant_cache is None:
            self._dominant_cache = {}
            if DOMINANT_CACHE_PATH.exists():
                try:
                    with open(DOMINANT_CACHE_PATH, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                        self._dominant_cache = data.get("contracts", {})
                except Exception as e:
                    logger.warning(f"加载主力合约缓存失败: {e}")
        return self._dominant_cache

    def _save_dominant_cache(self, contracts: Dict[str, str]):
        """保存主力合约缓存"""
        self._dominant_cache = contracts
        try:
            DOMINANT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(DOMINANT_CACHE_PATH, "w", encoding="utf-8") as f:
                yaml.dump(
                    {
                        "updated_at": datetime.now().isoformat(),
                        "contracts": contracts,
                    },
                    f,
                    allow_unicode=True,
                    default_flow_style=False,
                )
            logger.info(f"主力合约缓存已保存: {len(contracts)} 个品种")
        except Exception as e:
            logger.error(f"保存主力合约缓存失败: {e}")

    def get_all_commodities(self) -> List[Dict[str, Any]]:
        """获取所有品种配置"""
        config = self._load_config()
        commodities = config.get("commodities", [])
        # 补充主力合约信息：yaml 快照优先，缺失的品种回退本地主力合约库最新值
        dominant = dict(self._load_dominant_cache())
        missing = [c["symbol"] for c in commodities if not dominant.get(c["symbol"])]
        if missing:
            try:
                dominant.update(self._latest_from_main_store(missing))
            except Exception as e:
                logger.warning(f"从本地主力合约库补充主力合约失败: {e}")
        for c in commodities:
            c["dominant_contract"] = dominant.get(c["symbol"], "")
        return commodities

    # ---------- 本地主力合约库（真实具体合约，如 RB2510） ----------

    @staticmethod
    def _main_contract_store():
        """主力合约本地库：<backend>/data/qihuo/database/main_contract（目录不存在时自动创建）"""
        from modules.main_contract_sync import MainContractSync
        root = Path(__file__).resolve().parent.parent / "data" / "qihuo" / "database"
        return MainContractSync(data_root=root)

    def _latest_from_main_store(self, symbols: List[str]) -> Dict[str, str]:
        """
        从本地主力合约库读取各品种最近一个已知交易日的主力合约。

        主力合约库里保存的是“真实具体合约”（如 RB2510），与主连/连续合约(RB0)严格区分。
        """
        sync = self._main_contract_store()
        result: Dict[str, str] = {}
        for s in symbols:
            try:
                df = sync._read_local(s)
                if df is None or df.empty or "date" not in df.columns or "dominant_contract" not in df.columns:
                    continue
                latest = df.sort_values("date").iloc[-1]
                contract = str(latest.get("dominant_contract", "") or "").strip()
                if contract and contract.lower() != "nan":
                    result[s.upper()] = contract
            except Exception as e:
                logger.debug(f"读取 {s} 本地主力合约失败: {e}")
        return result

    def get_commodity(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取单个品种配置"""
        for c in self.get_all_commodities():
            if c["symbol"] == symbol.upper():
                return c
        return None

    def get_symbols(self) -> List[str]:
        """获取所有品种代码"""
        return [c["symbol"] for c in self._load_config().get("commodities", [])]

    def get_dominant_contract(self, symbol: str) -> str:
        """获取品种的主力合约代码（真实具体合约；缓存缺失时回退本地主力合约库）"""
        symbol = symbol.upper()
        cache = self._load_dominant_cache()
        if cache.get(symbol):
            return cache[symbol]
        try:
            return self._latest_from_main_store([symbol]).get(symbol, "")
        except Exception:
            return ""

    def get_all_dominant_contracts(self) -> Dict[str, str]:
        """获取所有主力合约映射（真实具体合约）"""
        return dict(self._load_dominant_cache())

    def refresh_dominant_contracts(self) -> Dict[str, str]:
        """
        刷新“当前主力合约”（真实具体合约，如 RB2510）。

        与“连续合约/主连”(RB0) 严格区分——本方法只确认并保存真实主力合约：
        1. 用当日（最近一个能取到数据的交易日）行情快照 futures_spot_price（生意社/100ppi）
           获取全部品种真实主力合约；
        2. 生意社未收录的品种（如 AO 氧化铝、SC 原油、B 豆二等）由新浪财经
           futures_zh_realtime 补种：取该品种全部挂牌合约中“持仓量最大的真实合约”为主力；
        3. 结果同步写入本地主力合约库 <main_contract>/<SYMBOL>/dominant_contract.csv（目录自动创建），
           供持仓席位/换月复权等模块复用；
        4. 联网失败则回退本地主力合约库各品种最新记录；
        5. 仍未取得则保留旧 yaml 缓存。
        """
        configured = set(self.get_symbols())
        try:
            import akshare as ak
            from modules.main_contract_sync import normalize_contract
        except ImportError:
            logger.error("akshare 未安装，无法获取主力合约")
            return self._load_dominant_cache()

        online: Dict[str, str] = {}
        snapshot_date = None
        # 最近 7 个自然日内取第一个能返回数据的交易日（周末/节假日日期会返回空）
        for back in range(7):
            date_str = (datetime.now() - timedelta(days=back)).strftime("%Y%m%d")
            try:
                df = ak.futures_spot_price(date_str)
            except Exception as e:
                logger.warning(f"获取 {date_str} 行情快照失败: {e}")
                continue
            if df is None or df.empty:
                continue
            tmp: Dict[str, str] = {}
            for _, row in df.iterrows():
                symbol = str(row.get("symbol", "") or "").strip().upper()
                if symbol not in configured:
                    continue
                contract = normalize_contract(row.get("dominant_contract"))
                if contract:
                    tmp[symbol] = contract
            if tmp:
                online, snapshot_date = tmp, date_str
                break

        # 新浪财经补种：生意社未收录品种按“持仓量最大真实合约”确认主力
        missing_symbols = [s for s in configured if s not in online]
        if missing_symbols:
            sina_contracts = self._fetch_missing_from_sina(missing_symbols)
            for symbol, contract in sina_contracts.items():
                if contract and symbol not in online:
                    online[symbol] = contract

        if online and snapshot_date:
            # 快照写回本地主力合约库（自动创建目录），供其它模块复用
            sync = self._main_contract_store()
            saved = 0
            for symbol, contract in online.items():
                try:
                    saved += sync._save(symbol, [(snapshot_date, contract)])
                except Exception:
                    pass
            logger.info(f"从 {snapshot_date} 行情快照确认 {len(online)} 个品种的真实主力合约"
                        f"（含新浪补种 {len(sina_contracts) if missing_symbols else 0} 个，"
                        f"已写入本地主力合约库 {saved} 条）")
            contracts = online
        elif online:
            # 生意社连续失败、仅新浪可用：只更新缓存，不写本地库（无法确定对应交易日）
            logger.warning("生意社行情快照不可用，仅用新浪实时行情更新主力合约缓存")
            contracts = online
        else:
            logger.warning("行情快照获取失败，回退本地主力合约库最新记录")
            contracts = self._latest_from_main_store(list(configured))
            if not contracts:
                contracts = dict(self._load_dominant_cache())

        if contracts:
            self._save_dominant_cache(contracts)
        else:
            logger.error("未能确认任何品种的主力合约")
        return contracts

    # ---------- 新浪财经补充源（生意社/100ppi 未收录的品种） ----------

    # 新浪期货行情列表的品种名与 commodities.yaml 的 name 不一致时的修正表
    _SINA_CN_NAME_FIX = {"AP": "鲜苹果"}

    def _fetch_missing_from_sina(self, symbols: List[str]) -> Dict[str, str]:
        """
        对生意社(100ppi)未收录的品种，用新浪财经实时行情确认“当前主力合约”。

        新浪 futures_zh_realtime 返回某品种全部挂牌合约并按持仓量(position)降序排列；
        其中 symbol 形如 XX0 的行是主连/连续行情，需剔除；随后从真实合约中取持仓量
        最大的一个作为主力（真实具体合约，如 AO2601 / SC2610）。

        Args:
            symbols: 待补种品种代码列表（大写）

        Returns:
            成功确认的 {symbol: contract}；无法确认的品种不出现在结果中。
        """
        if not symbols:
            return {}
        try:
            import akshare as ak
            from modules.main_contract_sync import normalize_contract
        except ImportError:
            return {}

        name_map = {
            c["symbol"]: c.get("name", "")
            for c in self._load_config().get("commodities", [])
        }
        exchange_map = {
            c["symbol"]: c.get("exchange", "")
            for c in self._load_config().get("commodities", [])
        }

        result: Dict[str, str] = {}
        for symbol in symbols:
            name = self._SINA_CN_NAME_FIX.get(symbol) or name_map.get(symbol)
            if not name:
                continue
            try:
                df = ak.futures_zh_realtime(symbol=name)
            except Exception as e:
                logger.warning(f"新浪获取 {symbol}({name}) 行情失败: {e}")
                continue
            if df is None or df.empty:
                continue
            candidates = []
            for _, row in df.iterrows():
                code = str(row.get("symbol", "") or "").strip()
                digits = "".join(ch for ch in code if ch.isdigit())
                if digits == "0":
                    continue  # XX0 主连/连续行情行，非真实合约
                pos = pd.to_numeric(row.get("position"), errors="coerce")
                if pd.isna(pos):
                    continue
                candidates.append((float(pos), code))
            if not candidates:
                continue
            candidates.sort(key=lambda x: x[0], reverse=True)
            contract = normalize_contract(candidates[0][1])
            if not contract:
                continue
            # 与生意社快照的落盘风格保持一致：上期所/大商所小写，其余大写
            if exchange_map.get(symbol) in ("SHFE", "DCE"):
                contract = contract.lower()
            result[symbol] = contract

        if result:
            logger.info(f"新浪实时行情确认 {len(result)} 个品种的主力合约: {result}")
        return result

    def ensure_data_directories(self, base_dir: Path, modules: List[str] = None):
        """确保所有品种的数据目录存在"""
        if modules is None:
            modules = ["inventory", "positioning", "term_structure",
                       "technical_analysis", "basis", "receipt", "main_contract"]

        symbols = self.get_symbols()
        created = 0
        for module in modules:
            module_dir = base_dir / module
            for symbol in symbols:
                symbol_dir = module_dir / symbol
                if not symbol_dir.exists():
                    symbol_dir.mkdir(parents=True, exist_ok=True)
                    created += 1
        if created:
            logger.info(f"已创建 {created} 个品种数据目录")
        return created


# 全局单例
commodity_service = CommodityService()
