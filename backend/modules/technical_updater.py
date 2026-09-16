#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技术分析数据更新器
基于智能技术分析数据更新器，支持OHLC数据获取和技术指标计算
"""

import akshare as ak
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import time
import random
import json
import warnings
from typing import Dict, List, Optional, Tuple
from loguru import logger

from modules.progress import ProgressReporter
from modules import variety_catalog
# 注：全部技术指标（MA/EMA/ATR/RSI/MACD/布林带/KDJ/CCI/OBV 等）均使用 pandas/numpy 实现，
# 不依赖 TA-Lib（原提示“talib 未安装指标将跳过”是历史误报，已移除）
warnings.filterwarnings('ignore')

# 品种清单兜底：仅在主力合约库为空且联网快照也失败时使用。
# 注意：品种与主力合约本身由 MainContractSync 每日同步动态维护（data/qihuo/database/main_contract），
# 这里刻意不再写死任何合约代码（历史上此处固化了一批 2024 年交割合约，早已失效）。
FALLBACK_VARIETIES = [
    'A', 'AG', 'AL', 'AO', 'AP', 'AU', 'B', 'BC', 'BR', 'BU', 'C', 'CF', 'CJ', 'CS', 'CU', 'EC',
    'EB', 'EG', 'FG', 'FU', 'HC', 'I', 'J', 'JD', 'JM', 'L', 'LC', 'LH', 'LU',
    'M', 'MA', 'NI', 'NR', 'OI', 'P', 'PB', 'PF', 'PG', 'PK', 'PP', 'PR', 'PS', 'PX',
    'RB', 'RM', 'RU', 'SA', 'SC', 'SF', 'SH', 'SI', 'SM', 'SN', 'SP', 'SR', 'SS',
    'TA', 'UR', 'V', 'Y', 'ZN',
]

class TechnicalDataUpdater(ProgressReporter):
    """技术分析数据更新器"""
    
    def __init__(self, database_path: str = "qihuo/database/technical_analysis"):
        """
        初始化技术分析数据更新器
        
        Args:
            database_path: 数据库路径
        """
        self.base_dir = Path(database_path)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._variety_name_map: Optional[Dict[str, str]] = None  # 品种中文名缓存
        
        self.update_stats = {
            "start_time": None,
            "end_time": None,
            "target_date": None,
            "updated_varieties": [],
            "failed_varieties": [],
            "skipped_varieties": [],
            "new_varieties": [],
            "total_new_records": 0,
            "error_messages": []
        }
    
    # ---------- 动态品种清单 / 中文名（不再写死合约） ----------

    def _load_variety_name_map(self) -> Dict[str, str]:
        """品种代码 -> 中文名，来自 commodities.yaml（用户可自行增删品种）。"""
        if self._variety_name_map is not None:
            return self._variety_name_map
        # 唯一数据源：commodities.yaml（经由 modules/variety_catalog）
        self._variety_name_map = dict(variety_catalog.name_map())
        if not self._variety_name_map:
            logger.warning("品种中文名配置不可用，回退内置映射")
        return self._variety_name_map

    def _dynamic_varieties(self, target_date: datetime) -> List[str]:
        """动态品种清单：主力合约库（每日同步维护）优先，其次联网全市场主力合约快照。"""
        try:
            from modules.main_contract_sync import MainContractSync
            symbols = MainContractSync(data_root=self.base_dir.parent).list_known_varieties(target_date)
            if symbols:
                logger.debug(f"品种清单来自主力合约库: {len(symbols)} 个")
                return symbols
        except Exception as e:
            logger.warning(f"动态获取品种清单失败 - {str(e)[:60]}")

        cfg_symbols = variety_catalog.symbols()
        if cfg_symbols:
            logger.info(f"品种清单来自 commodities.yaml: {len(cfg_symbols)} 个")
            return cfg_symbols

        logger.warning("主力合约库与配置均不可用，回退内置品种清单")
        return list(FALLBACK_VARIETIES)

    def get_existing_data_status(self) -> Tuple[List[str], Dict]:
        """
        获取现有数据状态
        
        Returns:
            varieties: 现有品种列表
            variety_info: 各品种详细信息
        """
        logger.info("检查现有技术分析数据状态...")
        
        varieties = []
        variety_info = {}
        
        if not self.base_dir.exists():
            return [], {}
        
        variety_folders = [d for d in self.base_dir.iterdir() if d.is_dir()]
        logger.info(f"发现 {len(variety_folders)} 个品种文件夹")
        
        for folder in variety_folders:
            variety = folder.name
            ohlc_file = folder / "ohlc_data.csv"
            
            if ohlc_file.exists():
                try:
                    df = pd.read_csv(ohlc_file, encoding='utf-8')
                    if len(df) > 0 and '时间' in df.columns:
                        df['时间'] = pd.to_datetime(df['时间'])
                        variety_latest = df['时间'].max()
                        variety_earliest = df['时间'].min()
                        record_count = len(df)
                        
                        variety_info[variety] = {
                            "earliest_date": variety_earliest,
                            "latest_date": variety_latest,
                            "record_count": record_count,
                            "ohlc_file": ohlc_file
                        }
                        
                        varieties.append(variety)
                        logger.info(f"{variety}: {record_count} 条记录 ({variety_earliest.strftime('%Y-%m-%d')} ~ {variety_latest.strftime('%Y-%m-%d')})")
                        
                except Exception as e:
                    logger.error(f"{variety}: 读取失败 - {str(e)[:50]}")
                    self.update_stats["error_messages"].append(f"{variety}: 数据读取失败 - {str(e)}")
        
        logger.info(f"总计: {len(varieties)} 个有效品种")
        return varieties, variety_info
    
    def fetch_ohlc_data(self, symbol: str, start_date: Optional[datetime] = None) -> Optional[pd.DataFrame]:
        """
        获取OHLC数据 - 主连行情（不依赖具体合约代码，故无需传入写死的合约）
        
        Args:
            symbol: 品种代码
            start_date: 开始日期（用于增量更新）
        
        Returns:
            数据DataFrame或None
        """
        # 品种代码到中文主连合约名称的映射
        SYMBOL_TO_CHINESE = {
            # 钢铁建材
            "RB": "螺纹钢主连", "HC": "热卷主连", "I": "铁矿石主连", "J": "焦炭主连", 
            "JM": "焦煤主连", "SS": "不锈钢主连",
            # 有色金属  
            "CU": "沪铜主连", "AL": "沪铝主连", "ZN": "沪锌主连", "NI": "沪镍主连", 
            "SN": "沪锡主连", "PB": "沪铅主连", "AO": "氧化铝主连",
            # 贵金属
            "AU": "沪金主连", "AG": "沪银主连",
            # 化工能源
            "RU": "橡胶主连", "NR": "20号胶主连", "BU": "沥青主连", "FU": "燃油主连",
            "LU": "低硫燃油主连", "PG": "LPG主连", "EB": "苯乙烯主连", 
            "EG": "乙二醇主连", "MA": "甲醇主连", "TA": "PTA主连", "PX": "对二甲苯主连", 
            "PF": "短纤主连", "PR": "瓶片主连", "EC": "集运欧线主连",
            "SH": "烧碱主连", "SC": "原油主连",
            # 农产品
            "SR": "白糖主连", "CF": "棉花主连", "AP": "苹果主连", "CJ": "红枣主连", 
            "SP": "纸浆主连", "P": "棕榈油主连", "Y": "豆油主连", "M": "豆粕主连", 
            "RM": "菜粕主连", "OI": "菜油主连", "PK": "花生主连", 
            "A": "豆一主连", "B": "豆二主连", "C": "玉米主连", "CS": "淀粉主连", 
            "JD": "鸡蛋主连", "LH": "生猪主连",
            # 玻璃
            "FG": "玻璃主连", "SA": "纯碱主连",
            # 塑料
            "L": "塑料主连", "PP": "聚丙烯主连", "V": "PVC主连", "UR": "尿素主连",
            # 纺织
            "SF": "硅铁主连", "SM": "锰硅主连",
            # 新能源
            "LC": "碳酸锂主连", "SI": "工业硅主连", "PS": "多晶硅主连"
        }
        
        # 中文名优先取 commodities.yaml（用户可增删品种），内置映射仅作兜底
        base_name = self._load_variety_name_map().get(symbol) or SYMBOL_TO_CHINESE.get(symbol, symbol)
        chinese_name = base_name if str(base_name).endswith("主连") else f"{base_name}主连"
        logger.info(f"获取 {symbol} ({chinese_name}) 的OHLC数据...")
        
        # ============ 数据源策略（命中即返回，按可靠性排序）============
        #  ① 新浪主力连续 futures_main_sina —— symbol 必须传 "品种+0"(如 JD0)，一次请求全量
        #  ② 东方财富主连 futures_hist_em   —— 中文主连名，网络偶发断连时只试1次，作次级源
        #  ③ 兜底：交易所官方日报 get_futures_daily（逐日抓取，最慢，前两源失败才走）
        # 修复要点：
        #  - 新浪两接口(futures_zh_daily_sina / futures_main_sina)是同一K线端点，重复，已去重；
        #  - 新浪 symbol 此前误传裸 "JD"，应为 "JD0"，否则解析空JSON报 "Expected object or value"；
        #  - 旧代码新品种默认只回填近10天(仅建7条，MA60/换月识别失真)，现改为全量历史初始化；
        #    （接口内部本就一次拉全量再按日期切片，拉长窗口不增加请求量）
        sina_symbol = f"{symbol}0"
        
        if start_date:
            start_str = start_date.strftime('%Y%m%d')
            fetch_desc = f"增量(>{start_date.strftime('%Y-%m-%d')})"
        else:
            start_str = "19900101"  # 全量创建：尽量取完整历史
            fetch_desc = "全量历史"
        
        # ---- ① 新浪主力连续（正确传 {symbol}0，可按窗口回填，重试1次） ----
        for attempt in range(2):
            try:
                logger.debug(f"① futures_main_sina({sina_symbol}) [{fetch_desc}]" + (" (重试)" if attempt else ""))
                time.sleep(random.uniform(0.5, 1))
                df = ak.futures_main_sina(symbol=sina_symbol, start_date=start_str,
                                          end_date=datetime.now().strftime('%Y%m%d'))
                
                if df is not None and not df.empty:
                    processed_df = self._process_sina_main_data(df, symbol, start_date)
                    if not processed_df.empty:
                        logger.debug(f"新浪主连成功: {len(processed_df)} 条记录")
                        return processed_df
                logger.warning("① 新浪主连: 空数据/无新增")
            except Exception as e:
                logger.warning(f"① 新浪主连: {str(e)[:60]}")
        
        # ---- ② 东方财富主连（中文名，单次尝试，避免被反爬时反复告警） ----
        try:
            logger.debug(f"② futures_hist_em({chinese_name}) [{fetch_desc}]")
            time.sleep(random.uniform(0.5, 1))
            df = ak.futures_hist_em(symbol=chinese_name, period="daily")
            
            if df is not None and not df.empty:
                processed_df = self._process_em_data(df, symbol, start_date)
                if not processed_df.empty:
                    logger.debug(f"东方财富主连成功: {len(processed_df)} 条记录")
                    return processed_df
            logger.warning("② 东方财富主连: 空数据/无新增")
        except Exception as e:
            logger.warning(f"② 东方财富主连: {str(e)[:60]}")
        
        # ---- ③ 兜底：交易所官方日报（逐日抓取，最慢） ----
        try:
            logger.debug("③ get_futures_daily(按交易所, 逐日回填)")
            
            # 品种到交易所的映射
            VARIETY_TO_EXCHANGE = {
                'CU': 'SHFE', 'AL': 'SHFE', 'ZN': 'SHFE', 'PB': 'SHFE', 'NI': 'SHFE', 
                # 注：已剔除退市/无成交品种 WR(线材)、RR(粳米)、ZC(动力煤)、
                #     JR/LR/WH/PM/RI(稻麦系列)、PL/AD/OP(非真实品种)
                'SN': 'SHFE', 'AU': 'SHFE', 'AG': 'SHFE', 'RB': 'SHFE',
                'HC': 'SHFE', 'FU': 'SHFE', 'BU': 'SHFE', 'RU': 'SHFE', 'AO': 'SHFE',
                'A': 'DCE', 'B': 'DCE', 'C': 'DCE', 'CS': 'DCE', 'M': 'DCE', 'Y': 'DCE', 
                'P': 'DCE', 'L': 'DCE', 'V': 'DCE', 'PP': 'DCE', 'J': 'DCE', 'JM': 'DCE', 
                'I': 'DCE', 'JD': 'DCE', 'LH': 'DCE', 'EB': 'DCE', 'EG': 'DCE', 'PG': 'DCE',
                'SR': 'CZCE', 'CF': 'CZCE', 'TA': 'CZCE', 'MA': 'CZCE', 'FG': 'CZCE', 
                'RM': 'CZCE', 'OI': 'CZCE', 'AP': 'CZCE', 'CJ': 'CZCE', 
                'UR': 'CZCE', 'SA': 'CZCE', 'PF': 'CZCE', 'PK': 'CZCE', 'SF': 'CZCE', 
                'SM': 'CZCE', 'PX': 'CZCE', 'PR': 'CZCE', 'SH': 'CZCE',
                'SC': 'INE', 'LU': 'INE', 'NR': 'INE', 'BC': 'INE', 'EC': 'INE',
                'LC': 'GFEX', 'SI': 'GFEX', 'PS': 'GFEX',
                'SP': 'SHFE', 'SS': 'SHFE', 'BR': 'SHFE'
            }
            
            exchange = VARIETY_TO_EXCHANGE.get(symbol)
            if exchange:
                # 回填窗口：增量从最新日期之后开始；新品种兜底也尽量给足 MA60 热身样本(~250个交易日)
                end = datetime.now()
                start = start_date if start_date else (end - timedelta(days=365))
                start_str = start.strftime('%Y%m%d')
                end_str = end.strftime('%Y%m%d')

                time.sleep(random.uniform(0.5, 1))
                df = ak.get_futures_daily(start_date=start_str, end_date=end_str, market=exchange)
                
                if df is not None and not df.empty:
                    # 筛选该品种的数据
                    if 'variety' in df.columns:
                        df_variety = df[df['variety'] == symbol].copy()
                        if not df_variety.empty:
                            logger.debug(f"get_futures_daily成功: {len(df_variety)} 条记录")
                            processed_df = self._process_get_futures_daily_data(df_variety, symbol, start_date)
                            if not processed_df.empty:
                                return processed_df
                            # 数据找到但无新记录 → 返回空DataFrame表示"数据已最新"
                            logger.debug("get_futures_daily: 数据已是最新，无新记录")
                            return pd.DataFrame()
                logger.warning("get_futures_daily: 无该品种数据")
            else:
                logger.warning("未找到品种对应的交易所")
                
        except Exception as e:
            # 交易所官网日报接口同样受反爬影响（如大商所瑞数 412），
            # 且它是最后兜底源，通常前两源已成功，故记为 WARNING。
            detail = str(e)[:80]
            hint = "（官网疑似反爬拦截）" if any(
                k in detail for k in ("Expecting value", "412", "JSONDecode", "BadZipFile")) else ""
            logger.warning(f"③ get_futures_daily({exchange or symbol})失败{hint}: {detail}")
        
        logger.error(f"{symbol}: 所有行情源均获取失败（新浪主连/东财主连/交易所官网）")
        return None
    
    def _process_em_data(self, df: pd.DataFrame, symbol: str, start_date: Optional[datetime] = None) -> pd.DataFrame:
        """处理东方财富数据"""
        try:
            logger.debug("处理东方财富数据...")
            
            # 标准化列名 - 东方财富返回的列名
            column_mapping = {
                '时间': '时间', '开盘': '开盘', '收盘': '收盘',
                '最高': '最高', '最低': '最低', '成交量': '成交量',
                '成交额': '成交额', '持仓量': '持仓量',
                '涨跌': '涨跌', '涨跌幅': '涨跌幅'
            }
            
            # 确保数据是DataFrame格式
            if not isinstance(df, pd.DataFrame):
                logger.error(f"数据不是DataFrame格式: {type(df)}")
                return pd.DataFrame()
            
            # 处理日期格式
            if '时间' not in df.columns:
                logger.error(f"未找到时间列，可用列: {list(df.columns)}")
                return pd.DataFrame()
            
            try:
                df['时间'] = pd.to_datetime(df['时间'])
            except Exception as time_error:
                logger.error(f"时间格式转换失败: {time_error}")
                return pd.DataFrame()
            
            # 如果指定了开始日期，过滤数据
            if start_date:
                df = df[df['时间'] > start_date]
            
            if df.empty:
                logger.debug("无新数据需要更新")
                return pd.DataFrame()
            
            # 排序并重置索引
            df = df.sort_values('时间').reset_index(drop=True)
            
            logger.debug(f"东方财富数据处理完成: {len(df)} 条记录")
            if len(df) > 0:
                logger.debug(f"日期范围: {df['时间'].min().strftime('%Y-%m-%d')} ~ {df['时间'].max().strftime('%Y-%m-%d')}")
            
            return df
            
        except Exception as e:
            logger.error(f"东方财富数据处理失败: {e}")
            return pd.DataFrame()
    
    def _process_sina_daily_data(self, df: pd.DataFrame, symbol: str, start_date: Optional[datetime] = None) -> pd.DataFrame:
        """处理新浪日线数据"""
        try:
            logger.debug("处理新浪日线数据...")
            
            # 处理日期（通常在索引中）
            if hasattr(df.index, 'to_series'):
                df = df.reset_index()
                df['时间'] = pd.to_datetime(df.index if 'date' not in df.columns else df['date'], errors='coerce')
            
            # 标准化列名
            column_mapping = {
                'date': '时间', 'open': '开盘', 'high': '最高', 'low': '最低', 
                'close': '收盘', 'volume': '成交量', 'hold': '持仓量'
            }
            
            for old_col, new_col in column_mapping.items():
                if old_col in df.columns:
                    df = df.rename(columns={old_col: new_col})
            
            # 确保时间格式
            if '时间' not in df.columns:
                # 尝试其他可能的时间列名
                time_cols = [col for col in df.columns if any(name in col.lower() for name in ['time', 'date', '时间', '日期'])]
                if time_cols:
                    df = df.rename(columns={time_cols[0]: '时间'})
                else:
                    logger.error("未找到时间列")
                    return pd.DataFrame()
            
            try:
                df['时间'] = pd.to_datetime(df['时间'])
            except Exception as time_error:
                logger.error(f"时间格式转换失败: {time_error}")
                return pd.DataFrame()
            
            # 如果指定了开始日期，过滤数据
            if start_date:
                df = df[df['时间'] > start_date]
            
            if df.empty:
                logger.debug("无新数据需要更新")
                return pd.DataFrame()
            
            # 排序并重置索引
            df = df.sort_values('时间').reset_index(drop=True)
            
            logger.debug(f"新浪日线数据处理完成: {len(df)} 条记录")
            return df
            
        except Exception as e:
            logger.error(f"新浪日线数据处理失败: {e}")
            return pd.DataFrame()
    
    def _process_sina_main_data(self, df: pd.DataFrame, symbol: str, start_date: Optional[datetime] = None) -> pd.DataFrame:
        """处理新浪主力合约数据"""
        try:
            logger.debug("处理新浪主力数据...")
            
            # 标准化列名
            column_mapping = {
                '日期': '时间', 'Date': '时间', 'date': '时间',
                '开盘价': '开盘', 'Open': '开盘', 'open': '开盘',
                '最高价': '最高', 'High': '最高', 'high': '最高',
                '最低价': '最低', 'Low': '最低', 'low': '最低',
                '收盘价': '收盘', 'Close': '收盘', 'close': '收盘',
                '成交量': '成交量', 'Volume': '成交量', 'volume': '成交量',
                '持仓量': '持仓量', 'OpenInterest': '持仓量', 'open_interest': '持仓量'
            }
            
            for old_col, new_col in column_mapping.items():
                if old_col in df.columns:
                    df = df.rename(columns={old_col: new_col})
            
            # 处理日期
            if '时间' not in df.columns:
                # 尝试从索引获取日期
                if hasattr(df.index, 'to_series'):
                    df = df.reset_index()
                    df['时间'] = pd.to_datetime(df.index, errors='coerce')
                else:
                    logger.error("无法找到时间列")
                    return pd.DataFrame()
            
            df['时间'] = pd.to_datetime(df['时间'], errors='coerce')
            df = df.dropna(subset=['时间'])
            
            if start_date:
                df = df[df['时间'] > start_date]
            
            if df.empty:
                logger.debug("无新数据需要更新")
                return pd.DataFrame()
            
            df = df.sort_values('时间').reset_index(drop=True)
            
            logger.debug(f"新浪主力数据处理完成: {len(df)} 条记录")
            return df
            
        except Exception as e:
            logger.error(f"新浪主力数据处理失败: {e}")
            return pd.DataFrame()
    
    def _process_get_futures_daily_data(self, df: pd.DataFrame, symbol: str, start_date: Optional[datetime] = None) -> pd.DataFrame:
        """处理get_futures_daily返回的数据"""
        try:
            logger.debug("处理get_futures_daily数据...")
            
            # get_futures_daily返回的是多个合约的数据，需要聚合成主力合约
            # 按日期分组，选择成交量最大的合约作为当日主力
            if '时间' not in df.columns and 'date' in df.columns:
                # 转换日期格式
                if df['date'].dtype in ['int64', 'float64', 'int32']:
                    df['时间'] = pd.to_datetime(df['date'].astype(str), format='%Y%m%d', errors='coerce')
                else:
                    df['时间'] = pd.to_datetime(df['date'], errors='coerce')
            elif '时间' not in df.columns:
                logger.error("未找到日期列")
                return pd.DataFrame()
            
            # 确保数值列是数值类型
            numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'open_interest']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # 按日期分组，选择成交量最大的合约
            result_data = []
            for date, group in df.groupby('时间'):
                # 选择成交量最大的记录
                if 'volume' in group.columns:
                    main_contract = group.loc[group['volume'].idxmax()]
                else:
                    main_contract = group.iloc[0]
                result_data.append(main_contract)
            
            if not result_data:
                logger.warning("无有效数据")
                return pd.DataFrame()
            
            df = pd.DataFrame(result_data)
            
            # 标准化列名
            column_mapping = {
                'open': '开盘', 'high': '最高', 'low': '最低', 
                'close': '收盘', 'volume': '成交量', 'open_interest': '持仓量'
            }
            
            for old_col, new_col in column_mapping.items():
                if old_col in df.columns:
                    df = df.rename(columns={old_col: new_col})
            
            # 确保时间格式
            df['时间'] = pd.to_datetime(df['时间'], errors='coerce')
            df = df.dropna(subset=['时间'])
            
            # 如果指定了开始日期，过滤数据
            if start_date:
                df = df[df['时间'] > start_date]
            
            if df.empty:
                logger.debug("无新数据需要更新")
                return pd.DataFrame()
            
            # 排序并重置索引
            df = df.sort_values('时间').reset_index(drop=True)
            
            # 只保留标准 OHLC 列，丢弃 get_futures_daily 带回的原始冗余列
            # （symbol/date/turnover/settle/pre_settle/variety/open_interest 等英文列），
            # 确保首列为“时间”，与新浪/东财路径产出的 ohlc_data.csv schema 一致。
            # 修复：此前该兜底路径会把 symbol/date 写在最前，导致消费方把“品种代码”当成日期解析。
            standard_cols = ['时间', '开盘', '最高', '最低', '收盘', '成交量']
            if '持仓量' in df.columns:
                standard_cols.append('持仓量')
            df = df[[c for c in standard_cols if c in df.columns]].copy()
            
            logger.debug(f"get_futures_daily数据处理完成: {len(df)} 条记录")
            if len(df) > 0:
                logger.debug(f"日期范围: {df['时间'].min().strftime('%Y-%m-%d')} ~ {df['时间'].max().strftime('%Y-%m-%d')}")
            
            return df
            
        except Exception as e:
            logger.error(f"get_futures_daily数据处理失败: {e}")
            return pd.DataFrame()
    
    def _process_general_data(self, df: pd.DataFrame, symbol: str, start_date: Optional[datetime] = None) -> pd.DataFrame:
        """处理通用期货数据"""
        try:
            logger.debug("处理通用期货数据...")
            
            # 标准化列名
            column_mapping = {
                'date': '时间', 'trade_date': '时间',
                'open': '开盘', 'high': '最高', 'low': '最低', 'close': '收盘',
                'volume': '成交量', 'open_interest': '持仓量'
            }
            
            for old_col, new_col in column_mapping.items():
                if old_col in df.columns:
                    df = df.rename(columns={old_col: new_col})
            
            if '时间' not in df.columns:
                logger.error("无法找到时间列")
                return pd.DataFrame()
            
            df['时间'] = pd.to_datetime(df['时间'], errors='coerce')
            df = df.dropna(subset=['时间'])
            
            if start_date:
                df = df[df['时间'] > start_date]
            
            if df.empty:
                logger.debug("无新数据需要更新")
                return pd.DataFrame()
            
            df = df.sort_values('时间').reset_index(drop=True)
            
            logger.debug(f"通用数据处理完成: {len(df)} 条记录")
            return df
            
        except Exception as e:
            logger.error(f"通用数据处理失败: {e}")
            return pd.DataFrame()
    
    def calculate_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算技术指标 - 安全版本，避免数据类型错误
        
        Args:
            df: OHLC数据
        
        Returns:
            带技术指标的数据
        """
        try:
            logger.debug("开始安全指标计算...")
            
            # 确保数据按时间排序
            df = df.sort_values('时间').reset_index(drop=True)
            
            # 强制数据类型转换和清理
            close = pd.to_numeric(df["收盘"], errors='coerce')
            high = pd.to_numeric(df["最高"], errors='coerce') 
            low = pd.to_numeric(df["最低"], errors='coerce')
            open_ = pd.to_numeric(df.get("开盘", close), errors='coerce')
            volume = pd.to_numeric(df.get("成交量", pd.Series(0, index=close.index)), errors='coerce')
            
            # 使用前向填充处理NaN，然后用0填充剩余的NaN
            close = close.ffill().bfill().fillna(0)
            high = high.ffill().bfill().fillna(0)
            low = low.ffill().bfill().fillna(0) 
            open_ = open_.ffill().bfill().fillna(0)
            volume = volume.fillna(0)
            
            # 确保价格逻辑正确
            high = np.maximum(high, np.maximum(open_, close))
            low = np.minimum(low, np.minimum(open_, close))
            
            logger.debug("数据预处理完成")
            
            # ========== 基础指标 ==========
            
            # 移动平均线
            df["MA5"] = close.rolling(5, min_periods=1).mean()
            df["MA10"] = close.rolling(10, min_periods=1).mean()
            df["MA20"] = close.rolling(20, min_periods=1).mean()
            df["MA60"] = close.rolling(60, min_periods=1).mean()
            df["EMA20"] = close.ewm(span=20, adjust=False, min_periods=1).mean()
            
            # ATR
            tr1 = (high - low).abs()
            tr2 = (high - close.shift(1)).abs().fillna(0)
            tr3 = (low - close.shift(1)).abs().fillna(0)
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            df["ATR14"] = tr.rolling(14, min_periods=1).mean()
            
            # RSI
            delta = close.diff().fillna(0)
            gain = delta.where(delta > 0, 0).rolling(14, min_periods=1).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14, min_periods=1).mean()
            rs = gain / loss.replace(0, 1e-10)  # 避免除零
            df["RSI14"] = 100 - (100 / (1 + rs))
            
            # MACD
            ema12 = close.ewm(span=12, adjust=False, min_periods=1).mean()
            ema26 = close.ewm(span=26, adjust=False, min_periods=1).mean()
            df["MACD"] = ema12 - ema26
            df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False, min_periods=1).mean()
            df["MACD_HIST"] = df["MACD"] - df["MACD_SIGNAL"]
            
            # 布林带
            ma20 = df["MA20"]
            std20 = close.rolling(20, min_periods=1).std().fillna(0)
            df["BOLL_UP"] = ma20 + 2 * std20
            df["BOLL_LOW"] = ma20 - 2 * std20
            df["BOLL_MID"] = ma20
            df["BOLL_WIDTH"] = df["BOLL_UP"] - df["BOLL_LOW"]
            
            logger.debug("基础指标完成")
            
            # ========== 高级指标 ==========
            
            # KDJ
            n = 9
            llv_n = low.rolling(n, min_periods=1).min()
            hhv_n = high.rolling(n, min_periods=1).max()
            rsv = 100 * (close - llv_n) / (hhv_n - llv_n).replace(0, 1e-10)
            k = rsv.ewm(alpha=1/3, adjust=False, min_periods=1).mean()
            d = k.ewm(alpha=1/3, adjust=False, min_periods=1).mean()
            j = 3 * k - 2 * d
            df["KDJ_K"] = k
            df["KDJ_D"] = d
            df["KDJ_J"] = j
            
            # Williams %R
            hhv14 = high.rolling(14, min_periods=1).max()
            llv14 = low.rolling(14, min_periods=1).min()
            df["WILLIAMS_R14"] = -100 * (hhv14 - close) / (hhv14 - llv14).replace(0, 1e-10)
            
            # CCI - 简化版本
            tp = (high + low + close) / 3
            sma = tp.rolling(20, min_periods=1).mean()
            std = tp.rolling(20, min_periods=1).std().fillna(1)
            df["CCI20"] = (tp - sma) / (0.02 * std)
            
            # Stochastic RSI
            rsi = df["RSI14"]
            stoch_rsi = 100 * (rsi - rsi.rolling(14, min_periods=1).min()) / (
                rsi.rolling(14, min_periods=1).max() - rsi.rolling(14, min_periods=1).min()
            ).replace(0, 1e-10)
            df["STOCH_RSI"] = stoch_rsi
            
            logger.debug("高级指标完成")
            
            # ========== 成交量指标 ==========
            
            df["VOL_MA20"] = volume.rolling(20, min_periods=1).mean()
            price_change = close.diff().fillna(0)
            sign = pd.Series(np.where(price_change > 0, 1, np.where(price_change < 0, -1, 0)), index=close.index)
            df["OBV"] = (sign * volume).cumsum()
            
            logger.debug("成交量指标完成")
            
            # ========== 持仓量指标 ==========
            
            if "持仓量" in df.columns:
                oi = pd.to_numeric(df["持仓量"], errors='coerce').fillna(0)
                
                df["OI_MA20"] = oi.rolling(20, min_periods=1).mean()
                df["OI_CHANGE"] = oi.diff().fillna(0)
                df["OI_CHANGE_PCT"] = oi.pct_change().fillna(0) * 100
                
                logger.debug("持仓量指标完成")
            
            # 统计指标数量
            original_cols = ['时间', '开盘', '最高', '最低', '收盘', '成交量', '持仓量']
            indicator_cols = [col for col in df.columns if col not in original_cols]
            
            logger.debug(f"安全指标计算完成: {len(indicator_cols)} 个指标")
            
            return df
            
        except Exception as e:
            logger.error(f"技术指标计算失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return df
    
    def _standard_ohlc_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """只保留标准 OHLC 列（含可选持仓量），统一列序且保证“时间”在最前。

        用于修复旧版本通过 get_futures_daily 兜底路径落盘时混入的
        symbol/date/turnover/settle/pre_settle/variety/动态结算价 等冗余列，
        这些列会被下游把“非日期列”误当日期解析。
        """
        if df is None or df.empty:
            return df
        standard_cols = ['时间', '开盘', '最高', '最低', '收盘', '成交量', '持仓量']
        present = [c for c in standard_cols if c in df.columns]
        return df[present].copy()

    def save_variety_data(self, symbol: str, new_data: pd.DataFrame, existing_info: Optional[Dict] = None) -> bool:
        """
        保存品种数据
        
        Args:
            symbol: 品种代码
            new_data: 新数据
            existing_info: 现有数据信息
        
        Returns:
            是否保存成功
        """
        try:
            variety_dir = self.base_dir / symbol
            variety_dir.mkdir(parents=True, exist_ok=True)
            
            ohlc_file = variety_dir / "ohlc_data.csv"
            
            if existing_info and ohlc_file.exists():
                # 读取现有数据
                existing_df = pd.read_csv(ohlc_file, encoding='utf-8')
                if '时间' not in existing_df.columns:
                    # 兼容异常历史文件：找不到时间列时按第一列兜底并转 datetime
                    existing_df.columns = ['时间'] + list(existing_df.columns[1:])
                existing_df['时间'] = pd.to_datetime(existing_df['时间'])
                
                # 合并数据（先归一化列，丢弃历史冗余列）
                existing_df = self._standard_ohlc_columns(existing_df)
                new_data = self._standard_ohlc_columns(new_data)
                combined_df = pd.concat([existing_df, new_data], ignore_index=True)
                combined_df = combined_df.drop_duplicates(subset=['时间']).sort_values('时间').reset_index(drop=True)
                
                new_records = len(combined_df) - len(existing_df)
                if new_records > 0:
                    logger.debug(f"{symbol}: 新增 {new_records} 条记录")
                    self.update_stats["updated_varieties"].append(symbol)
                    self.update_stats["total_new_records"] += new_records
                else:
                    logger.debug(f"{symbol}: 无新数据")
                    self.update_stats["skipped_varieties"].append(symbol)
                    # 若历史文件含冗余列（symbol/date 等在首列），即使无新增也触发一次重写清理
                    raw_existing = pd.read_csv(ohlc_file, encoding='utf-8', nrows=1)
                    standard_cols = ['时间', '开盘', '最高', '最低', '收盘', '成交量', '持仓量']
                    has_extra_cols = any(c not in standard_cols for c in raw_existing.columns)
                    if not has_extra_cols:
                        return True
                    logger.debug(f"{symbol}: 检测到历史冗余列，统一列序后重写")
            else:
                # 新品种或无现有数据
                new_data = self._standard_ohlc_columns(new_data)
                combined_df = new_data
                logger.debug(f"{symbol}: 创建 {len(new_data)} 条记录")
                self.update_stats["new_varieties"].append(symbol)
                self.update_stats["total_new_records"] += len(new_data)
            
            # 主力换月后复权：主连 K 线是不同月份“真实主力合约”的拼接，换月日存在人为跳空，
            # 会污染 MA/MACD/KDJ 等技术指标。这里依据本地主力合约库确认的换月点做后复权，
            # 使连续价格序列无缝衔接（主力合约 ≠ 连续合约，此处处理的是用于指标的连续序列）
            try:
                from modules.main_contract_sync import MainContractSync
                sync = MainContractSync(data_root=self.base_dir.parent)
                combined_df, rollover_cnt = sync.apply_rollover_adjustment(symbol, combined_df)
                if rollover_cnt:
                    logger.debug(f"{symbol}: 识别 {rollover_cnt} 个主力换月点，已后复权消除跳空")
                elif combined_df is not None and not combined_df.empty:
                    logger.debug(f"{symbol}: 本次无已确认的换月点（主力库积累后会自动修正）")
            except Exception as e:
                logger.warning(f"{symbol}: 主力换月复权失败（不影响保存）- {str(e)[:80]}")

            # 重新计算技术指标（基于完整数据）
            combined_df = self.calculate_technical_indicators(combined_df)
            
            # 保存数据（使用UTF-8-BOM让Excel正确识别）
            combined_df.to_csv(ohlc_file, index=False, encoding='utf-8-sig')
            
            # 另外保存技术指标数据
            tech_file = variety_dir / "technical_indicators.csv"
            # 排除基础OHLC列和无用的原始数据列
            excluded_cols = [
                '时间', '开盘', '最高', '最低', '收盘', '成交量', '持仓量',
                'date', 'symbol', 'pre_settle', 'settle', 'turnover', 'index',
                '涨跌', '涨跌幅', '成交额', 'variety', 'open', 'high', 'low', 'close', 'volume', 'open_interest'
            ]
            tech_columns = [col for col in combined_df.columns if col not in excluded_cols]
            
            if tech_columns:
                tech_df = combined_df[['时间'] + tech_columns]
                tech_df.to_csv(tech_file, index=False, encoding='utf-8-sig')
            
            # 保存主要指标数据（OHLC + 常用核心指标）
            # 注意：布林带列名与 technical_indicators.csv 一致（BOLL_UP/BOLL_MID/BOLL_LOW），
            # 避免“想要的 BOLL_UPPER 不存在”导致主指标文件缺布林带
            main_file = variety_dir / "main_indicators.csv"
            main_indicator_columns = [
                'MA5', 'MA10', 'MA20', 'MA60',  # 均线
                'MACD', 'MACD_SIGNAL', 'MACD_HIST',  # MACD
                'RSI14',  # RSI
                'BOLL_UP', 'BOLL_MID', 'BOLL_LOW',  # 布林带（中轨即MA20）
                'ATR14', 'CCI20',  # ATR和CCI
                'VOL_MA20', 'OBV',  # 成交量指标
                'OI_MA20', 'OI_CHANGE', 'OI_CHANGE_PCT'  # 持仓量指标
            ]

            # 选择存在的列
            available_main_cols = [col for col in main_indicator_columns if col in combined_df.columns]
            base_cols = ['时间', '开盘', '最高', '最低', '收盘', '成交量']
            if '持仓量' in combined_df.columns:
                base_cols.append('持仓量')

            main_df = combined_df[base_cols + available_main_cols]
            main_df.to_csv(main_file, index=False, encoding='utf-8-sig')

            return True
            
        except Exception as e:
            logger.error(f"{symbol}: 保存失败 - {str(e)}")
            self.update_stats["failed_varieties"].append(symbol)
            self.update_stats["error_messages"].append(f"{symbol}: 保存失败 - {str(e)}")
            return False
    
    def update_to_date(self, target_date_str: str, specific_varieties: Optional[List[str]] = None) -> Dict:
        """
        更新数据到指定日期
        
        Args:
            target_date_str: 目标日期 (YYYY-MM-DD格式)
            specific_varieties: 指定品种列表，None表示全部品种
        
        Returns:
            更新结果统计
        """
        logger.info("技术分析数据更新器")
        
        # 解析目标日期
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d')
        except ValueError:
            try:
                target_date = datetime.strptime(target_date_str, '%Y%m%d')
            except ValueError:
                raise ValueError(f"日期格式错误: {target_date_str}，请使用 YYYY-MM-DD 或 YYYYMMDD 格式")
        
        self.update_stats["start_time"] = datetime.now()
        self.update_stats["target_date"] = target_date_str
        
        logger.info(f"目标更新日期: {target_date.strftime('%Y-%m-%d')}")
        
        # 获取现有数据状态
        existing_varieties, variety_info = self.get_existing_data_status()
        
        # 确定要更新的品种（清单动态取自每日同步维护的主力合约库）
        known_varieties = self._dynamic_varieties(target_date)
        if specific_varieties:
            wanted = [str(s).strip().upper() for s in specific_varieties if str(s).strip()]
            target_symbols = list(dict.fromkeys(wanted))
            unknown = [s for s in target_symbols if s not in known_varieties]
            if unknown:
                # 新品种/冷门品种不在清单内也照常尝试，取不到数据会计入失败，而非静默丢弃
                logger.warning(f"{', '.join(unknown)} 不在已知品种清单内，仍尝试更新")
            logger.info(f"指定更新品种: {len(target_symbols)} 个")
        else:
            target_symbols = known_varieties
            logger.info(f"全品种更新: {len(target_symbols)} 个")
        
        # 执行更新
        processed_count = 0
        
        for i, symbol in enumerate(target_symbols):
            self._report_progress("处理品种(交易所K线)", i + 1, len(target_symbols), symbol)
            logger.info(f"[{i+1}/{len(target_symbols)}] 处理品种: {symbol}")
            
            existing_info = variety_info.get(symbol)
            
            # 确定起始日期（用于增量更新）
            start_date = None
            if existing_info:
                latest_date = existing_info["latest_date"]
                days_gap = (target_date.date() - latest_date.date()).days
                
                if days_gap <= 1:
                    logger.debug(f"数据已是最新 (最新: {latest_date.strftime('%Y-%m-%d')}, 缺口: {days_gap}天)")
                    self.update_stats["skipped_varieties"].append(symbol)
                    continue
                
                logger.debug(f"最新数据: {latest_date.strftime('%Y-%m-%d')}, 缺口: {days_gap}天")
                start_date = latest_date
            else:
                logger.debug("新品种，将创建完整数据")
            
            # 获取数据
            new_data = self.fetch_ohlc_data(symbol, start_date)
            
            if new_data is None:
                # 具体失败原因已在 fetch_ohlc_data 内记录，此处只做归集，避免重复告警
                logger.warning(f"{symbol}: 本次未取到行情数据，已计入失败")
                self.update_stats["failed_varieties"].append(symbol)
                continue
            
            if new_data.empty:
                logger.debug(f"{symbol}: 无新数据")
                self.update_stats["skipped_varieties"].append(symbol)
                continue
            
            # 保存数据
            if self.save_variety_data(symbol, new_data, existing_info):
                processed_count += 1
            
            # 添加随机延迟避免请求过快
            if i < len(target_symbols) - 1:
                delay = random.uniform(0.5, 1.5)
                time.sleep(delay)
        
        # 完成统计
        self.update_stats["end_time"] = datetime.now()
        
        self.log_update_summary()

        return self.update_stats
    
    def update_data(self, target_date_str: str, specific_varieties: Optional[List[str]] = None) -> Dict:
        """
        更新数据到指定日期（与update_to_date相同，为兼容统一更新器接口）
        
        Args:
            target_date_str: 目标日期 (YYYY-MM-DD格式)
            specific_varieties: 指定品种列表，None表示全部品种
        
        Returns:
            更新结果统计
        """
        return self.update_to_date(target_date_str, specific_varieties)

def main():
    """交互式主函数"""
    logger.info("技术分析数据更新器")
    
    updater = TechnicalDataUpdater()
    
    # 获取现有数据状态
    logger.info("正在检查现有数据状态...")
    varieties, info = updater.get_existing_data_status()
    
    logger.info(f"已有品种数量: {len(varieties)} 个")
    if varieties:
        logger.info(f"品种列表: {', '.join(sorted(varieties)[:20])}{'...' if len(varieties) > 20 else ''}")
        
        # 显示最新日期
        if info:
            latest_dates = {}
            for v, v_info in info.items():
                if v_info.get('latest_date'):
                    latest_dates[v] = v_info['latest_date']
            if latest_dates:
                overall_latest = max(latest_dates.values())
                logger.info(f"当前最新数据日期: {overall_latest.strftime('%Y-%m-%d')}")
    else:
        logger.warning("当前暂无数据")
    
    # 用户输入更新参数
    logger.info("请输入更新参数:")
    
    # 输入目标日期
    default_date = datetime.now().strftime('%Y-%m-%d')
    target_date_input = input(f"📅 目标日期 (格式: YYYY-MM-DD, 直接回车使用今天 {default_date}): ").strip()
    target_date = target_date_input if target_date_input else default_date
    
    # 验证日期格式
    try:
        datetime.strptime(target_date, '%Y-%m-%d')
    except ValueError:
        logger.error(f"日期格式错误，使用默认日期: {default_date}")
        target_date = default_date
    
    # 输入品种
    varieties_input = input(f"🎯 要更新的品种 (输入品种代码用逗号分隔，如 RB,CU,AL；直接回车更新全部): ").strip()
    
    if varieties_input:
        specific_varieties = [v.strip().upper() for v in varieties_input.split(',')]
        logger.info(f"将更新指定品种: {', '.join(specific_varieties)}")
    else:
        specific_varieties = None
        logger.info("将更新所有品种")
    
    # 确认
    logger.info("更新配置:")
    logger.info(f"目标日期: {target_date}")
    logger.info(f"更新品种: {'全部' if not specific_varieties else ', '.join(specific_varieties)}")
    logger.info("更新模式: 智能增量更新（自动从最新数据补全到目标日期）")
    
    confirm = input("\n确认开始更新？(y/N): ").strip().lower()
    if confirm != 'y':
        logger.error("已取消更新")
        return
    
    # 执行更新
    logger.info("开始更新...")
    result = updater.update_to_date(target_date, specific_varieties)
    
    logger.info("更新完成!")

if __name__ == "__main__":
    main()
