#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓单数据更新器（交易所仓单日报版）

数据来源：不再使用东财“库存”接口（futures_inventory_em，该接口返回的是库存而非仓单），
改为各交易所官方“仓单日报”：
  - 上期所: ak.futures_shfe_warehouse_receipt(date)   -> {中文品种名: DataFrame}
  - 大商所: ak.futures_warehouse_receipt_dce(date)    -> DataFrame
  - 郑商所: ak.futures_warehouse_receipt_czce(date)   -> {品种代码: DataFrame}
  - 广期所: ak.futures_gfex_warehouse_receipt(date)   -> {品种代码: DataFrame}

每个交易日取该品种全部交割仓库的“今日仓单量 / 当日增减”合计后入库。
历史最早仅回补近若干交易日（交易所官网通常仅提供近期文件），支持增量更新。
"""
import akshare as ak
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import time
import random
from typing import Dict, List, Optional, Tuple

# 品种映射配置（与库存相同）
SYMBOL_MAPPING = {
    'A': '豆一', 'AG': '沪银', 'AL': '沪铝', 'AO': '氧化铝', 'AP': '苹果',
    'AU': '沪金', 'B': '豆二', 'BR': '丁二烯橡胶', 'BU': '沥青', 'C': '玉米',
    'CF': '郑棉', 'CJ': '红枣', 'CS': '玉米淀粉', 'CU': '沪铜', 'CY': '棉纱',
    'EB': '苯乙烯', 'EG': '乙二醇', 'FG': '玻璃', 'FU': '燃油', 'HC': '热卷',
    'I': '铁矿石', 'J': '焦炭', 'JD': '鸡蛋', 'JM': '焦煤', 'L': '塑料',
    'LC': '碳酸锂', 'LG': '原木', 'LH': '生猪', 'LU': '低硫燃料油', 'M': '豆粕',
    'MA': '甲醇', 'NI': '镍', 'NR': '20号胶', 'OI': '菜油', 'P': '棕榈',
    'PB': '沪铅', 'PF': '短纤', 'PG': '液化石油气', 'PK': '花生', 'PP': '聚丙烯',
    'PR': '瓶片', 'PS': '多晶硅', 'PTA': 'PTA', 'PX': '对二甲苯', 'RB': '螺纹钢',
    'RM': '菜粕', 'RS': '菜籽', 'RU': '橡胶', 'SA': '纯碱', 'SF': '硅铁',
    'SH': '烧碱', 'SI': '工业硅', 'SM': '锰硅', 'SN': '锡', 'SP': '纸浆',
    'SR': '白糖', 'SS': '不锈钢', 'TA': 'PTA', 'UR': '尿素', 'V': 'PVC',
    'WR': '线材', 'Y': '豆油', 'ZN': '沪锌', 'ZC': '动力煤'
}

# 交易所归属（akshare 仓单日报按交易所提供）
SHFE_SYMBOLS = {'AG', 'AL', 'AO', 'AU', 'BR', 'BU', 'CU', 'FU', 'HC', 'NI',
                'PB', 'RB', 'RU', 'SN', 'SP', 'SS', 'WR', 'ZN'}
DCE_SYMBOLS = {'A', 'B', 'C', 'CS', 'EB', 'EG', 'I', 'J', 'JD', 'JM', 'L',
               'LG', 'LH', 'M', 'P', 'PG', 'PP', 'RR', 'V', 'Y'}
CZCE_SYMBOLS = {'AP', 'CF', 'CJ', 'CY', 'FG', 'MA', 'OI', 'PF', 'PK', 'PR',
                'PTA', 'PX', 'RM', 'RS', 'SA', 'SF', 'SH', 'SM', 'SR', 'TA',
                'UR', 'ZC'}
GFEX_SYMBOLS = {'LC', 'PS', 'SI'}
# akshare 未提供仓单日报的交易所（如能源中心 LU/NR 等），无法获取官方仓单
UNSUPPORTED_SYMBOLS = {'LU', 'NR', 'SC', 'BC'}


def exchange_of_symbol(symbol: str) -> Optional[str]:
    """返回品种所在交易所（用于仓单日报取数），未知返回 None"""
    s = symbol.upper()
    if s in SHFE_SYMBOLS:
        return 'SHFE'
    if s in DCE_SYMBOLS:
        return 'DCE'
    if s in CZCE_SYMBOLS:
        return 'CZCE'
    if s in GFEX_SYMBOLS:
        return 'GFEX'
    return None

# 上期所日报字典的键是中文品种名（如“铜”“螺纹钢”），给出短名匹配
SHFE_VARNAME_RULES = {
    'CU': '铜', 'AL': '铝', 'ZN': '锌', 'PB': '铅', 'NI': '镍', 'SN': '锡',
    'AU': '金', 'AG': '银', 'RB': '螺纹', 'WR': '线材', 'HC': '热卷',
    'FU': '燃油', 'BU': '沥青', 'RU': '橡胶', 'SS': '不锈钢',
    'AO': '氧化铝', 'BR': '丁二烯', 'SP': '漂针',
}

# 首次更新时回补的历史交易（会话）数
FIRST_RUN_SESSIONS = 30


def _safe_sum(series: pd.Series) -> float:
    """将可能含 '--'/','/NaN 的序列转为数值并求和"""
    numeric = pd.to_numeric(series, errors='coerce')
    return float(numeric.sum(skipna=True))


def _summary_row_mask(df: pd.DataFrame) -> pd.Series:
    """标记含“合计/小计/总计/汇总”字样的行（任意列命中），这些行不得参与累加"""
    mask = pd.Series(False, index=df.index)
    for col in df.columns:
        try:
            mask |= df[col].astype(str).str.contains('合计|小计|总计|汇总', regex=True, na=False)
        except Exception:
            continue
    return mask


def _weekdays_until(end: datetime, count: int) -> List[str]:
    """返回截至 end 日期的最近 count 个工作日（YYYYMMDD，升序）"""
    days = []
    cur = end
    while len(days) < count:
        if cur.weekday() < 5:
            days.append(cur)
        cur -= timedelta(days=1)
    days.reverse()
    return [d.strftime('%Y%m%d') for d in days]


class ReceiptDataUpdater:
    """仓单数据更新器"""

    def __init__(self, database_path: str = "qihuo/database/receipt"):
        """
        初始化仓单数据更新器

        Args:
            database_path: 数据库路径
        """
        self.base_dir = Path(database_path)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # 日期 x 交易所 的当日仓单缓存，避免同一交易日重复抓取
        self._day_cache: Dict[Tuple[str, str], Optional[Dict[str, Dict[str, float]]]] = {}

        self.update_stats = {
            "start_time": None,
            "end_time": None,
            "target_date": None,
            "updated_varieties": [],
            "new_varieties": [],
            "failed_varieties": [],
            "skipped_varieties": [],
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
        print("🔍 检查现有仓单数据状态...")

        varieties = []
        variety_info = {}

        if not self.base_dir.exists():
            return varieties, variety_info

        # 扫描品种文件夹
        variety_folders = [d for d in self.base_dir.iterdir() if d.is_dir()]
        print(f"📂 发现 {len(variety_folders)} 个品种文件夹")

        for folder in variety_folders:
            variety = folder.name
            receipt_file = folder / "receipt.csv"

            if receipt_file.exists():
                try:
                    df = pd.read_csv(receipt_file)
                    if not df.empty and 'date' in df.columns:
                        df['date'] = pd.to_datetime(df['date'])

                        # 兼容不同列名
                        record_col = 'receipt'
                        if 'receipt' not in df.columns and 'value' in df.columns:
                            record_col = 'value'

                        if record_col in df.columns:
                            varieties.append(variety)
                            variety_info[variety] = {
                                "records": len(df),
                                "latest_date": df['date'].max(),
                                "earliest_date": df['date'].min()
                            }
                            print(f"   ✅ {variety}: {len(df)} 条记录，最新 {df['date'].max().strftime('%Y-%m-%d')}")
                        else:
                            print(f"   ⚠️ {variety}: 缺少数据列")
                except Exception as e:
                    print(f"   ⚠️ {variety}: 读取失败 - {str(e)}")

        print(f"\n📊 总计: {len(varieties)} 个有效品种")
        return varieties, variety_info

    # ---------- 按交易所抓取单个交易日的仓单总量 ----------

    def _receipt_day(self, exchange: str, date_compact: str) -> Optional[Dict[str, Dict[str, float]]]:
        """
        抓取指定交易所、指定交易日的全部品种仓单合计。

        Returns:
            {品种代码: {"receipt": 今日仓单量, "change": 当日增减}}；非交易日/失败返回 None
        """
        key = (exchange, date_compact)
        if key in self._day_cache:
            return self._day_cache[key]

        result: Dict[str, Dict[str, float]] = {}
        try:
            if exchange == 'DCE':
                result = self._parse_dce(date_compact)
            elif exchange == 'CZCE':
                result = self._parse_czce(date_compact)
            elif exchange == 'SHFE':
                result = self._parse_shfe(date_compact)
            elif exchange == 'GFEX':
                result = self._parse_gfex(date_compact)
            else:
                self._day_cache[key] = None
                return None
        except Exception as e:
            # 非交易日/官网无文件等，不重试，直接视为该日无数据
            result = {}

        # 无任何品种返回时同样标记为“该日无数据”
        self._day_cache[key] = result if result else None
        return self._day_cache[key]

    def _parse_dce(self, date_compact: str) -> Dict[str, Dict[str, float]]:
        """大商所仓单日报 DataFrame：按品种代码汇总所有仓库"""
        raw = ak.futures_warehouse_receipt_dce(date=date_compact)
        if raw is None or raw.empty:
            return {}

        result: Dict[str, Dict[str, float]] = {}
        code_col, name_col = '品种代码', '品种名称'
        receipt_col = next((c for c in raw.columns if '今日仓单' in str(c)), None)
        change_col = next((c for c in raw.columns if ('增减' in str(c) or '变化' in str(c))), None)

        # 排除“小计/合计/总计”行（任意列命中即跳过），避免重复计入
        summary_mask = _summary_row_mask(raw)

        for _, row in raw.iterrows():
            if summary_mask.get(row.name, False):
                continue
            code = str(row.get(code_col, '') or '').strip().upper()
            name = str(row.get(name_col, '') or '').strip()
            if (not code or code in ('总计', '小计', '合计')
                    or name in ('总计', '小计', '合计') or '小计' in name):
                continue
            rec = result.setdefault(code, {"receipt": 0.0, "change": 0.0})
            if receipt_col is not None:
                rec["receipt"] += _safe_sum(pd.Series([row.get(receipt_col)]))
            if change_col is not None:
                rec["change"] += _safe_sum(pd.Series([row.get(change_col)]))
        return result

    def _parse_czce(self, date_compact: str) -> Dict[str, Dict[str, float]]:
        """郑商所仓单日报 dict（键为品种代码）：对每个品种的仓库行求和"""
        big = ak.futures_warehouse_receipt_czce(date=date_compact)
        if not big:
            return {}

        result: Dict[str, Dict[str, float]] = {}
        for code, inner in big.items():
            if inner is None or inner.empty:
                continue
            # 跳过“合计/小计/总计/汇总”行（任意列命中），避免重复计入
            clean = inner.loc[~_summary_row_mask(inner)]
            receipt_col = next((c for c in clean.columns if '仓单数量' in str(c)), None)
            change_col = next((c for c in clean.columns if '当日增减' in str(c) or '增减' in str(c)), None)
            rec = result.setdefault(str(code).strip().upper(), {"receipt": 0.0, "change": 0.0})
            if receipt_col is not None:
                rec["receipt"] += _safe_sum(clean[receipt_col])
            if change_col is not None:
                rec["change"] += _safe_sum(clean[change_col])
        return result

    def _parse_shfe(self, date_compact: str) -> Dict[str, Dict[str, float]]:
        """上期所仓单日报 dict（键为中文品种名）：按短名规则匹配到品种代码并求和"""
        big = ak.futures_shfe_warehouse_receipt(date=date_compact)
        if not big:
            return {}

        result: Dict[str, Dict[str, float]] = {}
        for code, short in SHFE_VARNAME_RULES.items():
            # 找到匹配的中文品种名（注意避免“铜”误匹配到“氧化铝”等情况：用集合排除）
            matched_name = None
            for varname in big.keys():
                vn = str(varname)
                if short in vn and code not in ('CU',):
                    matched_name = varname
                    break
                if code == 'CU' and vn == '铜':
                    matched_name = varname
                    break
            if matched_name is None:
                # 上期所某日可能缺该品种（零仓单时部分品种不出现），跳过
                continue
            inner = big[matched_name]
            if inner is None or inner.empty:
                continue
            receipt_col = next((c for c in inner.columns if 'WRTWGHTS' in str(c).upper()), None)
            change_col = next((c for c in inner.columns if 'WRTCHANGE' in str(c).upper()), None)
            rec = result.setdefault(code, {"receipt": 0.0, "change": 0.0})
            if receipt_col is not None:
                rec["receipt"] += _safe_sum(inner[receipt_col])
            if change_col is not None:
                rec["change"] += _safe_sum(inner[change_col])
        return result

    def _parse_gfex(self, date_compact: str) -> Dict[str, Dict[str, float]]:
        """广期所仓单日报 dict（键为品种代码）"""
        big = ak.futures_gfex_warehouse_receipt(date=date_compact)
        if not big:
            return {}

        result: Dict[str, Dict[str, float]] = {}
        for code, inner in big.items():
            if inner is None or inner.empty:
                continue
            receipt_col = next((c for c in inner.columns if '今日仓单' in str(c)), None)
            change_col = next((c for c in inner.columns if '增减' in str(c) or '变化' in str(c)), None)
            rec = result.setdefault(str(code).strip().upper(), {"receipt": 0.0, "change": 0.0})
            if receipt_col is not None:
                rec["receipt"] += _safe_sum(inner[receipt_col])
            if change_col is not None:
                rec["change"] += _safe_sum(inner[change_col])
        return result

    # ---------- 品种级取数 ----------

    def fetch_variety_history(self, symbol: str, exchange: str, target_date: datetime) -> Optional[pd.DataFrame]:
        """
        获取某品种从本地最新日期（或回补窗口）到目标日期的仓单数据。

        Returns:
            规范化的 DataFrame(date, receipt, change)；无新数据返回 None
        """
        # 计算需要抓取的日期
        file = self.base_dir / symbol / "receipt.csv"
        latest = None
        if file.exists():
            try:
                old = pd.read_csv(file)
                if not old.empty and 'date' in old.columns:
                    latest = pd.to_datetime(old['date']).max()
            except Exception:
                latest = None

        if latest is not None:
            if latest.date() >= target_date.date():
                return None
            dates = []
            cur = latest + timedelta(days=1)
            while cur.date() <= target_date.date():
                if cur.weekday() < 5:
                    dates.append(cur.strftime('%Y%m%d'))
                cur += timedelta(days=1)
        else:
            print(f"    📅 首次更新，回补最近 {FIRST_RUN_SESSIONS} 个交易日仓单")
            dates = _weekdays_until(target_date, FIRST_RUN_SESSIONS)

        if not dates:
            return None

        rows = []
        for date_compact in dates:
            day = self._receipt_day(exchange, date_compact)
            if day is None:
                continue  # 非交易日或官网该日无数据
            rec = day.get(symbol.upper())
            if rec is None:
                continue  # 该交易所当日无此品种仓单行
            rows.append({
                "date": f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}",
                "receipt": rec["receipt"],
                "change": rec["change"],
            })
            time.sleep(random.uniform(0.2, 0.6))  # 控制抓取节奏

        if not rows:
            return None
        return pd.DataFrame(rows, columns=['date', 'receipt', 'change'])

    def update_to_date(self, target_date_str: str, specific_varieties: Optional[List[str]] = None) -> Dict:
        """
        智能增量更新到指定日期

        Args:
            target_date_str: 目标日期 (YYYY-MM-DD格式)
            specific_varieties: 指定品种列表，None表示全部品种

        Returns:
            更新统计信息
        """
        self.update_stats["start_time"] = datetime.now()
        self.update_stats["target_date"] = target_date_str

        target_date = datetime.strptime(target_date_str, '%Y-%m-%d')

        print(f"\n🎯 目标日期: {target_date_str}")
        print("=" * 80)

        # 确定要更新的品种
        if specific_varieties:
            varieties_to_update = [(code.upper(), SYMBOL_MAPPING.get(code.upper(), code))
                                   for code in specific_varieties if code.upper() in SYMBOL_MAPPING]
        else:
            varieties_to_update = list(SYMBOL_MAPPING.items())

        # 剔除不支持交易所的品种（akshare 无对应官方仓单接口）
        supported = []
        unsupported = []
        for symbol, cn in varieties_to_update:
            ex = exchange_of_symbol(symbol)
            if ex:
                supported.append((symbol, cn, ex))
            else:
                unsupported.append(symbol)

        if unsupported:
            print(f"⚠️ 以下品种 akshare 暂无官方仓单接口，跳过: {', '.join(sorted(unsupported))}")

        print(f"📋 计划更新 {len(supported)} 个品种（交易所官方仓单日报）")
        print("=" * 80)

        # 更新每个品种
        for idx, (symbol, series_cn, exchange) in enumerate(supported, 1):
            print(f"\n[{idx}/{len(supported)}] 处理品种: {symbol} ({series_cn}, {exchange})")
            print("-" * 80)

            variety_dir = self.base_dir / symbol
            variety_dir.mkdir(parents=True, exist_ok=True)
            receipt_file = variety_dir / "receipt.csv"

            # 获取新数据
            new_data = self.fetch_variety_history(symbol, exchange, target_date)

            if new_data is None:
                print(f"   ⏭️ 跳过 {symbol}（数据已最新或无新仓单记录）")
                self.update_stats["skipped_varieties"].append(symbol)
                continue

            # 智能合并：如果已有数据，进行合并
            if receipt_file.exists():
                try:
                    old_data = pd.read_csv(receipt_file)
                    if 'receipt' not in old_data.columns and 'value' in old_data.columns:
                        old_data = old_data.rename(columns={'value': 'receipt'})
                    old_data['date'] = pd.to_datetime(old_data['date'])
                    if 'receipt' in old_data.columns:
                        old_data['receipt'] = pd.to_numeric(old_data['receipt'], errors='coerce')
                    else:
                        old_data['receipt'] = 0
                    if 'change' in old_data.columns:
                        old_data['change'] = pd.to_numeric(old_data['change'], errors='coerce')
                    else:
                        old_data['change'] = 0
                    old_data = old_data[['date', 'receipt', 'change']]

                    combined = pd.concat([old_data, new_data], ignore_index=True)
                    combined = combined.drop_duplicates(subset=['date'], keep='last')
                    combined = combined.sort_values('date').reset_index(drop=True)

                    new_records = len(combined) - len(old_data)
                    combined.to_csv(receipt_file, index=False, encoding='utf-8-sig')

                    print(f"   ✅ 更新成功: 新增 {new_records} 条记录（共 {len(combined)} 条）")
                    self.update_stats["updated_varieties"].append(symbol)
                    self.update_stats["total_new_records"] += new_records

                except Exception as e:
                    print(f"   ❌ 更新失败: {str(e)}")
                    self.update_stats["failed_varieties"].append(symbol)
            else:
                # 首次创建
                try:
                    new_data.to_csv(receipt_file, index=False, encoding='utf-8-sig')
                    print(f"   🆕 创建新文件: {len(new_data)} 条记录")
                    self.update_stats["new_varieties"].append(symbol)
                    self.update_stats["total_new_records"] += len(new_data)
                except Exception as e:
                    print(f"   ❌ 创建失败: {str(e)}")
                    self.update_stats["failed_varieties"].append(symbol)

        self.update_stats["end_time"] = datetime.now()

        # 打印统计
        print("\n" + "=" * 80)
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
    print("📜 仓单数据更新器（交易所官方仓单日报）")
    print("=" * 80)

    updater = ReceiptDataUpdater()

    # 获取现有数据状态
    print("\n🔍 正在检查现有数据状态...")
    varieties, info = updater.get_existing_data_status()

    print(f"\n📦 已有品种数量: {len(varieties)} 个")
    if varieties:
        print(f"   品种列表: {', '.join(sorted(varieties)[:20])}{'...' if len(varieties) > 20 else ''}")

        # 显示最新日期
        if info:
            latest_dates = {}
            for v, v_info in info.items():
                if v_info.get('latest_date'):
                    latest_dates[v] = v_info['latest_date']
            if latest_dates:
                overall_latest = max(latest_dates.values())
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
    print("=" * 80)

    confirm = input("\n确认开始更新？(y/N): ").strip().lower()
    if confirm != 'y':
        print("❌ 已取消更新")
        return

    # 执行更新
    print("\n🚀 开始更新...")
    result = updater.update_to_date(target_date, specific_varieties)

    print(f"\n" + "=" * 80)
    print("🎯 更新完成!")
    print("=" * 80)


if __name__ == "__main__":
    main()
