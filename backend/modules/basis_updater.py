#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基差数据更新器
基于最终修复版基差数据更新器的逻辑，支持增量更新
"""

import akshare as ak
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import time
import random
import json
from typing import Dict, List, Optional, Tuple
from loguru import logger

from modules.progress import ProgressReporter

class BasisDataUpdater(ProgressReporter):
    """基差数据更新器"""
    
    def __init__(self, database_path: str = None):
        """
        初始化基差数据更新器
        
        Args:
            database_path: 数据库路径，默认为项目根目录下的 qihuo/database/basis
        """
        if database_path is None:
            # 使用相对路径，从当前文件位置找到项目根目录
            current_file = Path(__file__).resolve()
            project_root = current_file.parent.parent  # modules -> TradingAgent
            database_path = project_root / "qihuo" / "database" / "basis"
        self.base_dir = Path(database_path)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        self.update_stats = {
            "start_time": None,
            "end_time": None,
            "target_date": None,
            "updated_varieties": [],
            "failed_varieties": [],
            "skipped_varieties": [],
            "total_new_records": 0,
            "error_messages": []
        }
    
    def _count_variety_once(self, key: str, variety: str) -> None:
        """按品种去重累计：跨多交易日更新时同一品种只计一次"""
        stats = self.update_stats.setdefault(key, [])
        if variety not in stats:
            stats.append(variety)

    def get_existing_data_status(self) -> Tuple[Optional[datetime], List[str], Dict]:
        """
        获取现有数据状态
        
        Returns:
            latest_date: 最新数据日期
            varieties: 现有品种列表
            variety_info: 各品种详细信息
        """
        logger.info("检查现有基差数据状态...")
        
        latest_date = None
        varieties = []
        variety_info = {}
        
        if not self.base_dir.exists():
            return None, [], {}
        
        variety_folders = [d for d in self.base_dir.iterdir() if d.is_dir()]
        logger.info(f"发现 {len(variety_folders)} 个品种文件夹")
        
        for folder in variety_folders:
            variety = folder.name
            basis_file = folder / "basis_data.csv"
            
            if basis_file.exists():
                try:
                    df = pd.read_csv(basis_file, encoding='utf-8')
                    if len(df) > 0 and 'date' in df.columns:
                        df['date'] = pd.to_datetime(df['date'])
                        variety_latest = df['date'].max()
                        variety_earliest = df['date'].min()
                        record_count = len(df)
                        
                        variety_info[variety] = {
                            "earliest_date": variety_earliest,
                            "latest_date": variety_latest,
                            "record_count": record_count
                        }
                        
                        varieties.append(variety)
                        
                        if latest_date is None or variety_latest > latest_date:
                            latest_date = variety_latest
                        
                        logger.info(f"{variety}: {record_count} 条记录 ({variety_earliest.strftime('%Y-%m-%d')} ~ {variety_latest.strftime('%Y-%m-%d')})")
                        
                except Exception as e:
                    logger.warning(f"{variety}: 读取失败 - {str(e)[:50]}")
                    self.update_stats["error_messages"].append(f"{variety}: 数据读取失败 - {str(e)}")
        
        if latest_date:
            logger.info(f"整体最新数据日期: {latest_date.strftime('%Y-%m-%d')}")
        else:
            logger.warning("未找到有效的基差数据")
        
        return latest_date, varieties, variety_info
    
    def calculate_update_dates(self, latest_date: Optional[datetime], target_date: datetime) -> List[str]:
        """
        计算需要更新的日期列表
        
        Args:
            latest_date: 现有数据的最新日期
            target_date: 目标更新日期
        
        Returns:
            需要更新的日期列表 (YYYYMMDD格式)
        """
        update_dates = []
        
        if latest_date is None:
            # 没有现有数据，获取最近5个交易日
            logger.warning("未找到现有数据，将获取最近5个交易日数据")
            current = target_date
            while len(update_dates) < 5:
                current -= timedelta(days=1)
                if current.weekday() < 5:  # 工作日
                    update_dates.append(current.strftime('%Y%m%d'))
            update_dates.reverse()
        else:
            # 计算需要更新的交易日
            next_date = latest_date + timedelta(days=1)
            current = next_date
            
            while current.date() <= target_date.date():
                if current.weekday() < 5:  # 工作日
                    update_dates.append(current.strftime('%Y%m%d'))
                current += timedelta(days=1)
        
        return update_dates
    
    def fetch_daily_data(self, date_str: str, retry_count: int = 3) -> Optional[pd.DataFrame]:
        """
        获取指定日期的基差数据
        
        Args:
            date_str: 日期字符串 (YYYYMMDD格式)
            retry_count: 重试次数
        
        Returns:
            数据DataFrame或None
        """
        logger.info(f"获取 {date_str} 的基差数据...")
        
        for attempt in range(retry_count):
            try:
                # 获取数据
                df = ak.futures_spot_price(date_str)
                
                if df is None or df.empty:
                    logger.warning(f"{date_str}: 第{attempt+1}次尝试无数据返回")
                    if attempt < retry_count - 1:
                        time.sleep(random.uniform(1, 3))
                    continue
                
                logger.debug(f"{date_str}: 获取到 {len(df)} 个品种的数据")
                
                # 检查品种列名（适应不同版本的akshare）
                variety_col = None
                if 'var' in df.columns:
                    variety_col = 'var'
                elif 'symbol' in df.columns:
                    variety_col = 'symbol'
                else:
                    # akshare 版本差异导致的列名回退，属可容忍异常
                    logger.warning(f"{date_str}: 返回数据中未找到品种列（var 或 symbol），可用列: {list(df.columns)[:8]}")
                    continue
                
                # 数据标准化
                df = df.copy()
                df['date'] = pd.to_datetime(date_str, format='%Y%m%d')
                
                return df, variety_col
                
            except Exception as e:
                logger.warning(f"{date_str}: 第{attempt+1}次尝试失败 - {str(e)[:100]}")
                if attempt < retry_count - 1:
                    time.sleep(random.uniform(1, 3))
        
        logger.error(f"{date_str}: 所有尝试均失败")
        return None, None
    
    def _sync_dominant_contract(self, symbol: str, df: pd.DataFrame):
        """
        将基差数据中的主力合约同步到本地主力合约库
        <data_root>/main_contract/<SYMBOL>/dominant_contract.csv（目录不存在自动创建）
        """
        try:
            try:
                from modules.main_contract_sync import MainContractSync, normalize_contract
            except Exception:
                import sys
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
                from modules.main_contract_sync import MainContractSync, normalize_contract

            if df is None or df.empty:
                return
            if "date" not in df.columns or "dominant_contract" not in df.columns:
                return

            rows = []
            for _, row in df.iterrows():
                contract = normalize_contract(row.get("dominant_contract"))
                date_value = row.get("date")
                if contract is None or date_value is None:
                    continue
                try:
                    date_compact = pd.to_datetime(date_value).strftime("%Y%m%d")
                except Exception:
                    continue
                rows.append((date_compact, contract))

            if not rows:
                return
            sync = MainContractSync(data_root=self.base_dir.parent)
            saved = sync._save(symbol, rows)
            if saved:
                logger.debug(f"{symbol}: 主力合约已同步到本地库（{saved} 条）")
        except Exception as e:
            logger.warning(f"{symbol}: 同步主力合约失败（不影响基差保存）- {str(e)[:80]}")

    def save_variety_data(self, variety: str, new_data: pd.DataFrame) -> bool:
        """
        保存品种数据
        
        Args:
            variety: 品种代码
            new_data: 新数据
        
        Returns:
            是否保存成功
        """
        try:
            variety_dir = self.base_dir / variety
            variety_dir.mkdir(parents=True, exist_ok=True)
            
            basis_file = variety_dir / "basis_data.csv"
            summary_file = variety_dir / "basis_summary.json"
            
            # 读取现有数据
            if basis_file.exists():
                existing_df = pd.read_csv(basis_file, encoding='utf-8')
                existing_df['date'] = pd.to_datetime(existing_df['date'])
                
                # 合并数据
                combined_df = pd.concat([existing_df, new_data], ignore_index=True)
                combined_df = combined_df.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
                
                # 注：该字段命名为 new_records，实为“合并后相对旧文件的净增行数”
                new_records = len(combined_df) - len(existing_df)
                if new_records > 0:
                    logger.debug(f"{variety}: 新增 {new_records} 条记录")
                    self.update_stats["total_new_records"] += new_records
                else:
                    logger.debug(f"{variety}: 无新数据")
                    self._count_variety_once("skipped_varieties", variety)
                    return True
            else:
                combined_df = new_data
                logger.debug(f"{variety}: 创建 {len(new_data)} 条记录")
                self.update_stats["total_new_records"] += len(new_data)
            
            # 保存CSV数据
            combined_df.to_csv(basis_file, index=False, encoding='utf-8-sig')

            # 同步主力合约到本地主力合约库（供持仓等其它模块复用，无需重复联网）
            self._sync_dominant_contract(variety, combined_df)

            # 更新摘要信息
            summary_info = {
                "symbol": variety,
                "record_count": len(combined_df),
                "date_range": {
                    "start": combined_df['date'].min().isoformat(),
                    "end": combined_df['date'].max().isoformat()
                },
                "last_updated": datetime.now().isoformat(),
                "columns": list(combined_df.columns)
            }
            
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary_info, f, ensure_ascii=False, indent=2)
            
            self._count_variety_once("updated_varieties", variety)
            return True
            
        except Exception as e:
            logger.error(f"{variety}: 保存失败 - {str(e)}")
            self._count_variety_once("failed_varieties", variety)
            self.update_stats["error_messages"].append(f"{variety}: 保存失败 - {str(e)}")
            return False
    
    def update_to_date(self, target_date_str: str, start_date_str: Optional[str] = None, specific_varieties: Optional[List[str]] = None) -> Dict:
        """
        更新数据到指定日期
        
        Args:
            target_date_str: 目标日期 (YYYY-MM-DD格式)
            start_date_str: 开始日期 (YYYY-MM-DD格式，可选)，如果不指定则从现有数据的最新日期开始
            specific_varieties: 指定品种列表，None表示全部品种
        
        Returns:
            更新结果统计
        """
        logger.info("基差数据更新器")
        
        # 解析目标日期
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d')
        except ValueError:
            try:
                target_date = datetime.strptime(target_date_str, '%Y%m%d')
            except ValueError:
                raise ValueError(f"日期格式错误: {target_date_str}，请使用 YYYY-MM-DD 或 YYYYMMDD 格式")
        
        # 解析开始日期（如果提供）
        start_date = None
        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
                logger.info(f"指定开始日期: {start_date.strftime('%Y-%m-%d')}")
            except ValueError:
                try:
                    start_date = datetime.strptime(start_date_str, '%Y%m%d')
                    logger.info(f"指定开始日期: {start_date.strftime('%Y-%m-%d')}")
                except ValueError:
                    logger.warning(f"开始日期格式错误（{start_date_str}），将使用现有数据的最新日期")
                    start_date = None
        
        self.update_stats["start_time"] = datetime.now()
        self.update_stats["target_date"] = target_date_str
        
        logger.info(f"目标更新日期: {target_date.strftime('%Y-%m-%d')}")
        
        # 获取现有数据状态
        latest_date, existing_varieties, variety_info = self.get_existing_data_status()
        
        # 确定实际的开始日期
        if start_date:
            # 使用用户指定的开始日期
            actual_start_date = start_date
        else:
            # 使用现有数据的最新日期
            actual_start_date = latest_date
        
        # 计算更新日期
        update_dates = self.calculate_update_dates(actual_start_date, target_date)
        
        if not update_dates:
            logger.info("数据已是最新，无需更新")
            self.update_stats["end_time"] = datetime.now()
            return self.update_stats
        
        # 品种范围（接口一次返回当日全市场品种，因此“指定品种”需在逐行处理阶段过滤）
        wanted: Optional[set] = None
        if specific_varieties:
            wanted = {str(v).strip().upper() for v in specific_varieties if str(v).strip()}
            logger.info(f"指定更新品种: {len(wanted)} 个 -> {', '.join(sorted(wanted))}")
        else:
            logger.info("全品种更新")

        logger.info(f"需要更新的日期: {len(update_dates)} 个交易日")
        
        # 执行更新
        success_count = 0
        total_attempts = 0
        
        for i, date_str in enumerate(update_dates):
            self._report_progress("拉取期现基差(按交易日)", i + 1, len(update_dates), date_str)
            logger.info(f"[{i+1}/{len(update_dates)}] 处理日期: {date_str}")
            total_attempts += 1
            
            # 获取当日数据
            result = self.fetch_daily_data(date_str)
            if result is None or result[0] is None:
                # 原因已在 fetch_daily_data 内记录，此处只提示跳过，避免重复告警
                logger.warning(f"跳过 {date_str}: 本次无基差数据（非交易日或数据源未发布）")
                continue
            
            df, variety_col = result
            success_count += 1
            
            # 按品种处理数据
            variety_success = 0
            variety_total = 0
            
            for _, row in df.iterrows():
                variety = str(row[variety_col]).upper()
                
                # 如果指定了品种，只处理指定的品种
                if wanted is not None and variety not in wanted:
                    continue

                # 分母只统计本次范围内的品种（接口返回的是全市场）
                variety_total += 1
                
                
                # 构造该品种的数据（使用正确的列名从akshare数据中获取）
                variety_data = pd.DataFrame([{
                    'date': row['date'],
                    'symbol': variety,
                    # akshare返回的是完整的英文列名
                    'spot_price': row.get('spot_price', 0),  # 现货价格
                    'near_contract': row.get('near_contract', ''),  # 近月合约
                    'near_contract_price': row.get('near_contract_price', 0),  # 近月价格
                    'dominant_contract': row.get('dominant_contract', ''),  # 主力合约
                    'dominant_contract_price': row.get('dominant_contract_price', 0),  # 主力价格
                    'near_month': row.get('near_month', 0),  # 近月月份
                    'dominant_month': row.get('dominant_month', 0),  # 主力月份
                    'near_basis': row.get('near_basis', 0),  # 近月基差
                    'dom_basis': row.get('dom_basis', 0),  # 主力基差
                    'near_basis_rate': row.get('near_basis_rate', 0),  # 近月基差率
                    'dom_basis_rate': row.get('dom_basis_rate', 0)  # 主力基差率
                }])
                
                if self.save_variety_data(variety, variety_data):
                    variety_success += 1
            
            logger.info(f"品种更新: 成功 {variety_success}/{variety_total}")
            
            # 添加随机延迟避免请求过快
            if i < len(update_dates) - 1:
                delay = random.uniform(0.5, 2.0)
                time.sleep(delay)
        
        # 完成统计
        self.update_stats["end_time"] = datetime.now()
        
        self.log_update_summary()

        return self.update_stats
    
    def update_data(self, target_date_str: str, start_date_str: Optional[str] = None, specific_varieties: Optional[List[str]] = None) -> Dict:
        """
        更新数据到指定日期（与update_to_date相同，为兼容统一更新器接口）
        
        Args:
            target_date_str: 目标日期 (YYYY-MM-DD格式)
            start_date_str: 开始日期 (YYYY-MM-DD格式，可选)
            specific_varieties: 指定品种列表，None表示全部品种
        
        Returns:
            更新结果统计
        """
        return self.update_to_date(target_date_str, start_date_str, specific_varieties)

def main():
    """交互式主函数"""
    logger.info("基差数据更新器")
    
    updater = BasisDataUpdater()
    
    # 获取现有数据状态
    logger.info("正在检查现有数据状态...")
    latest_date, varieties, info = updater.get_existing_data_status()
    
    if latest_date:
        logger.info(f"当前最新数据日期: {latest_date.strftime('%Y-%m-%d')}")
    else:
        logger.warning("当前暂无数据")
    
    logger.info(f"已有品种数量: {len(varieties)} 个")
    if varieties:
        logger.info(f"品种列表: {', '.join(sorted(varieties)[:20])}{'...' if len(varieties) > 20 else ''}")
    
    # 用户输入更新参数
    logger.info("请输入更新参数:")
    
    # 只输入目标日期，自动从最新数据开始智能增量更新
    default_date = datetime.now().strftime('%Y-%m-%d')
    target_date_input = input(f"📅 目标日期 (格式: YYYY-MM-DD, 直接回车使用今天 {default_date}): ").strip()
    target_date = target_date_input if target_date_input else default_date
    
    # 验证目标日期格式
    try:
        datetime.strptime(target_date, '%Y-%m-%d')
    except ValueError:
        logger.error(f"目标日期格式错误，使用默认日期: {default_date}")
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
    if latest_date:
        logger.info(f"从最新数据日期: {latest_date.strftime('%Y-%m-%d')}")
    else:
        logger.info("首次更新: 将获取完整历史数据")
    logger.info(f"更新到日期: {target_date}")
    logger.info(f"更新品种: {'全部' if not specific_varieties else ', '.join(specific_varieties)}")
    logger.info("更新模式: 智能增量更新（只更新缺失的数据）")
    
    confirm = input("\n确认开始更新？(y/N): ").strip().lower()
    if confirm != 'y':
        logger.error("已取消更新")
        return
    
    # 执行更新（不传入start_date，自动智能更新）
    logger.info("开始更新...")
    result = updater.update_to_date(target_date, start_date_str=None, specific_varieties=specific_varieties)
    
    logger.info("更新完成!")

if __name__ == "__main__":
    main()
