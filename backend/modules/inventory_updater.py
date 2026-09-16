#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
库存数据更新器
基于增量更新库存数据逻辑，支持智能数据合并
"""

import akshare as ak
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import time
import random
import json
from typing import Dict, List, Optional, Tuple
from modules.progress import ProgressReporter
from modules import variety_catalog
from loguru import logger

# 品种映射配置
SYMBOL_MAPPING = {
    'A': '豆一', 'AG': '沪银', 'AL': '沪铝', 'AO': '氧化铝', 'AP': '苹果',
    'AU': '沪金', 'B': '豆二', 'BR': '丁二烯橡胶', 'BU': '沥青', 'C': '玉米',
    'CF': '郑棉', 'CJ': '红枣', 'CS': '玉米淀粉', 'CU': '沪铜', 'EC': '集运指数',
    'EB': '苯乙烯', 'EG': '乙二醇', 'FG': '玻璃', 'FU': '燃油', 'HC': '热卷',
    'I': '铁矿石', 'J': '焦炭', 'JD': '鸡蛋', 'JM': '焦煤', 'L': '塑料',
    # 注：LG(原木)、RS(菜籽) 成交清淡，不纳入更新范围
    'LC': '碳酸锂', 'LH': '生猪', 'LU': '低硫燃料油', 'M': '豆粕',
    'MA': '甲醇', 'NI': '镍', 'NR': '20号胶', 'OI': '菜油', 'P': '棕榈',
    'PB': '沪铅', 'PF': '短纤', 'PG': '液化石油气', 'PK': '花生', 'PP': '聚丙烯',
    'PR': '瓶片', 'PS': '多晶硅', 'PX': '对二甲苯', 'RB': '螺纹钢',
    'RM': '菜粕', 'RU': '橡胶', 'SA': '纯碱', 'SF': '硅铁',
    'SH': '烧碱', 'SI': '工业硅', 'SM': '锰硅', 'SN': '锡', 'SP': '纸浆',
    'SR': '白糖', 'SS': '不锈钢', 'TA': 'PTA', 'UR': '尿素', 'V': 'PVC',
    'Y': '豆油', 'ZN': '沪锌',
    # 🔧 修复：补齐 commodities.yaml 中存在但此前缺失的映射，
    # 避免全量/指定更新时这些品种被静默跳过（如 SC 原油、BC 国际铜）。
    'SC': '原油', 'BC': '国际铜'
}

class InventoryDataUpdater(ProgressReporter):
    """库存数据更新器"""
    
    def __init__(self, database_path: str = "qihuo/database/inventory"):
        """
        初始化库存数据更新器
        
        Args:
            database_path: 数据库路径
        """
        self.base_dir = Path(database_path)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
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
        logger.info("检查现有库存数据状态...")
        
        varieties = []
        variety_info = {}
        
        if not self.base_dir.exists():
            return [], {}
        
        variety_folders = [d for d in self.base_dir.iterdir() if d.is_dir()]
        logger.info(f"发现 {len(variety_folders)} 个品种文件夹")
        
        for folder in variety_folders:
            variety = folder.name
            inventory_file = folder / "inventory.csv"
            
            if inventory_file.exists():
                try:
                    df = pd.read_csv(inventory_file, encoding='utf-8')
                    if len(df) > 0 and 'date' in df.columns:
                        df['date'] = pd.to_datetime(df['date'])
                        variety_latest = df['date'].max()
                        variety_earliest = df['date'].min()
                        record_count = len(df)
                        
                        variety_info[variety] = {
                            "earliest_date": variety_earliest,
                            "latest_date": variety_latest,
                            "record_count": record_count,
                            "file_path": inventory_file
                        }
                        
                        varieties.append(variety)
                        logger.info(f"{variety}: {record_count} 条记录 ({variety_earliest.strftime('%Y-%m-%d')} ~ {variety_latest.strftime('%Y-%m-%d')})")
                        
                except Exception as e:
                    logger.warning(f"{variety}: 读取失败 - {str(e)[:50]}")
                    self.update_stats["error_messages"].append(f"{variety}: 数据读取失败 - {str(e)}")
        
        logger.info(f"总计: {len(varieties)} 个有效品种")
        return varieties, variety_info
    
    def fetch_variety_data(self, symbol: str, series_cn: str, target_date: datetime, retries: int = 3) -> Optional[pd.DataFrame]:
        """
        获取品种数据
        
        Args:
            symbol: 品种代码
            series_cn: 中文品种名称
            target_date: 目标日期（用于数据过滤）
            retries: 重试次数
        
        Returns:
            数据DataFrame或None
        """
        logger.info(f"获取 {symbol} ({series_cn}) 的库存数据...")
        
        for attempt in range(retries):
            try:
                # 获取数据（注意：库存数据接口不支持日期参数，会返回所有历史数据）
                raw_df = ak.futures_inventory_em(symbol=series_cn)
                
                if raw_df is None or raw_df.empty:
                    logger.warning(f"{symbol}: 第{attempt+1}次尝试无数据返回")
                    if attempt < retries - 1:
                        time.sleep(random.uniform(1, 3))
                    continue
                
                # 标准化数据（使用英文列名，避免编码问题）
                new_df = raw_df.rename(columns={"日期": "date", "库存": "value"})
                new_df["date"] = pd.to_datetime(new_df["date"])
                new_df["value"] = pd.to_numeric(new_df["value"], errors="coerce")
                
                # 计算增减列（基于前一日数据计算）
                new_df = new_df.sort_values('date').reset_index(drop=True)
                new_df["change"] = new_df["value"].diff().fillna(0)
                
                new_df = new_df.dropna(subset=["value"]).drop_duplicates(subset=["date"]).sort_values("date")
                
                # 过滤到目标日期（库存数据特殊处理）
                new_df = new_df[new_df['date'] <= target_date]
                
                if new_df.empty:
                    logger.warning(f"{symbol}: 截止日期前无有效数据")
                    return None
                
                new_start = new_df['date'].min().strftime('%Y-%m-%d')
                new_end = new_df['date'].max().strftime('%Y-%m-%d')
                logger.debug(f"{symbol}: 获取到 {len(new_df)} 条记录 ({new_start} ~ {new_end})")
                
                return new_df
                
            except Exception as e:
                logger.warning(f"{symbol}: 第{attempt+1}次尝试失败 - {str(e)[:50]}")
                if attempt < retries - 1:
                    time.sleep(random.uniform(1, 3))
        
        # 东财库存接口并不覆盖全部品种（如 SC/BC 等），属可容忍失败，不计 ERROR
        logger.warning(f"{symbol} ({series_cn}): 库存数据获取失败（接口可能不覆盖该品种）")
        return None
    
    def save_variety_data(self, symbol: str, new_data: pd.DataFrame, existing_info: Optional[Dict] = None) -> bool:
        """
        保存品种数据（智能合并）
        
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
            
            inventory_file = variety_dir / "inventory.csv"
            
            if existing_info and inventory_file.exists():
                # 读取现有数据
                existing_df = pd.read_csv(inventory_file, encoding='utf-8')
                existing_df['date'] = pd.to_datetime(existing_df['date'])
                
                # 计算新增数据
                existing_dates = set(existing_df['date'].dt.date)
                new_dates = set(new_data['date'].dt.date)
                added_dates = new_dates - existing_dates
                
                if added_dates:
                    # 有新数据，合并
                    combined_df = pd.concat([existing_df, new_data]).drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
                    
                    # 重新计算全部增减值（使用英文列名）
                    combined_df["change"] = combined_df["value"].diff().fillna(0)
                    
                    latest_added = max(added_dates).strftime('%Y-%m-%d')
                    logger.debug(f"{symbol}: 新增 {len(added_dates)} 条记录 (至 {latest_added})")
                    self.update_stats["updated_varieties"].append(symbol)
                    self.update_stats["total_new_records"] += len(added_dates)
                else:
                    # 无新数据
                    combined_df = existing_df
                    logger.debug(f"{symbol}: 无新数据")
                    self.update_stats["skipped_varieties"].append(symbol)
                    return True
            else:
                # 新品种或无现有数据
                combined_df = new_data
                data_span = f"{new_data['date'].min().strftime('%Y-%m-%d')} ~ {new_data['date'].max().strftime('%Y-%m-%d')}"
                logger.debug(f"{symbol}: 创建 {len(new_data)} 条记录 ({data_span})")
                self.update_stats["new_varieties"].append(symbol)
                self.update_stats["total_new_records"] += len(new_data)
            
            # 保存数据（使用UTF-8-BOM让Excel正确识别中文）
            combined_df.to_csv(inventory_file, index=False, encoding='utf-8-sig')
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
        logger.info("库存数据更新器")
        
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
        logger.warning("注意: 库存数据接口无法选择日期范围，会获取完整历史数据然后过滤")
        
        # 获取现有数据状态
        existing_varieties, variety_info = self.get_existing_data_status()
        
        # 确定要更新的品种：范围取自 commodities.yaml，内置字典只提供东财库存接口用的中文系列名
        cfg_names = variety_catalog.name_map()
        if specific_varieties:
            target_symbols = []
            unmapped = []
            for s in specific_varieties:
                key = str(s).upper()
                series_cn = SYMBOL_MAPPING.get(key) or cfg_names.get(key)
                if series_cn:
                    target_symbols.append((key, series_cn))
                else:
                    unmapped.append(key)
            # 🔧 修复：未配置映射的品种不再静默过滤——写入失败列表并提示，
            # 避免 UI 上明明“更新成功”，实际却有品种从未被更新。
            if unmapped:
                self.update_stats["failed_varieties"].extend(unmapped)
                for k in unmapped:
                    msg = f"{k}: 未配置东财库存映射(SYMBOL_MAPPING)，无法更新"
                    self.update_stats["error_messages"].append(msg)
                logger.error(f"缺少库存映射的品种(已计入失败): {', '.join(unmapped)}")
            logger.info(
                f"指定更新品种: {len(target_symbols)} 个"
                + (f"（另有 {len(unmapped)} 个无映射）" if unmapped else "")
            )
        else:
            # 并集：commodities.yaml 为主，内置字典补充配置中尚未登记的老品种
            cfg_symbols = variety_catalog.symbols()
            wanted = cfg_symbols + [s for s in SYMBOL_MAPPING if s not in cfg_symbols]
            target_symbols = [(s, SYMBOL_MAPPING.get(s) or cfg_names.get(s) or s) for s in wanted]
            logger.info(f"全品种更新: {len(target_symbols)} 个")
        
        # 执行更新
        processed_count = 0
        
        for i, (symbol, series_cn) in enumerate(target_symbols):
            self._report_progress("处理品种(东财库存)", i + 1, len(target_symbols), symbol)
            logger.info(f"[{i+1}/{len(target_symbols)}] 处理品种: {symbol} ({series_cn})")
            
            # 获取品种数据
            new_data = self.fetch_variety_data(symbol, series_cn, target_date)
            
            if new_data is None or new_data.empty:
                # 失败原因已在 fetch_variety_data 内记录，此处只归集，避免重复告警
                logger.warning(f"跳过 {symbol}: 本次无库存数据，已计入失败")
                self.update_stats["failed_varieties"].append(symbol)
                continue
            
            # 保存数据
            existing_info = variety_info.get(symbol)
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
    logger.info("库存数据更新器")
    
    updater = InventoryDataUpdater()
    
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
    logger.info("更新模式: 智能增量更新（只更新缺失的数据）")
    
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
