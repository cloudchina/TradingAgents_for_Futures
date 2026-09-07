#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期限结构数据更新器
基于期限结构数据更新程序，支持多交易所数据获取和增量更新
"""

import akshare as ak
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import time
import random
import json
import warnings
import re
import requests
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings('ignore')


# ────────────────────────────────────────────────────────────── #
# 大商所降级数据源常量
# 2025 年起 www.dce.com.cn 全站启用瑞数(RiverSecurity)动态 JS 反爬，
# 普通 HTTP 请求只能拿到 412 挑战页（akshare 的 get_futures_daily/DCE
# 分支因此报 "Expecting value: line 1 column 1"，同 B8 持仓排名问题）。
# 降级方案：新浪“单合约日K”接口（保留已交割合约历史）按交割月份逐合约
# 组装出与官网 get_futures_daily 同构的“全合约日报”，再走统一处理管线。
# ────────────────────────────────────────────────────────────── #

# 大商所受支持的品种（与 backend/config/commodities.yaml exchange: DCE 一致）
DCE_SUPPORTED_SYMBOLS = (
    'A', 'B', 'C', 'CS', 'EB', 'EG', 'I', 'J', 'JD', 'JM',
    'L', 'LH', 'M', 'P', 'PG', 'PP', 'V', 'Y',
)

# 新浪期货单合约日K（JSONP）：一次返回该合约上市以来全部日线（含已交割）
_SINA_DAILY_KLINE_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_t=/InnerFuturesNewService.getDailyKLine"
)

# 东财静态合约目录：msgid=114 为大商所品种表，114_<vtype> 为该品种当前挂牌合约
_EM_REDIS_STATIC_URL = "https://futsse-static.eastmoney.com/redis"
_EM_DCE_MARKET_ID = "114"

# 旧版本误存的“主力连续”脏行，如 EG0 / JD0 / MA0（单行非期限结构）
_CONTINUOUS_SYMBOL_RE = re.compile(r'^[A-Za-z]+0$')

class TermStructureUpdater:
    """期限结构数据更新器"""
    
    def __init__(self, database_path: str = "qihuo/database/term_structure"):
        """
        初始化期限结构数据更新器
        
        Args:
            database_path: 数据库路径
        """
        self.base_dir = Path(database_path)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        # 交易所配置
        self.exchanges = [
            {"market": "DCE", "name": "大商所"},
            {"market": "CZCE", "name": "郑商所"},
            {"market": "SHFE", "name": "上期所"},
            {"market": "INE", "name": "上海国际能源交易中心"},
            {"market": "GFEX", "name": "广期所"}
        ]

        # DCE 降级数据源缓存（单次运行内复用）
        self._em_dce_codes_cache: Optional[Dict[str, List[str]]] = None
        
        self.update_stats = {
            "start_time": None,
            "end_time": None,
            "target_date": None,
            "update_days": 0,
            "updated_varieties": [],
            "failed_varieties": [],
            "skipped_varieties": [],
            "new_varieties": [],
            "total_new_records": 0,
            "exchange_stats": {},
            "error_messages": []
        }
    
    def get_existing_data_status(self) -> Tuple[List[str], Dict]:
        """
        获取现有数据状态
        
        Returns:
            varieties: 现有品种列表
            variety_info: 各品种详细信息
        """
        print("🔍 检查现有期限结构数据状态...")
        
        varieties = []
        variety_info = {}
        
        if not self.base_dir.exists():
            return [], {}
        
        variety_folders = [d for d in self.base_dir.iterdir() if d.is_dir()]
        print(f"📂 发现 {len(variety_folders)} 个品种文件夹")
        
        for folder in variety_folders:
            variety = folder.name
            ts_file = folder / "term_structure.csv"
            
            if ts_file.exists():
                try:
                    df = pd.read_csv(ts_file, encoding='utf-8')
                    if len(df) > 0 and 'date' in df.columns:
                        # 处理日期列（可能是多种格式）
                        try:
                            df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
                        except Exception:
                            df['date'] = pd.to_datetime(df['date'])
                        
                        variety_latest = df['date'].max()
                        variety_earliest = df['date'].min()
                        record_count = len(df)
                        
                        variety_info[variety] = {
                            "earliest_date": variety_earliest,
                            "latest_date": variety_latest,
                            "record_count": record_count,
                            "file_path": ts_file
                        }
                        
                        varieties.append(variety)
                        print(f"  {variety}: {record_count} 条记录 ({variety_earliest.strftime('%Y-%m-%d')} ~ {variety_latest.strftime('%Y-%m-%d')})")
                        
                except Exception as e:
                    print(f"  ❌ {variety}: 读取失败 - {str(e)[:50]}")
                    self.update_stats["error_messages"].append(f"{variety}: 数据读取失败 - {str(e)}")
        
        print(f"\n📊 总计: {len(varieties)} 个有效品种")
        return varieties, variety_info
    
    def calculate_roll_yield(self, current_contract: str, next_contract: str, current_price: float, next_price: float) -> float:
        """
        计算展期收益率
        
        Args:
            current_contract: 当前合约
            next_contract: 下一个合约
            current_price: 当前合约价格
            next_price: 下一个合约价格
        
        Returns:
            展期收益率
        """
        try:
            if current_price <= 0 or next_price <= 0:
                return 0.0
            
            # 提取合约月份
            current_month = int(current_contract[-4:])
            next_month = int(next_contract[-4:])
            
            # 计算月份差
            if next_month > current_month:
                month_diff = next_month - current_month
            else:
                # 跨年情况
                month_diff = (next_month + 1200) - current_month
            
            if month_diff == 0:
                return 0.0
            
            # 计算年化收益率
            roll_yield = ((next_price / current_price - 1) / month_diff) * 12
            return roll_yield
            
        except Exception:
            return 0.0

    # ------------------------------------------------------------------ #
    # 大商所期限结构 - 降级数据源（新浪单合约日K 组装全合约日报）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _add_months(dt: datetime, months: int) -> datetime:
        """月份加减（安全处理跨年）"""
        total = dt.year * 12 + (dt.month - 1) + months
        return datetime(total // 12, total % 12 + 1, 1)

    def _sina_contract_kline(self, symbol: str) -> Optional[List[dict]]:
        """
        新浪单合约日K。

        Args:
            symbol: 合约代码（大小写均可，如 EG2609 / eg2609）

        Returns:
            日线列表 [{d,o,h,l,c,v,p,s}, ...]；合约不存在返回 []；
            网络/风控等异常返回 None（由调用方决定是否终止）。
        """
        params = {"symbol": symbol, "type": "2021_4_12"}
        for attempt in range(3):
            try:
                resp = requests.get(_SINA_DAILY_KLINE_URL, params=params, timeout=15)
                text = resp.text
                left = text.find('(')
                right = text.rfind(')')
                if left == -1 or right <= left:
                    return None  # 非 JSONP 响应（可能被限流/反爬）
                body = text[left + 1:right].strip()
                if body in ("null", "undefined", ""):
                    return []  # 该月份合约从未挂牌
                data = json.loads(body)
                return data if isinstance(data, list) else []
            except json.JSONDecodeError:
                return None
            except Exception:
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                return None
        return None

    def _em_dce_contract_codes(self) -> Dict[str, List[str]]:
        """
        东财静态目录：大商所各品种“当前挂牌”的真实合约代码。
        msgid=114 -> 品种表(vcode, vtype)；msgid=114_<vtype> -> 该品种合约代码列表。
        仅用于推导候选交割月份（减少逐月盲扫），失败时返回 {}（退化为全月份扫描）。
        """
        if self._em_dce_codes_cache is not None:
            return self._em_dce_codes_cache

        result: Dict[str, List[str]] = {}
        try:
            resp = requests.get(_EM_REDIS_STATIC_URL,
                                params={"msgid": _EM_DCE_MARKET_ID}, timeout=15)
            resp.raise_for_status()
            varieties = resp.json()
            vtype_map = {}
            for item in varieties or []:
                vcode = str(item.get("vcode", "")).upper()
                vtype = str(item.get("vtype", ""))
                if vcode and vtype:
                    vtype_map[vcode] = vtype

            for variety in DCE_SUPPORTED_SYMBOLS:
                vtype = vtype_map.get(variety)
                if not vtype:
                    continue
                try:
                    r2 = requests.get(_EM_REDIS_STATIC_URL,
                                      params={"msgid": f"{_EM_DCE_MARKET_ID}_{vtype}"},
                                      timeout=15)
                    r2.raise_for_status()
                    codes = []
                    for it in r2.json() or []:
                        code = str(it.get("code", "")).strip()
                        # 只要 YYMM 真实合约，跳过 egm/egs 等连续代码
                        if re.match(r'^[A-Za-z]+\d{4}$', code):
                            codes.append(code.upper())
                    if codes:
                        result[variety] = sorted(set(codes))
                except Exception:
                    continue
                time.sleep(0.1)
        except Exception:
            pass
        self._em_dce_codes_cache = result
        return result

    def _dce_candidate_contracts(self, start_dt: datetime, end_dt: datetime) -> Dict[str, List[str]]:
        """
        推导每个 DCE 品种在 [start, end] 区间“需要抓取的合约代码集合”：
        1. 区间内可能已交割、但新浪仍保留历史的近月合约；
        2. 当前仍在挂牌的远月合约（截至 end 后约一年，与官网“全部月份合约”口径一致）。
        """
        em_map = self._em_dce_contract_codes()  # 可能为空字典

        def months_range(frm: datetime, to: datetime):
            """返回 [(year, month), ...]（含首尾），to 不超过两年半，避免无限远扫"""
            to = min(to, self._add_months(end_dt, 24))
            cur = datetime(frm.year, frm.month, 1)
            out = []
            while cur <= to:
                out.append((cur.year, cur.month))
                cur = self._add_months(cur, 1)
            return out

        candidates: Dict[str, List[str]] = {}
        for variety in DCE_SUPPORTED_SYMBOLS:
            em_codes = em_map.get(variety, [])

            # 从当前挂牌合约反推该品种的交割月份规律（如 A 只在 1/3/5/7/9/11 交割）
            pattern = {int(code[-2:]) for code in em_codes if len(code) >= 4}
            if not pattern:
                pattern = set(range(1, 13))  # EM 目录不可用 → 逐月盲扫兜底

            # 远月上限：优先取当前挂牌最远合约月份；目录失效时取 end+13 个月
            max_far_ym = None
            for code in em_codes:
                ym = int(code[-4:])
                max_far_ym = ym if max_far_ym is None else max(max_far_ym, ym)
            far_cap = end_dt
            if max_far_ym:
                try:
                    far_cap = datetime(max_far_ym // 100 + 2000,
                                       max_far_ym % 100, 1)
                except ValueError:
                    pass
            else:
                far_cap = self._add_months(end_dt, 13)

            code_set = set(em_codes)  # 当前挂牌的直接纳入
            # 区间 [start .. 远月cap] 内按交割月份规律生成代码
            for yy, mm in months_range(start_dt, far_cap):
                if mm in pattern:
                    code_set.add(f"{variety}{yy % 100:02d}{mm:02d}")

            def ym_key(code: str) -> int:
                m = re.search(r'(\d{4})$', code)
                return int(m.group(1)) if m else 0

            candidates[variety] = sorted(code_set, key=ym_key)
        return candidates

    def _fetch_dce_daily_from_sina(self, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """
        降级链路：新浪单合约日K 逐合约抓取，组装成与官网 get_futures_daily
        同构的“全合约日报”DataFrame（symbol/date/close/volume/open_interest）。
        已交割月份新浪仍保留历史，故能还原完整多合约期限结构。

        Returns:
            DataFrame 或 None（所有品种都没有任何数据时）
        """
        start_dt = datetime.strptime(start_date, '%Y%m%d')
        end_dt = datetime.strptime(end_date, '%Y%m%d')
        print("  📡 降级数据源: 新浪单合约日K 组装大商所全合约日报...")

        candidates = self._dce_candidate_contracts(start_dt, end_dt)
        records: List[dict] = []
        consecutive_errors = 0
        variety_got = {}

        for variety in sorted(candidates):
            codes = candidates[variety]
            if not codes:
                continue
            got_rows = 0
            for code in codes:
                klines = self._sina_contract_kline(code)
                if klines is None:
                    consecutive_errors += 1
                    if consecutive_errors >= 8:
                        print(f"    ⚠️ 新浪接口连续失败，停止抓取（已获取 {len(records)} 条）")
                        break
                    time.sleep(1.0)
                    continue
                consecutive_errors = 0
                if not klines:
                    continue  # 该月份从未挂牌
                for day in klines:
                    try:
                        d = datetime.strptime(day["d"], '%Y-%m-%d')
                    except (KeyError, ValueError, TypeError):
                        continue
                    if d.date() < start_dt.date() or d.date() > end_dt.date():
                        continue
                    records.append({
                        "symbol": code.upper(),
                        "date": int(d.strftime('%Y%m%d')),
                        "close": float(day.get("c") or 0),
                        "volume": float(day.get("v") or 0),
                        "open_interest": float(day.get("p") or 0),
                    })
                    got_rows += 1
                time.sleep(random.uniform(0.08, 0.2))
            if got_rows:
                variety_got[variety] = got_rows
                print(f"    ✅ {variety}: {got_rows} 条记录 ({len(codes)} 个合约)")
            if consecutive_errors >= 8:
                break

        if not records:
            print("    ❌ 大商所降级源: 未获取到任何记录")
            return None

        df = pd.DataFrame(records, columns=[
            "symbol", "date", "close", "volume", "open_interest"])
        print(f"    📊 大商所降级源共获取 {len(df)} 条记录, 覆盖品种 {sorted(variety_got)}")
        return df

    def fetch_exchange_data(self, exchange: Dict, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """
        获取交易所数据
        
        Args:
            exchange: 交易所配置
            start_date: 开始日期 (YYYYMMDD)
            end_date: 结束日期 (YYYYMMDD)
        
        Returns:
            数据DataFrame或None
        """
        print(f"  📡 获取 {exchange['name']} 数据 ({start_date} ~ {end_date})...")

        # 主通道：交易所官方“全合约日报”接口 get_futures_daily。
        # 一次返回该交易所全部品种的全部月份合约（多合约多行），
        # 才能计算真正的期限结构与 roll_yield。
        # 🔧 修复：此前 DCE 分支误用 futures_main_sina(f"{variety}0") 拉“主力连续”，
        #   每个交易日只有 1 行（symbol 恒为 XX0、roll_yield=0），期限结构曲线完全失真。
        df = None
        try:
            df = ak.get_futures_daily(start_date=start_date, end_date=end_date,
                                      market=exchange['market'])
            if df is None or df.empty:
                df = None
                print(f"    ⚠️ {exchange['name']}: 官网接口返回空数据")
        except Exception as e:
            df = None
            print(f"    ❌ {exchange['name']}: 官网接口获取失败 - {str(e)[:100]}")

        # 降级通道：DCE 官网全站启用瑞数动态 JS 反爬（HTTP 412），主通道基本必然失败。
        # 此时改走新浪单合约日K（保留已交割合约历史）逐合约组装“全合约日报”。
        if df is None and exchange['market'] == 'DCE':
            df = self._fetch_dce_daily_from_sina(start_date, end_date)

        if df is None:
            print(f"    ❌ {exchange['name']}: 获取失败")
            self.update_stats["error_messages"].append(
                f"{exchange['name']}: 所有数据源均获取失败")
            return None

        print(f"    ✅ {exchange['name']}: 获取到 {len(df)} 条原始记录")
        return df
    
    def process_exchange_data(self, df: pd.DataFrame, exchange: Dict) -> Dict[str, pd.DataFrame]:
        """
        处理交易所数据，按品种分组
        
        Args:
            df: 原始数据
            exchange: 交易所配置
        
        Returns:
            按品种分组的数据字典
        """
        variety_data = {}
        
        try:
            # 标准化列名
            column_mapping = {
                'symbol': 'symbol', 'code': 'symbol', '合约代码': 'symbol',
                'date': 'date', '日期': 'date',
                'close': 'close', '收盘价': 'close',
                'volume': 'volume', '成交量': 'volume',
                'open_interest': 'open_interest', '持仓量': 'open_interest'
            }
            
            # 重命名列
            for old_col, new_col in column_mapping.items():
                if old_col in df.columns:
                    df = df.rename(columns={old_col: new_col})
            
            # 确保必要的列存在
            required_columns = ['symbol', 'date', 'close']
            if not all(col in df.columns for col in required_columns):
                print(f"    ❌ {exchange['name']}: 缺少必要列，跳过处理")
                return variety_data
            
            # 🔧 修复：正确处理日期（避免int64被当作纳秒时间戳）
            # 关键：统一转换为datetime类型
            if df['date'].dtype in ['int64', 'float64', 'int32']:
                # 整数类型（如20251114）：先转字符串，再指定格式解析
                df['date'] = pd.to_datetime(df['date'].astype(str), format='%Y%m%d', errors='coerce')
            elif df['date'].dtype == 'object':
                # 对象类型：可能是字符串、datetime.date等
                # 尝试智能解析（pandas会自动识别格式）
                df['date'] = pd.to_datetime(df['date'], errors='coerce')
            else:
                # 其他类型（如datetime64）：直接转换
                df['date'] = pd.to_datetime(df['date'], errors='coerce')
            
            # 过滤无效日期（NaT或早于2000年的异常数据）
            df = df[df['date'].notna()]
            df = df[df['date'] >= '2000-01-01']
            
            # 转换为YYYYMMDD格式
            df['date'] = df['date'].dt.strftime('%Y%m%d')
            
            # 按品种分组
            for symbol, group in df.groupby('symbol'):
                try:
                    # 提取品种代码（去除合约月份）
                    # 正确方法：找到第一个数字的位置，之前的部分就是品种代码
                    match = re.match(r'([A-Za-z]+)(\d+)', str(symbol))
                    if not match:
                        continue
                    variety = match.group(1).upper()  # 提取字母部分作为品种代码
                    month_code = match.group(2)  # 提取月份代码
                    
                    # 标准化合约代码：郑商所3位月份补齐为4位
                    # 如FG511 → FG2511, AP603 → AP2603
                    # 特殊处理：主力合约标识"0"保持不变
                    if month_code == '0':
                        pass  # 主力合约，保持"0"不变
                    elif len(month_code) == 3:
                        month_code = '2' + month_code  # 在第一位前加2补齐为4位
                    elif len(month_code) == 4:
                        pass  # 已经是4位，不需要处理
                    else:
                        continue  # 非3位或4位的异常格式，跳过
                    
                    # 生成标准化的合约代码
                    standardized_symbol = variety + month_code
                    
                    # 整理数据
                    processed_group = group[['date', 'symbol', 'close', 'volume', 'open_interest']].copy()
                    # 使用标准化的合约代码
                    processed_group['symbol'] = standardized_symbol
                    processed_group['close'] = pd.to_numeric(processed_group['close'], errors='coerce')
                    processed_group['volume'] = pd.to_numeric(processed_group['volume'], errors='coerce')
                    processed_group['open_interest'] = pd.to_numeric(processed_group['open_interest'], errors='coerce')
                    
                    # 去除无效数据
                    processed_group = processed_group.dropna(subset=['close'])
                    
                    if not processed_group.empty:
                        if variety not in variety_data:
                            variety_data[variety] = []
                        variety_data[variety].append(processed_group)
                        
                except Exception as e:
                    continue
            
            print(f"    📊 {exchange['name']}: 处理得到 {len(variety_data)} 个品种")
            
        except Exception as e:
            print(f"    ❌ {exchange['name']}: 数据处理失败 - {str(e)[:100]}")
        
        return variety_data
    
    def calculate_term_structure_metrics(self, variety_df: pd.DataFrame) -> pd.DataFrame:
        """
        计算期限结构指标
        
        Args:
            variety_df: 品种数据
        
        Returns:
            添加了指标的数据
        """
        try:
            # 按日期分组计算指标
            variety_df = variety_df.sort_values(['date', 'symbol'])
            variety_df['roll_yield'] = 0.0
            
            for date, date_group in variety_df.groupby('date'):
                # 按合约月份排序
                sorted_contracts = date_group.sort_values('symbol')
                
                # 计算展期收益率
                for i in range(len(sorted_contracts) - 1):
                    current_idx = sorted_contracts.index[i]
                    next_idx = sorted_contracts.index[i + 1]
                    
                    current_contract = sorted_contracts.loc[current_idx, 'symbol']
                    next_contract = sorted_contracts.loc[next_idx, 'symbol']
                    current_price = sorted_contracts.loc[current_idx, 'close']
                    next_price = sorted_contracts.loc[next_idx, 'close']
                    
                    roll_yield = self.calculate_roll_yield(
                        current_contract, next_contract,
                        current_price, next_price
                    )
                    
                    variety_df.loc[current_idx, 'roll_yield'] = roll_yield
            
            return variety_df
            
        except Exception as e:
            print(f"      ⚠️ 计算指标时出错: {str(e)[:50]}")
            if 'roll_yield' not in variety_df.columns:
                variety_df['roll_yield'] = 0.0
            return variety_df
    
    def save_variety_data(self, variety: str, new_data: pd.DataFrame, existing_info: Optional[Dict] = None, target_date: datetime = None) -> bool:
        """
        保存品种数据（智能增量更新）
        
        Args:
            variety: 品种代码
            new_data: 新数据
            existing_info: 现有数据信息
            target_date: 目标日期
        
        Returns:
            是否成功保存
        """
        try:
            # 🔒 验证数据：过滤1970年的异常数据
            new_data['date_dt'] = pd.to_datetime(new_data['date'], format='%Y%m%d', errors='coerce')
            new_data = new_data[new_data['date_dt'].notna()]  # 过滤解析失败的日期
            new_data = new_data[new_data['date_dt'] >= '2000-01-01']  # 过滤2000年之前的数据
            
            if len(new_data) == 0:
                print(f"    ⚠️ {variety}: 过滤后无有效数据（可能全是1970异常数据）")
                self.update_stats["skipped_varieties"].append(variety)
                return True
            
            variety_dir = self.base_dir / variety
            variety_dir.mkdir(parents=True, exist_ok=True)
            ts_file = variety_dir / "term_structure.csv"
            
            # 如果有现有数据，智能判断需要保存的部分
            if existing_info and ts_file.exists():
                existing_df = pd.read_csv(ts_file, encoding='utf-8')
                
                # 🔒 清理现有数据中的1970异常数据
                existing_df['date_dt'] = pd.to_datetime(existing_df['date'], format='%Y%m%d', errors='coerce')
                existing_df = existing_df[existing_df['date_dt'].notna()]
                existing_df = existing_df[existing_df['date_dt'] >= '2000-01-01']
                existing_df = existing_df.drop(columns=['date_dt'])
                
                # 🔧 修复(DCE)：旧版本曾误用 futures_main_sina 写入 symbol=XX0 的“主力连续”单行
                #   （每个交易日只有1行、roll_yield=0），不是真正的多合约期限结构。
                #   一旦本次拿到真实合约行（symbol 形如 EG2609），自动剔除文件中的 XX0 脏行，
                #   避免“主连单行 + 真实多合约”混存的脏数据。
                new_symbols = new_data['symbol'].astype(str)
                has_real_contract = new_symbols.apply(
                    lambda s: not bool(_CONTINUOUS_SYMBOL_RE.match(s))).any()
                if has_real_contract:
                    old_len = len(existing_df)
                    existing_df = existing_df[
                        ~existing_df['symbol'].astype(str).apply(
                            lambda s: bool(_CONTINUOUS_SYMBOL_RE.match(s)))]
                    if len(existing_df) != old_len:
                        print(f"    🧹 {variety}: 清理 {old_len - len(existing_df)} 条主力连续(XX0)脏数据")
                
                if len(existing_df) == 0:
                    # 如果现有数据清理后为空（含“全是 XX0 主连”的情况），视为全新写入
                    existing_info = None
                
                # 以清理后的现有数据最新日期为准做增量
                latest_date = None
                if existing_info is not None:
                    latest_series = pd.to_datetime(existing_df['date'], format='%Y%m%d', errors='coerce')
                    latest_date = latest_series.max() if len(latest_series) > 0 else None
                else:
                    latest_date = None
                
                # 🎯 智能过滤：只保留比现有最新日期更新的数据
                if latest_date:
                    filtered_new_data = new_data[new_data['date_dt'] > latest_date].copy()
                else:
                    filtered_new_data = new_data.copy()
                filtered_new_data = filtered_new_data.drop(columns=['date_dt'])
                
                if len(filtered_new_data) > 0:
                    # 合并新旧数据
                    combined_df = pd.concat([existing_df, filtered_new_data], ignore_index=True)
                    
                    # 去重（保留最新的）
                    combined_df = combined_df.drop_duplicates(subset=['date', 'symbol'], keep='last')
                    combined_df = combined_df.sort_values(['date', 'symbol'])
                    
                    # 计算实际新增
                    new_record_count = len(combined_df) - len(existing_df)
                    
                    if new_record_count > 0:
                        # 计算日期范围
                        filtered_new_data['date_dt'] = pd.to_datetime(filtered_new_data['date'], format='%Y%m%d')
                        new_min = filtered_new_data['date_dt'].min().strftime('%Y-%m-%d')
                        new_max = filtered_new_data['date_dt'].max().strftime('%Y-%m-%d')
                        
                        print(f"    ✅ {variety}: 新增 {new_record_count} 条 ({new_min} ~ {new_max})")
                        self.update_stats["updated_varieties"].append(variety)
                        self.update_stats["total_new_records"] += new_record_count
                        
                        # 保存数据
                        combined_df.to_csv(ts_file, index=False, encoding='utf-8-sig')
                        return True
                    else:
                        print(f"    ℹ️ {variety}: 去重后无新数据")
                        self.update_stats["skipped_varieties"].append(variety)
                        return True
                else:
                    if existing_info is not None and latest_date is not None:
                        print(f"    ℹ️ {variety}: 已是最新 (现有: {latest_date.strftime('%Y-%m-%d')})")
                    else:
                        print(f"    ℹ️ {variety}: 无新增数据")
                    self.update_stats["skipped_varieties"].append(variety)
                    return True
            else:
                # 新品种或无现有数据
                combined_df = new_data.drop(columns=['date_dt'])
                print(f"    ✅ {variety}: 创建 {len(combined_df)} 条记录 (新品种)")
                self.update_stats["new_varieties"].append(variety)
                self.update_stats["total_new_records"] += len(combined_df)
                
                # 保存数据
                combined_df.to_csv(ts_file, index=False, encoding='utf-8-sig')
                return True
            
        except Exception as e:
            print(f"    ❌ {variety}: 保存失败 - {str(e)}")
            self.update_stats["failed_varieties"].append(variety)
            self.update_stats["error_messages"].append(f"{variety}: 保存失败 - {str(e)}")
            return False
    
    def update_to_date(self, target_date_str: str, update_days: Optional[int] = None, specific_varieties: Optional[List[str]] = None) -> Dict:
        """
        更新数据到指定日期（支持智能增量更新）
        
        Args:
            target_date_str: 目标日期 (YYYY-MM-DD格式)
            update_days: 更新天数（可选，如果不指定则自动计算）
            specific_varieties: 指定品种列表，None表示全部品种
        
        Returns:
            更新结果统计
        """
        print(f"🚀 期限结构数据更新器")
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
        
        # 获取现有数据状态
        existing_varieties, variety_info = self.get_existing_data_status()
        
        # 智能计算更新天数
        if update_days is None:
            latest_dates = [info['latest_date'] for info in variety_info.values() if info.get('latest_date')]
            if latest_dates:
                overall_latest = max(latest_dates)
                calculated_days = (target_date.date() - overall_latest.date()).days
                
                # 🔧 修复：增加最小回溯天数，确保覆盖所有品种
                # 原因：不同品种可能更新进度不同，需要足够长的时间窗口
                min_lookback = 90  # 至少回溯90天
                update_days = max(calculated_days, min_lookback)
                
                print(f"📊 智能更新: 从 {overall_latest.strftime('%Y-%m-%d')} 更新到 {target_date_str}")
                print(f"   回溯天数: {update_days} 天 (最新品种需{calculated_days}天，保证覆盖所有品种需{min_lookback}天)")
            else:
                print(f"📊 首次更新: 获取最近90天数据")
                update_days = 90
        else:
            print(f"📊 指定天数: 更新最近 {update_days} 天")
        
        self.update_stats["update_days"] = update_days
        
        # 计算日期范围
        start_date = target_date - timedelta(days=update_days + 2)
        start_date_str = start_date.strftime('%Y%m%d')
        end_date_str = target_date.strftime('%Y%m%d')
        
        print(f"📅 更新日期范围: {start_date_str} - {end_date_str}")
        
        # 按交易所获取数据
        all_variety_data = {}
        
        for exchange in self.exchanges:
            print(f"\n🔄 处理 {exchange['name']}...")
            
            # 获取交易所数据
            exchange_df = self.fetch_exchange_data(exchange, start_date_str, end_date_str)
            if exchange_df is None:
                self.update_stats["exchange_stats"][exchange['name']] = {"status": "failed", "varieties": 0}
                continue
            
            # 处理数据
            variety_data = self.process_exchange_data(exchange_df, exchange)
            
            # 合并到总数据中
            for variety, data_list in variety_data.items():
                if specific_varieties and variety not in specific_varieties:
                    continue
                    
                if variety not in all_variety_data:
                    all_variety_data[variety] = []
                all_variety_data[variety].extend(data_list)
            
            self.update_stats["exchange_stats"][exchange['name']] = {
                "status": "success", 
                "varieties": len(variety_data)
            }
            
            # 添加延迟
            time.sleep(random.uniform(1, 2))
        
        # 处理并保存各品种数据
        print(f"\n💾 保存各品种数据...")
        processed_count = 0
        
        for variety, data_list in all_variety_data.items():
            print(f"\n  处理品种: {variety}")
            
            try:
                # 合并该品种的所有数据
                variety_df = pd.concat(data_list, ignore_index=True)
                
                # 计算期限结构指标
                variety_df = self.calculate_term_structure_metrics(variety_df)
                
                if variety_df.empty:
                    print(f"    ⚠️ {variety}: 无有效数据")
                    continue
                
                # 保存数据
                existing_info = variety_info.get(variety)
                if self.save_variety_data(variety, variety_df, existing_info, target_date):
                    processed_count += 1
                    
            except Exception as e:
                print(f"    ❌ {variety}: 处理失败 - {str(e)}")
                self.update_stats["failed_varieties"].append(variety)
                self.update_stats["error_messages"].append(f"{variety}: 处理失败 - {str(e)}")
        
        # 生成统计报告
        self.update_stats["end_time"] = datetime.now()
        elapsed_time = (self.update_stats["end_time"] - self.update_stats["start_time"]).total_seconds()
        
        print(f"\n📊 更新完成统计:")
        print(f"  ✅ 成功更新品种: {len(self.update_stats['updated_varieties'])} 个")
        print(f"  🆕 新增品种: {len(self.update_stats['new_varieties'])} 个")
        print(f"  ❌ 失败品种: {len(self.update_stats['failed_varieties'])} 个")
        print(f"  ⏭️ 跳过品种: {len(self.update_stats['skipped_varieties'])} 个")
        print(f"  📈 新增记录总数: {self.update_stats['total_new_records']} 条")
        print(f"  ⏱️ 耗时: {elapsed_time:.1f} 秒")
        
        if self.update_stats["exchange_stats"]:
            print(f"\n📋 交易所数据获取统计:")
            for exchange_name, stats in self.update_stats["exchange_stats"].items():
                status_icon = "✅" if stats["status"] == "success" else "❌"
                print(f"  {status_icon} {exchange_name}: {stats['varieties']} 个品种")
        
        if self.update_stats["error_messages"]:
            print(f"\n⚠️ 错误信息:")
            for msg in self.update_stats["error_messages"][:10]:
                print(f"  • {msg}")
        
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
        return self.update_to_date(target_date_str, specific_varieties=specific_varieties)

def main():
    """主函数"""
    import sys
    
    print("\n" + "=" * 80)
    print("🎯 期限结构数据更新器".center(76))
    print("=" * 80)
    print("\n📌 更新模式: 智能增量更新（自动从最新数据补全到目标日期）")
    print("\n请输入更新参数:\n")
    print("-" * 80)
    
    # 获取目标日期
    target_date = input(f"📅 目标日期 (格式: YYYY-MM-DD, 直接回车使用今天 {datetime.now().strftime('%Y-%m-%d')}): ").strip()
    if not target_date:
        target_date = datetime.now().strftime('%Y-%m-%d')
    
    # 获取品种列表
    varieties_input = input("🎯 要更新的品种 (输入品种代码用逗号分隔，如 RB,CU,AL；直接回车更新全部): ").strip()
    varieties = [v.strip().upper() for v in varieties_input.split(',')] if varieties_input else None
    
    print("\n" + "=" * 80)
    print(f"开始更新期限结构数据到 {target_date}")
    if varieties:
        print(f"指定品种: {', '.join(varieties)}")
    print("=" * 80 + "\n")
    
    try:
        updater = TermStructureUpdater()
        result = updater.update_to_date(target_date, specific_varieties=varieties)
        
        print("\n" + "=" * 80)
        print("✅ 更新完成！".center(76))
        print("=" * 80)
        
        input("\n按回车键退出...")
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断更新")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 更新失败: {str(e)}")
        import traceback
        traceback.print_exc()
        input("\n按回车键退出...")
        sys.exit(1)

if __name__ == "__main__":
    main()
