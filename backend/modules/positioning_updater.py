#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓数据更新器
基于comprehensive_positioning_data_system的逻辑，支持多数据源和增量更新
"""

import akshare as ak
import pandas as pd
import requests
from pathlib import Path
from datetime import datetime, timedelta
import time
import random
import json
import warnings
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings('ignore')

# 品种名称映射
SYMBOL_NAMES = {
    'A': '豆一', 'AG': '白银', 'AL': '铝', 'AU': '黄金', 'B': '豆二',
    'BU': '沥青', 'C': '玉米', 'CF': '棉花', 'CU': '铜', 'CY': '棉纱',
    'EB': '苯乙烯', 'EG': '乙二醇', 'FG': '玻璃', 'FU': '燃油', 'HC': '热卷',
    'I': '铁矿石', 'J': '焦炭', 'JD': '鸡蛋', 'JM': '焦煤', 'L': '聚乙烯',
    'LC': '碳酸锂', 'LH': '生猪', 'LU': '低硫燃料油', 'M': '豆粕', 'MA': '甲醇',
    'NI': '镍', 'NR': '20号胶', 'OI': '菜籽油', 'P': '棕榈油', 'PB': '铅',
    'PF': '短纤', 'PG': '液化石油气', 'PP': '聚丙烯', 'PR': '瓶片', 'PS': '多晶硅',
    'PX': '对二甲苯', 'RB': '螺纹钢', 'RM': '菜籽粕', 'RU': '天然橡胶', 'SA': '纯碱',
    'SF': '硅铁', 'SI': '工业硅', 'SM': '锰硅', 'SN': '锡', 'SP': '纸浆',
    'SR': '白糖', 'SS': '不锈钢', 'TA': 'PTA', 'UR': '尿素', 'V': 'PVC',
    'Y': '豆油', 'ZN': '锌'
}

# 大商所品种：新浪"成交持仓"排名接口不覆盖大商所，需走大商所官网/东财镜像排名接口
DCE_SYMBOLS = {
    "A", "B", "C", "EB", "EG", "I", "J", "JD", "JM",
    "L", "LH", "M", "P", "PG", "PP", "V", "Y",
}

# 东方财富数据中心（大商所每日会员成交持仓排名的第三方镜像，字段与官网一致）。
# 2025 年起 www.dce.com.cn 全站启用动态 JS 反爬(瑞数)，requests/akshare 直连官网
# 会收到 412 挑战页（表现为 "File is not a zip file" 等），东财 datacenter 接口
# 无该限制，作为大商所持仓排名的首选数据源。
EM_DATACENTER_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_DAILYPOSITION_REPORT = "RPT_FUTU_DAILYPOSITION"
EM_DAILYPOSITION_COLUMNS = (
    "SECURITY_CODE,MEMBER_NAME_ABBR,"
    "VOLUME_RANK,VOLUME,VOLUME_CHANGE,"
    "LP_RANK,LONG_POSITION,LP_CHANGE,"
    "SP_RANK,SHORT_POSITION,SP_CHANGE"
)
EM_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/futures/",
}


def _normalize_contract_code(code: str) -> str:
    """归一化合约代码用于比对（忽略大小写及分隔符）"""
    return "".join(ch for ch in str(code) if ch.isalnum()).upper()


class PositioningDataUpdater:
    """持仓数据更新器"""
    
    def __init__(self, database_path: str = "qihuo/database/positioning"):
        """
        初始化持仓数据更新器
        
        Args:
            database_path: 数据库路径
        """
        self.database_path = Path(database_path)
        self.database_path.mkdir(parents=True, exist_ok=True)
        
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
    
    def get_existing_data_status(self) -> Tuple[List[str], Dict]:
        """
        获取现有数据状态
        
        Returns:
            varieties: 现有品种列表
            variety_info: 各品种详细信息
        """
        print("🔍 检查现有持仓数据状态...")
        
        varieties = []
        variety_info = {}
        
        if not self.database_path.exists():
            return [], {}
        
        variety_folders = [d for d in self.database_path.iterdir() if d.is_dir()]
        print(f"📂 发现 {len(variety_folders)} 个品种文件夹")
        
        for folder in variety_folders:
            variety = folder.name
            
            # 检查各类持仓数据文件
            files_info = {
                "long_position_ranking.csv": None,
                "short_position_ranking.csv": None,
                "volume_ranking.csv": None,
                "positioning_summary.json": None
            }
            
            has_data = False
            total_records = 0
            date_range = {"earliest": None, "latest": None}
            
            for filename in files_info.keys():
                file_path = folder / filename
                if file_path.exists():
                    try:
                        if filename.endswith('.csv'):
                            df = pd.read_csv(file_path, encoding='utf-8')
                            if len(df) > 0 and 'date' in df.columns:
                                df['date'] = pd.to_datetime(df['date'])
                                file_earliest = df['date'].min()
                                file_latest = df['date'].max()
                                file_records = len(df)
                                
                                files_info[filename] = {
                                    "records": file_records,
                                    "earliest": file_earliest,
                                    "latest": file_latest
                                }
                                
                                total_records += file_records
                                has_data = True
                                
                                # 更新品种整体日期范围
                                if date_range["earliest"] is None or file_earliest < date_range["earliest"]:
                                    date_range["earliest"] = file_earliest
                                if date_range["latest"] is None or file_latest > date_range["latest"]:
                                    date_range["latest"] = file_latest
                        
                        elif filename.endswith('.json'):
                            files_info[filename] = {"exists": True}
                            
                    except Exception as e:
                        files_info[filename] = {"error": str(e)[:50]}
            
            if has_data:
                variety_info[variety] = {
                    "files": files_info,
                    "total_records": total_records,
                    "date_range": date_range
                }
                varieties.append(variety)
                
                earliest_str = date_range["earliest"].strftime('%Y-%m-%d') if date_range["earliest"] else "无"
                latest_str = date_range["latest"].strftime('%Y-%m-%d') if date_range["latest"] else "无"
                print(f"  {variety}: {total_records} 条记录 ({earliest_str} ~ {latest_str})")
        
        print(f"\n📊 总计: {len(varieties)} 个有效品种")
        return varieties, variety_info
    
    def generate_trading_dates(self, start_date: datetime, end_date: datetime) -> List[str]:
        """
        生成交易日列表
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
        
        Returns:
            交易日列表 (YYYYMMDD格式)
        """
        dates = []
        current = start_date
        
        while current <= end_date:
            # 简单的工作日判断（不考虑节假日）
            if current.weekday() < 5:
                dates.append(current.strftime('%Y%m%d'))
            current += timedelta(days=1)
        
        return dates
    
    def get_dominant_contracts(self, symbol: str, start_date: datetime, end_date: datetime) -> Dict[str, str]:
        """
        获取主力合约信息（模拟实现）
        
        Args:
            symbol: 品种代码
            start_date: 开始日期
            end_date: 结束日期
        
        Returns:
            日期到主力合约的映射
        """
        # 简化实现：生成当前年份的主要合约月份
        current_year = datetime.now().year
        year_suffix = str(current_year)[-2:]
        
        # 常见的主力合约月份
        main_months = ['01', '03', '05', '07', '09', '11']
        
        # 构造主力合约代码
        contracts = [f"{symbol.lower()}{year_suffix}{month}" for month in main_months]
        
        # 为每个交易日分配主力合约（简化逻辑）
        trading_dates = self.generate_trading_dates(start_date, end_date)
        contract_map = {}
        
        for date_str in trading_dates:
            # 根据日期选择合约（简化逻辑）
            month = int(date_str[4:6])
            contract_index = min(month // 2, len(contracts) - 1)
            contract_map[date_str] = contracts[contract_index]
        
        return contract_map
    
    def fetch_positioning_data_by_contracts(self, dominant_contracts: Dict[str, Dict[str, str]], 
                                          start_date: datetime, target_date: datetime) -> Dict[str, List]:
        """
        基于主力合约获取持仓数据
        
        Args:
            dominant_contracts: {symbol: {date: contract}} 主力合约信息
            start_date: 开始日期
            target_date: 结束日期
        
        Returns:
            按品种分组的持仓数据
        """
        print(f"    📡 基于主力合约获取持仓数据...")
        print(f"    📅 数据时间范围: {start_date.strftime('%Y-%m-%d')} ~ {target_date.strftime('%Y-%m-%d')}")
        
        # 生成交易日列表
        trading_dates = self.generate_trading_dates(start_date, target_date)
        print(f"    📅 交易日数量: {len(trading_dates)} 天")
        
        # 持仓数据类型
        position_types = ["成交量", "多单持仓", "空单持仓"]
        
        all_positioning_data = {}  # {symbol: [data_list]}
        total_requests = 0
        successful_requests = 0
        
        for symbol, contracts_dict in dominant_contracts.items():
            print(f"\n    🔍 处理品种: {symbol} ({SYMBOL_NAMES.get(symbol, symbol)})")
            
            symbol_data = []
            symbol_requests = 0
            symbol_success = 0
            
            for date_str in trading_dates:
                if date_str not in contracts_dict:
                    continue
                
                contract = contracts_dict[date_str]
                print(f"      📅 {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} | {contract}", end=" ")
                
                daily_success = 0
                daily_errors = 0

                # 获取三种类型的持仓数据
                for position_type in position_types:
                    try:
                        df = ak.futures_hold_pos_sina(
                            symbol=position_type,
                            contract=contract,
                            date=date_str
                        )

                        symbol_requests += 1
                        total_requests += 1

                        if df is not None and not df.empty:
                            # 添加元数据
                            df = df.copy()
                            df['date'] = date_str
                            df['contract'] = contract
                            df['position_type'] = position_type
                            df['symbol'] = symbol

                            # 标准化列名
                            if len(df.columns) >= 4:
                                if position_type == "多单持仓":
                                    df.columns = ['排名', '会员简称', '持仓量', '比上交易增减', 'date', 'contract', 'position_type', 'symbol']
                                elif position_type == "空单持仓":
                                    df.columns = ['排名', '会员简称', '持仓量', '比上交易增减', 'date', 'contract', 'position_type', 'symbol']
                                elif position_type == "成交量":
                                    df.columns = ['排名', '会员简称', '成交量', '比上交易增减', 'date', 'contract', 'position_type', 'symbol']

                            symbol_data.append(df)
                            symbol_success += 1
                            successful_requests += 1
                            daily_success += 1

                        # 避免请求过快
                        time.sleep(random.uniform(0.5, 1.0))

                    except Exception as e:
                        daily_errors += 1
                        print(f"❌{position_type[:2]}", end="")
                        continue

                if daily_success > 0:
                    print(f" ✅({daily_success}/3)")
                elif daily_errors > 0:
                    print(" ❌ 接口异常")
                else:
                    print(" ⏳ 数据未发布")
            
            if symbol_data:
                all_positioning_data[symbol] = symbol_data
                success_rate = symbol_success / symbol_requests * 100 if symbol_requests > 0 else 0
                print(f"      ✅ {symbol}: 获取 {len(symbol_data)} 批数据 (成功率: {success_rate:.1f}%)")
            else:
                print(f"      ❌ {symbol}: 未获取到任何数据")
        
        overall_success_rate = successful_requests / total_requests * 100 if total_requests > 0 else 0
        print(f"    📊 总体统计: {successful_requests}/{total_requests} 请求成功 (成功率: {overall_success_rate:.1f}%)")
        
        return all_positioning_data
    
    def _main_contract_sync(self):
        """惰性加载主力合约同步器（兼容 data_service 与命令行两种运行方式）"""
        try:
            from modules.main_contract_sync import MainContractSync
        except Exception:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from modules.main_contract_sync import MainContractSync
        # 主力合约库与 basis/positioning 同级，都位于数据根目录下
        return MainContractSync(data_root=self.database_path.parent)

    def resolve_dominant_contracts(self, start_date: datetime, target_date: datetime,
                                   symbols: List[str]) -> Dict[str, Dict[str, str]]:
        """
        确认主力合约（本地主力合约库优先；缺失时用基差数据补种；
        仍缺失则联网查询并保存到本地，自动创建品种目录）

        Args:
            start_date: 开始日期
            target_date: 结束日期
            symbols: 需要更新的品种列表

        Returns:
            {symbol: {YYYYMMDD: contract}}
        """
        print(f"    📖 确认主力合约（本地优先，缺失联网查询并落盘）...")

        trading_dates = self.generate_trading_dates(start_date, target_date)
        if not trading_dates:
            return {}

        # 只处理配置内存在的品种，避免对无关品种发起联网请求
        valid_symbols = [s.upper() for s in symbols if s and s.upper() in SYMBOL_NAMES]
        if not valid_symbols:
            return {}

        sync = self._main_contract_sync()
        return sync.ensure(valid_symbols, trading_dates)

    def load_dominant_contracts_from_basis(self, start_date: datetime, target_date: datetime) -> Dict[str, Dict[str, str]]:
        """
        从基差数据中提取各品种的主力合约信息
        
        Args:
            start_date: 开始日期
            target_date: 结束日期
            
        Returns:
            {symbol: {date: contract}} 主力合约信息
        """
        print(f"    📖 从基差数据中提取主力合约信息...")
        
        # 基差数据目录
        basis_root = self.database_path.parent / "basis"
        
        dominant_contracts = {}
        
        for symbol in SYMBOL_NAMES.keys():
            basis_file = basis_root / symbol / "basis_data.csv"
            
            if not basis_file.exists():
                print(f"      ⚠️ {symbol}: 基差数据文件不存在")
                continue
            
            try:
                df = pd.read_csv(basis_file, encoding='utf-8')
                
                if 'date' not in df.columns or 'dominant_contract' not in df.columns:
                    print(f"      ⚠️ {symbol}: 基差数据格式不正确")
                    continue
                
                # 转换日期格式
                df['date'] = pd.to_datetime(df['date'])
                
                # 筛选时间范围
                mask = (df['date'] >= start_date) & (df['date'] <= target_date)
                filtered_df = df[mask].copy()
                
                if len(filtered_df) == 0:
                    print(f"      ⚠️ {symbol}: 指定时间范围内无基差数据")
                    continue
                
                # 提取主力合约信息
                contracts_dict = {}
                for _, row in filtered_df.iterrows():
                    date_str = row['date'].strftime('%Y%m%d')
                    contract = str(row['dominant_contract']).strip()
                    if contract and contract != 'nan':
                        # 修复合约代码格式：3位数字的合约需要在英文后补2
                        # 例如：CY511 -> CY2511
                        if len(contract) >= 5:  # 至少要有字母+数字
                            # 找到字母和数字的分界点
                            alpha_part = ""
                            num_part = ""
                            for i, char in enumerate(contract):
                                if char.isalpha():
                                    alpha_part += char
                                elif char.isdigit():
                                    num_part = contract[i:]
                                    break
                            
                            # 如果数字部分是3位，在前面补2
                            if len(num_part) == 3 and num_part.isdigit():
                                contract = alpha_part + "2" + num_part
                                print(f"        🔧 修正合约代码: {row['dominant_contract']} -> {contract}")
                        
                        contracts_dict[date_str] = contract
                
                dominant_contracts[symbol] = contracts_dict
                print(f"      ✅ {symbol}: 提取了 {len(contracts_dict)} 个交易日的主力合约")
                
            except Exception as e:
                print(f"      ❌ {symbol}: 处理基差数据失败 - {str(e)[:50]}")
                continue
        
        print(f"    ✅ 主力合约信息提取完成，共 {len(dominant_contracts)} 个品种")
        return dominant_contracts
    
    def _extract_symbol_from_contract(self, contract: str) -> str:
        """从合约代码中提取品种代码"""
        import re
        
        # 移除数字，保留字母
        symbol = re.sub(r'\d+', '', str(contract).upper())
        
        # 处理特殊情况
        if symbol in ['IF', 'IH', 'IC', 'IM', 'TS', 'TF', 'T']:  # 中金所品种
            return symbol
        elif len(symbol) >= 1:
            return symbol
        else:
            return str(contract)[:2].upper()
    
    def _process_contract_positioning_data(self, contract_data: pd.DataFrame, 
                                         contract: str, symbol: str, date_str: str,
                                         exchange_name: str, all_data: Dict):
        """处理单个合约的持仓数据"""
        
        # 不同交易所的列名映射
        column_mappings = {
            "中金所": {
                "long_party_name": "会员简称",
                "long_open_interest": "持仓量", 
                "long_open_interest_chg": "比上交易增减",
                "short_party_name": "会员简称",
                "short_open_interest": "持仓量",
                "short_open_interest_chg": "比上交易增减",
                "vol_party_name": "会员简称",
                "vol": "成交量",
                "vol_chg": "比上交易增减"
            },
            "郑商所": {
                "long_party_name": "会员简称",
                "long_open_interest": "持仓量",
                "long_open_interest_chg": "比上交易增减", 
                "short_party_name": "会员简称",
                "short_open_interest": "持仓量",
                "short_open_interest_chg": "比上交易增减",
                "vol_party_name": "会员简称",
                "vol": "成交量"
            },
            "上期所": {
                "long_party_name": "会员简称",
                "long_open_interest": "持仓量",
                "long_open_interest_chg": "比上交易增减",
                "short_party_name": "会员简称", 
                "short_open_interest": "持仓量",
                "short_open_interest_chg": "比上交易增减",
                "vol_party_name": "会员简称",
                "vol": "成交量"
            },
            "广期所": {
                "long_party_name": "会员简称",
                "long_open_interest": "持仓量",
                "short_party_name": "会员简称",
                "short_open_interest": "持仓量", 
                "vol_party_name": "会员简称",
                "vol": "成交量"
            }
        }
        
        mapping = column_mappings.get(exchange_name, {})

        def _is_blank_name(value):
            """部分行情源（如东财）某榜单不足20名时其余行无会员简称，需跳过"""
            if value is None:
                return True
            return str(value).strip() in ('', 'nan', 'None')

        # 处理多头持仓
        if 'long_party_name' in contract_data.columns:
            for i, (_, row) in enumerate(contract_data.iterrows()):
                if i >= 20:  # 限制数量
                    break
                if _is_blank_name(row.get('long_party_name')):
                    continue

                all_data['long_positions'].append({
                    "排名": i + 1,
                    "会员简称": str(row.get('long_party_name', f'会员{i+1}')),
                    "持仓量": self._parse_int_with_comma(row.get('long_open_interest', 0)),
                    "比上交易增减": self._parse_int_with_comma(row.get('long_open_interest_chg', 0)),
                    "date": date_str,
                    "contract": contract,
                    "position_type": "多单持仓",
                    "symbol": symbol
                })

        # 处理空头持仓
        if 'short_party_name' in contract_data.columns:
            for i, (_, row) in enumerate(contract_data.iterrows()):
                if i >= 20:  # 限制数量
                    break
                if _is_blank_name(row.get('short_party_name')):
                    continue

                all_data['short_positions'].append({
                    "排名": i + 1,
                    "会员简称": str(row.get('short_party_name', f'会员{i+1}')),
                    "持仓量": self._parse_int_with_comma(row.get('short_open_interest', 0)),
                    "比上交易增减": self._parse_int_with_comma(row.get('short_open_interest_chg', 0)),
                    "date": date_str,
                    "contract": contract,
                    "position_type": "空单持仓", 
                    "symbol": symbol
                })

        # 处理成交量排名
        if 'vol_party_name' in contract_data.columns:
            for i, (_, row) in enumerate(contract_data.iterrows()):
                if i >= 20:  # 限制数量
                    break
                if _is_blank_name(row.get('vol_party_name')):
                    continue

                all_data['volume_rankings'].append({
                    "排名": i + 1,
                    "会员简称": str(row.get('vol_party_name', f'会员{i+1}')),
                    "成交量": self._parse_int_with_comma(row.get('vol', 0)),
                    "比上交易增减": self._parse_int_with_comma(row.get('vol_chg', 0)),
                    "date": date_str,
                    "contract": contract,
                    "position_type": "成交量",
                    "symbol": symbol
                })
    
    def _parse_int_with_comma(self, value):
        """解析可能包含逗号的数字字符串"""
        if value is None or value == '':
            return 0
        
        try:
            # 如果是数字，直接返回
            if isinstance(value, (int, float)):
                return int(value)
            
            # 如果是字符串，移除逗号后转换
            if isinstance(value, str):
                # 移除逗号和空格
                clean_value = value.replace(',', '').replace(' ', '').strip()
                if clean_value == '' or clean_value == '-':
                    return 0
                return int(float(clean_value))
            
            return int(value)
        except (ValueError, TypeError):
            return 0
    
    def _fetch_dce_data_with_fallback(self, date: str, symbols: Optional[List[str]] = None):
        """
        大商所数据获取（多种备用策略）
        
        Args:
            date: 日期字符串 (YYYYMMDD)
            symbols: 需要获取的品种列表，None 表示不做品种过滤
            
        Returns:
            大商所持仓数据字典 {合约代码: DataFrame}，与其他交易所格式一致
        """
        print(f"        🏢 大商所数据获取 (多策略)...")
        
        strategies = [
            {
                # 首选：东财数据中心。2025 年起 www.dce.com.cn 启用动态 JS 反爬，
                # 普通请求只能拿到 412 挑战页（akshare 表现为 "File is not a zip file"
                # / "list index out of range"），东财 datacenter 接口不受影响且字段与
                # 官网一致（含三榜排名），可完整还原前20名会员持仓。
                "name": "东财数据中心",
                "func": self._fetch_dce_data_from_eastmoney,
                "params": {"date": date, "symbols": list(symbols) if symbols else None},
            },
            {
                "name": "官网主接口",
                "func": ak.futures_dce_position_rank,
                "params": {"date": date} if not symbols else {"date": date, "vars_list": list(symbols)},
            },
            {
                "name": "官网备用接口",
                "func": ak.futures_dce_position_rank_other,
                "params": {"date": date},
            },
            {
                "name": "官网排名表接口",
                "func": ak.get_dce_rank_table,
                "params": {"date": date} if not symbols else {"date": date, "vars_list": list(symbols)},
            },
        ]
        
        for strategy in strategies:
            try:
                print(f"          🔄 尝试{strategy['name']}...", end="")
                data = strategy['func'](**strategy['params'])
                
                if data and isinstance(data, dict) and len(data) > 0:
                    print(f" ✅ 成功 ({len(data)}个合约)")
                    return data
                else:
                    print(f" ❌ 无数据")
                    
            except Exception as e:
                detail = str(e)
                hint = ""
                if strategy["name"] != "东财数据中心" and \
                        any(k in detail for k in ("zip", "BadZipFile", "list index", "412")):
                    hint = "（官网疑似动态反爬拦截，仅作官网通道降级尝试）"
                print(f" ❌ 失败: {detail[:40]}...{hint}")
                continue
        
        # 如果所有策略都失败，返回空字典
        print(f"        ⚠️ 大商所数据暂时无法获取")
        return {}

    # ------------------------------------------------------------------ #
    # 大商所持仓排名 - 东方财富数据中心数据源
    # ------------------------------------------------------------------ #
    @staticmethod
    def _dce_date_to_dash(date) -> str:
        """YYYYMMDD -> YYYY-MM-DD；已是 YYYY-MM-DD 则原样返回"""
        s = str(date)
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:]}"
        return s

    def _query_em_daily_position(self, date_dash: str, symbol: str) -> List[dict]:
        """
        查询东财数据中心：某品种在指定交易日全部合约的会员持仓排名明细。
        返回原始行字典列表（含 SECURITY_CODE/MEMBER_NAME_ABBR/三榜排名与数值）。
        """
        rows: List[dict] = []
        page = 1
        while True:
            params = {
                "columns": EM_DAILYPOSITION_COLUMNS,
                "reportName": EM_DAILYPOSITION_REPORT,
                "filter": f'(TRADE_DATE=\'{date_dash}\')(TYPE="0")(TRADE_CODE="{symbol}")',
                "pageNumber": str(page),
                "pageSize": "500",
                "source": "WEB",
                "client": "WEB",
            }
            resp = requests.get(
                EM_DATACENTER_API, params=params, timeout=25, headers=EM_HEADERS
            )
            resp.raise_for_status()
            payload = resp.json()
            if not payload.get("result"):
                break
            data = payload["result"].get("data") or []
            rows.extend(data)
            total = payload["result"].get("count") or 0
            if not data or len(rows) >= total:
                break
            page += 1
            time.sleep(0.2)
        return rows

    @staticmethod
    def _dce_merged_df_from_em(g: pd.DataFrame) -> Optional[pd.DataFrame]:
        """
        将东财返回的"每会员一行(含三榜各自名次)"明细，转成与官网一致、
        akshare futures_dce_position_rank 输出的"按名次对齐"三榜 DataFrame。
        三榜各自前20名（名次 9999 表示未入榜）分别排序后按名次 1-20 对齐。
        """
        if g is None or g.empty:
            return None
        g = g.copy()
        # 数值列统一转 float，避免 9999/空值的比较问题
        for col in ("VOLUME_RANK", "LP_RANK", "SP_RANK",
                    "VOLUME", "VOLUME_CHANGE",
                    "LONG_POSITION", "LP_CHANGE",
                    "SHORT_POSITION", "SP_CHANGE"):
            g[col] = pd.to_numeric(g[col], errors="coerce")

        def ranked_list(rank_col, val_col, chg_col):
            sub = g[g[rank_col].notna() & (g[rank_col] < 9000)]
            sub = sub.sort_values(rank_col)
            names = ["" if pd.isna(x) else str(x) for x in sub["MEMBER_NAME_ABBR"]]
            vals = [None if pd.isna(x) else x for x in sub[val_col]]
            chgs = [None if pd.isna(x) else x for x in sub[chg_col]]
            return names, vals, chgs

        def pad(seq, length: int = 20):
            seq = list(seq)
            return (seq + [""] * length)[:length]

        long_names, long_vals, long_chgs = ranked_list("LP_RANK", "LONG_POSITION", "LP_CHANGE")
        short_names, short_vals, short_chgs = ranked_list("SP_RANK", "SHORT_POSITION", "SP_CHANGE")
        vol_names, vol_vals, vol_chgs = ranked_list("VOLUME_RANK", "VOLUME", "VOLUME_CHANGE")

        merged = pd.DataFrame({
            "long_party_name": pad(long_names),
            "long_open_interest": pad(long_vals),
            "long_open_interest_chg": pad(long_chgs),
            "short_party_name": pad(short_names),
            "short_open_interest": pad(short_vals),
            "short_open_interest_chg": pad(short_chgs),
            "vol_party_name": pad(vol_names),
            "vol": pad(vol_vals),
            "vol_chg": pad(vol_chgs),
        })
        if merged["long_party_name"].eq("").all() and \
           merged["short_party_name"].eq("").all() and \
           merged["vol_party_name"].eq("").all():
            return None
        return merged

    def _fetch_dce_data_from_eastmoney(self, date, symbols: Optional[List[str]] = None) -> Dict:
        """
        通过东方财富数据中心获取大商所持仓排名（多策略首选数据源）。

        东财 RPT_FUTU_DAILYPOSITION 按(合约, 交易日)提供每家会员的成交量/多单/空单
        及其官方名次(9999=未进前20)，与官网发布的"前20名会员持仓排名"一一对应。

        Args:
            date: 日期字符串 (YYYYMMDD)
            symbols: 需要获取的品种列表，None 表示不做品种过滤

        Returns:
            {合约代码(大写): DataFrame}，DataFrame 结构与 akshare
            futures_dce_position_rank 一致（三榜按名次 1-20 逐行对齐）
        """
        date_dash = self._dce_date_to_dash(date)
        symbol_list = sorted(symbols) if symbols else sorted(DCE_SYMBOLS)
        result: Dict[str, pd.DataFrame] = {}

        for symbol in symbol_list:
            try:
                rows = self._query_em_daily_position(date_dash, symbol.upper())
            except Exception as e:
                print(f"            ⚠️ {symbol}: 东财接口异常 ({str(e)[:60]})")
                continue
            if not rows:
                continue
            df = pd.DataFrame(rows)
            for contract, group in df.groupby("SECURITY_CODE"):
                if contract is None:
                    continue
                merged = self._dce_merged_df_from_em(group)
                if merged is not None:
                    result[str(contract).upper()] = merged
            time.sleep(0.15)

        return result

    def _update_dce_varieties(self, symbols: List[str],
                              dominant_contracts: Dict[str, Dict[str, str]],
                              trading_dates: List[str]) -> Dict:
        """
        大商所品种：逐交易日调用官网排名接口，按主力合约抽取前20席位数据

        Args:
            symbols: 需要更新的大商所品种列表
            dominant_contracts: {symbol: {YYYYMMDD: 主力合约}}
            trading_dates: 交易日列表 (YYYYMMDD)

        Returns:
            {symbol: {"long_positions": [], "short_positions": [], "volume_rankings": []}}
        """
        print(f"    📡 大商所持仓排名获取（{len(symbols)} 个品种）...")

        result: Dict[str, Dict[str, list]] = {}

        for date_str in trading_dates:
            date_display = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            # 该交易日需要的主合约（仅取目标品种）
            needed = {}
            for symbol in symbols:
                contract = dominant_contracts.get(symbol, {}).get(date_str)
                if contract:
                    needed[symbol] = str(contract)
            if not needed:
                continue

            data = self._fetch_dce_data_with_fallback(date_str, symbols=symbols)
            if not data:
                print(f"      ⚠️ {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}: 无数据")
                continue

            # 归一化索引：合约代码(如 jd2611/JD2611) -> DataFrame
            index = {}
            for key, df in data.items():
                if df is not None and not df.empty:
                    index[_normalize_contract_code(key)] = df

            hit_symbols = []
            for symbol, contract in needed.items():
                df = index.get(_normalize_contract_code(contract))
                if df is None:
                    continue
                bucket = result.setdefault(
                    symbol,
                    {"long_positions": [], "short_positions": [], "volume_rankings": []},
                )
                self._process_contract_positioning_data(
                    df, contract, symbol, date_display, "大商所", bucket
                )
                hit_symbols.append(symbol)

            if hit_symbols:
                print(f"      ✅ {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}: "
                      f"{len(hit_symbols)}/{len(needed)} 个品种匹配主力合约")

        return result
    

    
    def save_positioning_data(self, symbol: str, all_data: List[pd.DataFrame]) -> Tuple[bool, int]:
        """
        保存单个品种的持仓数据
        
        Args:
            symbol: 品种代码
            all_data: 该品种的所有持仓数据列表
            
        Returns:
            (是否成功, 总记录数)
        """
        try:
            # 创建品种目录
            symbol_dir = self.database_path / symbol
            symbol_dir.mkdir(parents=True, exist_ok=True)
            
            # 持仓数据类型
            position_types = ["成交量", "多单持仓", "空单持仓"]
            
            total_records = 0
            
            # 按数据类型分别保存
            for position_type in position_types:
                type_data = [df for df in all_data if not df.empty and df.iloc[0]['position_type'] == position_type]
                
                if not type_data:
                    continue
                    
                # 合并同类型数据
                combined_df = pd.concat(type_data, ignore_index=True)
                
                # 转换日期格式
                combined_df['date'] = pd.to_datetime(combined_df['date'], format='%Y%m%d')
                
                # 按日期排序
                combined_df = combined_df.sort_values(['date', '排名']).reset_index(drop=True)
                
                # 保存文件
                filename_map = {
                    "成交量": "volume_ranking.csv",
                    "多单持仓": "long_position_ranking.csv", 
                    "空单持仓": "short_position_ranking.csv"
                }
                
                file_path = symbol_dir / filename_map[position_type]
                combined_df.to_csv(file_path, index=False, encoding='utf-8-sig')
                
                total_records += len(combined_df)
                print(f"      💾 {position_type}: {len(combined_df)} 条记录已保存")
            
            # 保存汇总信息
            summary_data = {
                'symbol': symbol,
                'symbol_name': SYMBOL_NAMES.get(symbol, symbol),
                'total_records': total_records,
                'position_types': position_types,
                'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            summary_file = symbol_dir / "positioning_summary.json"
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary_data, f, ensure_ascii=False, indent=2)
            
            return True, total_records
            
        except Exception as e:
            print(f"      ❌ 保存失败: {e}")
            return False, 0
    
    def save_variety_data(self, symbol: str, new_data: Dict, existing_info: Optional[Dict] = None) -> bool:
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
            variety_dir = self.database_path / symbol
            variety_dir.mkdir(parents=True, exist_ok=True)
            
            new_records_count = 0
            
            # 保存各类数据
            for data_type, file_name in [
                ("long_positions", "long_position_ranking.csv"),
                ("short_positions", "short_position_ranking.csv"),
                ("volume_rankings", "volume_ranking.csv")
            ]:
                if data_type not in new_data or not new_data[data_type]:
                    continue
                
                file_path = variety_dir / file_name
                new_df = pd.DataFrame(new_data[data_type])
                
                if file_path.exists():
                    # 合并数据
                    existing_df = pd.read_csv(file_path, encoding='utf-8')
                    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
                    
                    # 去重
                    key_columns = ['date', 'contract'] + (['-name'] if '会员简称' in combined_df.columns else [])
                    if '会员简称' in combined_df.columns:
                        key_columns = ['date', 'contract', '会员简称']
                    combined_df = combined_df.drop_duplicates(subset=key_columns).reset_index(drop=True)
                    
                    new_records_count += len(combined_df) - len(existing_df)
                else:
                    # 新文件
                    combined_df = new_df
                    new_records_count += len(new_df)
                
                combined_df.to_csv(file_path, index=False, encoding='utf-8')
            
            # 保存摘要信息
            summary_file = variety_dir / "positioning_summary.json"
            summary_info = {
                "symbol": symbol,
                "symbol_name": SYMBOL_NAMES.get(symbol, symbol),
                "last_updated": datetime.now().isoformat(),
                "files": {
                    "long_position_ranking": len(new_data.get("long_positions", [])),
                    "short_position_ranking": len(new_data.get("short_positions", [])),
                    "volume_ranking": len(new_data.get("volume_rankings", []))
                }
            }
            
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary_info, f, ensure_ascii=False, indent=2)
            
            if new_records_count > 0:
                print(f"    ✅ {symbol}: 新增 {new_records_count} 条记录")
                self.update_stats["updated_varieties"].append(symbol)
                self.update_stats["total_new_records"] += new_records_count
            else:
                print(f"    ℹ️ {symbol}: 无新数据")
                self.update_stats["skipped_varieties"].append(symbol)
            
            return True
            
        except Exception as e:
            print(f"    ❌ {symbol}: 保存失败 - {str(e)}")
            self.update_stats["failed_varieties"].append(symbol)
            self.update_stats["error_messages"].append(f"{symbol}: 保存失败 - {str(e)}")
            return False
    
    def update_to_date(self, target_date_str: str, specific_varieties: Optional[List[str]] = None) -> Dict:
        """
        更新数据到指定日期（智能增量更新）
        
        Args:
            target_date_str: 目标日期 (YYYY-MM-DD格式)
            specific_varieties: 指定品种列表，None表示全部品种
        
        Returns:
            更新结果统计
        """
        print(f"🚀 持仓数据更新器")
        print("=" * 60)
        
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
        
        # 获取现有数据状态，智能确定起始日期
        existing_varieties, variety_info = self.get_existing_data_status()
        
        # 智能确定起始日期
        if variety_info:
            # 找到所有品种中最新的数据日期
            latest_dates = []
            for v, v_info in variety_info.items():
                if v_info.get('latest_date'):
                    latest_dates.append(v_info['latest_date'])
            
            if latest_dates:
                overall_latest = max(latest_dates)
                # 从最新日期的下一天开始更新
                start_date = overall_latest + timedelta(days=1)
                print(f"📅 检测到最新数据日期: {overall_latest.strftime('%Y-%m-%d')}")
                print(f"📅 将从 {start_date.strftime('%Y-%m-%d')} 更新到 {target_date.strftime('%Y-%m-%d')}")
                
                # 如果已经是最新，则获取最近3天的数据（用于确保数据完整性）
                if start_date >= target_date:
                    print(f"📅 数据已是最新，获取最近3天数据以确保完整性")
                    start_date = target_date - timedelta(days=3)
            else:
                # 没有日期信息，获取最近7天数据
                start_date = target_date - timedelta(days=7)
                print(f"📅 首次更新，将获取最近7天数据")
        else:
            # 没有现有数据，获取最近7天数据
            start_date = target_date - timedelta(days=7)
            print(f"📅 首次更新，将获取最近7天数据")
        
        print(f"📅 更新日期范围: {start_date.strftime('%Y-%m-%d')} ~ {target_date.strftime('%Y-%m-%d')}")
        
        # 确定要更新的品种
        if specific_varieties:
            target_symbols = [s for s in specific_varieties if s.upper() in SYMBOL_NAMES]
            print(f"🎯 指定更新品种: {len(target_symbols)} 个")
        else:
            target_symbols = list(SYMBOL_NAMES.keys())
            print(f"🎯 全品种更新: {len(target_symbols)} 个")
        
        # 生成交易日期
        trading_dates = self.generate_trading_dates(start_date, target_date)
        print(f"📅 计划处理 {len(trading_dates)} 个交易日")
        
        # 执行更新 - 基于主力合约获取持仓数据
        processed_count = 0
        
        print(f"\n🔄 开始基于主力合约获取持仓数据...")

        # 1. 确认主力合约：本地库优先 -> 基差数据补种 -> 联网查询并保存到本地
        dominant_contracts = self.resolve_dominant_contracts(start_date, target_date, target_symbols)

        if not dominant_contracts:
            print("❌ 无法获取主力合约信息，无法更新持仓数据")
            self.update_stats["end_time"] = datetime.now()
            return

        # 如果指定了品种，只处理指定品种
        if specific_varieties:
            filtered_contracts = {}
            for symbol in specific_varieties:
                if symbol in dominant_contracts:
                    filtered_contracts[symbol] = dominant_contracts[symbol]
            dominant_contracts = filtered_contracts

        if not dominant_contracts:
            print("❌ 指定的品种都没有主力合约信息")
            self.update_stats["end_time"] = datetime.now()
            return
        
        # 2. 按交易所分流获取持仓数据：
        #    大商所 -> 东财数据中心/官网排名接口（新浪成交持仓源不覆盖大商所）
        #    其余交易所 -> 新浪成交持仓接口（原有链路）
        dce_symbols = [s for s in dominant_contracts if s in DCE_SYMBOLS]
        sina_symbols = [s for s in dominant_contracts if s not in DCE_SYMBOLS]

        dce_positioning_data = {}
        if dce_symbols:
            print(f"🔄 大商所品种走东财/官网排名接口: {len(dce_symbols)} 个")
            dce_positioning_data = self._update_dce_varieties(
                dce_symbols, dominant_contracts, trading_dates
            )

        all_positioning_data = {}
        if sina_symbols:
            print(f"🔄 其余品种走新浪成交持仓接口: {len(sina_symbols)} 个")
            sina_contracts = {s: dominant_contracts[s] for s in sina_symbols}
            all_positioning_data = self.fetch_positioning_data_by_contracts(
                sina_contracts, start_date, target_date
            )

        print(f"\n💾 开始保存品种数据...")

        # 3. 保存新浪链路数据（DataFrame 列表 -> 分类型 CSV）
        for symbol, symbol_data in all_positioning_data.items():
            print(f"\n  处理品种: {symbol} ({SYMBOL_NAMES.get(symbol, symbol)})")

            if symbol_data:
                success, record_count = self.save_positioning_data(symbol, symbol_data)
                if success:
                    processed_count += 1
                    print(f"    ✅ {symbol}: 成功保存 {record_count} 条记录")
                    self.update_stats["updated_varieties"].append(symbol)
                else:
                    print(f"    ❌ {symbol}: 保存失败")
                    self.update_stats["failed_varieties"].append(symbol)
            else:
                print(f"    ❌ {symbol}: 无有效数据")
                self.update_stats["failed_varieties"].append(symbol)

        # 4. 保存大商所链路数据（会员对象列表 -> 分类型 CSV，自动增量合并去重）
        for symbol, new_data in dce_positioning_data.items():
            print(f"\n  处理品种: {symbol} ({SYMBOL_NAMES.get(symbol, symbol)})")

            has_rows = any(new_data.get(k) for k in ("long_positions", "short_positions", "volume_rankings"))
            if not has_rows:
                print(f"    ❌ {symbol}: 无有效数据")
                self.update_stats["failed_varieties"].append(symbol)
                continue

            if self.save_variety_data(symbol, new_data):
                processed_count += 1
        
        # 完成统计
        self.update_stats["end_time"] = datetime.now()
        
        print(f"\n📊 更新完成统计:")
        print(f"  ✅ 成功更新品种: {len(self.update_stats['updated_varieties'])} 个")
        print(f"  🆕 新增品种: {len(self.update_stats['new_varieties'])} 个")
        print(f"  ❌ 失败品种: {len(self.update_stats['failed_varieties'])} 个")
        print(f"  ⏭️ 跳过品种: {len(self.update_stats['skipped_varieties'])} 个")
        print(f"  📈 新增记录总数: {self.update_stats['total_new_records']} 条")
        print(f"  ⏱️ 耗时: {(self.update_stats['end_time'] - self.update_stats['start_time']).total_seconds():.1f} 秒")
        
        if self.update_stats["failed_varieties"]:
            print(f"  ⚠️ 失败品种列表: {', '.join(self.update_stats['failed_varieties'])}")
        
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
    print("=" * 80)
    print("🎯 持仓席位数据更新器")
    print("=" * 80)
    
    updater = PositioningDataUpdater()
    
    # 获取现有数据状态
    print("\n🔍 正在检查现有数据状态...")
    varieties, info = updater.get_existing_data_status()
    
    print(f"\n📦 已有品种数量: {len(varieties)} 个")
    if varieties:
        print(f"   品种列表: {', '.join(sorted(varieties)[:20])}{'...' if len(varieties) > 20 else ''}")
        
        # 显示最新日期
        if info:
            latest_dates = []
            for v, v_info in info.items():
                if v_info.get('latest_date'):
                    latest_dates.append(v_info['latest_date'])
            if latest_dates:
                overall_latest = max(latest_dates)
                print(f"📅 当前最新数据日期: {overall_latest.strftime('%Y-%m-%d')}")
    else:
        print("📅 当前暂无数据")
    
    # 用户输入更新参数
    print("\n" + "=" * 80)
    print("请输入更新参数:")
    print("-" * 80)
    
    # 输入目标日期
    default_date = datetime.now().strftime('%Y-%m-%d')
    target_date_input = input(f"📅 目标日期 (格式: YYYY-MM-DD, 直接回车使用今天 {default_date}): ").strip()
    target_date = target_date_input if target_date_input else default_date
    
    # 验证日期格式
    try:
        datetime.strptime(target_date, '%Y-%m-%d')
    except ValueError:
        print(f"❌ 日期格式错误，使用默认日期: {default_date}")
        target_date = default_date
    
    # 输入品种
    varieties_input = input(f"🎯 要更新的品种 (输入品种代码用逗号分隔，如 RB,CU,AL；直接回车更新全部): ").strip()
    
    if varieties_input:
        specific_varieties = [v.strip().upper() for v in varieties_input.split(',')]
        print(f"\n✅ 将更新指定品种: {', '.join(specific_varieties)}")
    else:
        specific_varieties = None
        print(f"\n✅ 将更新所有品种")
    
    # 确认
    print("\n" + "=" * 80)
    print(f"📋 更新配置:")
    print(f"   目标日期: {target_date}")
    print(f"   更新品种: {'全部' if not specific_varieties else ', '.join(specific_varieties)}")
    print(f"   更新模式: 智能增量更新（自动从最新数据补全到目标日期）")
    print("=" * 80)
    
    confirm = input("\n确认开始更新？(y/N): ").strip().lower()
    if confirm != 'y':
        print("❌ 已取消更新")
        return
    
    # 执行更新
    print("\n🚀 开始更新...")
    result = updater.update_to_date(target_date, specific_varieties=specific_varieties)
    
    print(f"\n" + "=" * 80)
    print("🎯 更新完成!")
    print("=" * 80)

if __name__ == "__main__":
    main()
