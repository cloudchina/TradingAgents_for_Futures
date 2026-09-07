#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主力合约数据同步器（公共模块）

规则（所有数据更新模块共用）：
1. 先检查本地主力合约库 <data_root>/main_contract/<品种>/dominant_contract.csv
2. 本地缺失的交易日，先尝试用本地基差数据 (basis) 补种（已有数据不再联网）
3. 仍缺失的，联网查询（ak.futures_spot_price，每日期一次返回全部品种主力合约），
   查询结果保存到本地（目录不存在时自动创建）
4. 返回 {品种: {YYYYMMDD: 合约代码}} 供各更新器使用

存储文件格式：
  date,symbol,dominant_contract
  2026-09-07,MA,MA2609

合约代码格式与原持仓/基差逻辑保持一致，不做大小写转换，
仅对郑商所等"字母+3位数字"(如 MA509 -> MA2509) 补年份前缀。
"""
import random
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import akshare as ak
import pandas as pd


def normalize_contract(raw) -> Optional[str]:
    """
    清洗主力合约代码：
    - 去除空白；nan 视为缺失
    - 提取字母前缀 + 数字
    - 数字为 3 位（郑商所风格，如 MA509/CY511）时在前面补年份前缀 '2'
      还原为 MA2509/CY2511；其它格式（如 rb2510/TA2601）原样保留
    """
    if raw is None:
        return None
    contract = str(raw).strip()
    if not contract or contract.lower() == 'nan':
        return None

    alpha_part = ""
    num_part = ""
    for i, ch in enumerate(contract):
        if ch.isalpha():
            alpha_part += ch
        elif ch.isdigit():
            num_part = contract[i:]
            break
    # 去掉数字串中的非数字字符
    num_part = "".join(ch for ch in num_part if ch.isdigit())

    if not alpha_part or not num_part:
        return None

    if len(num_part) == 3 and len(alpha_part) <= 2:
        num_part = "2" + num_part
    elif len(num_part) > 4:
        # 异常情况只保留后 4 位数字（YYMM）
        num_part = num_part[-4:]

    return alpha_part + num_part


def _compact_to_dash(date_key: str) -> str:
    """YYYYMMDD -> YYYY-MM-DD"""
    if len(date_key) == 8 and date_key.isdigit():
        return f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:]}"
    return date_key


def _to_compact(date_value) -> str:
    """任意日期表示 -> YYYYMMDD"""
    if isinstance(date_value, (datetime, pd.Timestamp)):
        return date_value.strftime("%Y%m%d")
    s = str(date_value).strip()
    # 兼容 2026-09-07 / 2026/09/07 / 20260907 / Timestamp 字符串
    s = s.replace("-", "").replace("/", "")
    if s.isdigit() and len(s) == 8:
        return s
    try:
        return pd.to_datetime(str(date_value)).strftime("%Y%m%d")
    except Exception:
        return None


class MainContractSync:
    """主力合约本地库：本地优先，缺失联网补齐并落盘"""

    def __init__(self, data_root: Optional[Path] = None):
        """
        Args:
            data_root: 数据根目录（应包含 basis/positioning/main_contract 等子目录）。
                       默认使用 backend/data/qihuo/database
        """
        if data_root is None:
            self.root = (
                Path(__file__).resolve().parent.parent / "data" / "qihuo" / "database"
            )
        else:
            self.root = Path(data_root)
        self.store_dir = self.root / "main_contract"

    # ---------- 内部文件读写 ----------

    def _symbol_file(self, symbol: str) -> Path:
        return self.store_dir / symbol.upper() / "dominant_contract.csv"

    def _read_local(self, symbol: str) -> pd.DataFrame:
        """读取某品种本地主力合约表；不存在/无有效列返回空表"""
        file_path = self._symbol_file(symbol)
        empty = pd.DataFrame(columns=["date", "symbol", "dominant_contract"])
        if not file_path.exists():
            return empty
        try:
            df = pd.read_csv(file_path, encoding="utf-8-sig")
            if df.empty or "date" not in df.columns or "dominant_contract" not in df.columns:
                return empty
            return df[["date", "symbol", "dominant_contract"]]
        except Exception as e:
            print(f"      ⚠️ {symbol}: 读取主力合约本地文件失败 - {str(e)[:80]}")
            return empty

    def _save(self, symbol: str, rows: List[Tuple[str, str]]) -> int:
        """追加/合并保存某品种若干 (date(YYYYMMDD), contract) 记录；自动创建目录"""
        if not rows:
            return 0
        symbol = symbol.upper()
        new_df = pd.DataFrame(
            [{"date": _compact_to_dash(d), "symbol": symbol,
              "dominant_contract": c} for d, c in rows]
        )

        symbol_dir = self.store_dir / symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)  # 目录不存在则新建
        file_path = symbol_dir / "dominant_contract.csv"

        if file_path.exists():
            existing = self._read_local(symbol)
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df

        # 同一日期保留最新一条
        combined = (
            combined.dropna(subset=["dominant_contract"])
            .drop_duplicates(subset=["date"], keep="last")
            .sort_values("date")
        )
        combined.to_csv(file_path, index=False, encoding="utf-8-sig")
        return len(new_df)

    # ---------- 数据来源 ----------

    def _local_map(self, symbol: str) -> Dict[str, str]:
        """本地主力合约 {YYYYMMDD: contract}"""
        mapping = {}
        df = self._read_local(symbol)
        if df.empty:
            return mapping
        for _, row in df.iterrows():
            date_compact = _to_compact(row["date"])
            contract = normalize_contract(row.get("dominant_contract"))
            if date_compact and contract:
                mapping[date_compact] = contract
        return mapping

    def _seed_from_basis(self, symbol: str, needed_dates: set) -> Dict[str, str]:
        """从本地基差数据 (basis/<symbol>/basis_data.csv) 补种主力合约"""
        basis_file = self.root / "basis" / symbol.upper() / "basis_data.csv"
        if not basis_file.exists():
            return {}
        try:
            df = pd.read_csv(basis_file, encoding="utf-8-sig")
            if "date" not in df.columns or "dominant_contract" not in df.columns:
                return {}
            seeded = {}
            for _, row in df.iterrows():
                date_compact = _to_compact(row["date"])
                if date_compact in needed_dates:
                    contract = normalize_contract(row.get("dominant_contract"))
                    if contract:
                        seeded[date_compact] = contract
            if seeded:
                self._save(symbol, list(seeded.items()))
                print(f"      📚 {symbol}: 从基差数据补种 {len(seeded)} 个交易日的主力合约")
            return seeded
        except Exception as e:
            print(f"      ⚠️ {symbol}: 从基差数据补种失败 - {str(e)[:80]}")
            return {}

    def _fetch_online_by_date(self, date_compact: str) -> Dict[str, str]:
        """联网查询某交易日的全部品种主力合约（一次返回全部品种）"""
        for attempt in range(3):
            try:
                df = ak.futures_spot_price(date_compact)
                if df is None or df.empty:
                    return {}
                mapping = {}
                for _, row in df.iterrows():
                    symbol = str(row.get("symbol", "")).strip().upper()
                    contract = normalize_contract(row.get("dominant_contract"))
                    if symbol and contract:
                        mapping[symbol] = contract
                return mapping
            except Exception as e:
                print(f"      ⚠️ 联网获取 {date_compact} 主力合约第{attempt + 1}次失败: {str(e)[:80]}")
                if attempt < 2:
                    time.sleep(random.uniform(1, 3))
        return {}

    # ---------- 对外主入口 ----------

    def ensure(
        self,
        symbols: List[str],
        trading_dates: List[str],
        use_basis_seed: bool = True,
    ) -> Dict[str, Dict[str, str]]:
        """
        确保给定品种在给定交易日都有主力合约。

        Args:
            symbols: 品种代码列表（大写）
            trading_dates: 交易日列表（YYYYMMDD）
            use_basis_seed: 是否允许用本地基差数据补种

        Returns:
            {symbol: {YYYYMMDD: contract}}
        """
        symbols = sorted({s.upper() for s in symbols if s and s.upper() != "NAN"})
        needed_dates = sorted({_to_compact(d) for d in trading_dates if _to_compact(d)})
        if not symbols or not needed_dates:
            return {}

        result = {symbol: {} for symbol in symbols}
        missing_by_symbol = {symbol: set(needed_dates) for symbol in symbols}

        # 1) 本地主力合约库
        for symbol in symbols:
            local = self._local_map(symbol)
            if local:
                result[symbol].update(local)
                covered = set(local.keys()) & set(needed_dates)
                missing_by_symbol[symbol] -= covered
                if covered:
                    print(f"      ✅ {symbol}: 本地已有 {len(covered)} 个交易日的主力合约")

        # 2) 基差数据补种（可选）
        if use_basis_seed:
            for symbol in symbols:
                if not missing_by_symbol[symbol]:
                    continue
                seeded = self._seed_from_basis(symbol, missing_by_symbol[symbol])
                if seeded:
                    result[symbol].update(seeded)
                    missing_by_symbol[symbol] -= set(seeded.keys())

        # 3) 按日期联网查询（一次查一个交易日，覆盖所有仍有缺失的品种）
        dates_still_missing = sorted({
            d for symbol in symbols for d in missing_by_symbol[symbol]
        })
        for date_compact in dates_still_missing:
            online = self._fetch_online_by_date(date_compact)
            if not online:
                print(f"      ⚠️ {date_compact}: 联网未获取到主力合约数据")
                continue
            for symbol in symbols:
                if date_compact not in missing_by_symbol[symbol]:
                    continue
                contract = online.get(symbol)
                if contract:
                    result[symbol][date_compact] = contract
                    missing_by_symbol[symbol].discard(date_compact)
                    self._save(symbol, [(date_compact, contract)])
            print(f"      🌐 {date_compact}: 联网获取并保存 {sum(1 for s in symbols if s in online)} 个品种的主力合约")
            time.sleep(random.uniform(0.5, 1.5))

        # 汇总
        total = 0
        for symbol in symbols:
            if result[symbol]:
                total += len(result[symbol])
            else:
                print(f"      ❌ {symbol}: 未能确认任何交易日的主力合约")
        print(f"      ✅ 主力合约确认完成：共 {total} 条（{len([s for s in result if result[s]])} 个品种）")
        return {s: v for s, v in result.items() if v}

    # ---------- 主力换月复权（技术指标连续化） ----------

    def apply_rollover_adjustment(
        self,
        symbol: str,
        df: pd.DataFrame,
        date_col: str = "时间",
        open_col: str = "开盘",
        high_col: str = "最高",
        low_col: str = "最低",
        close_col: str = "收盘",
    ) -> Tuple[pd.DataFrame, int]:
        """
        主力换月后复权（主连价格连续化）。

        主连 K 线本质是把“各时期真实主力合约”的行情按日拼接起来；换月日两个合约价格
        存在系统性差异（升贴水/基差不同），直接拼接会产生人为跳空，导致基于该价格序列
        计算的 MA/MACD/KDJ/RSI 等指标在换月点附近失真。

        本方法只依据本地主力合约库能够确认的换月日（前后两交易日真实主力合约不同）进行
        后复权：以换月后首日(新合约)收盘 / 换月前一日(旧合约)收盘 的比值，对换月日之前
        的全部 K 线等比缩放，使价格序列在换月点无缝衔接。
        无法确认换月的位置一律不做处理（绝不臆测、绝不误删真实行情跳空）。

        Args:
            symbol: 品种代码
            df: 升序 OHLC（时间列日期可解析）
            date_col/open_col/high_col/low_col/close_col: 列名

        Returns:
            (复权后的 DataFrame, 识别到的换月点数)
        """
        if df is None or df.empty or date_col not in df.columns or close_col not in df.columns:
            return df, 0

        out = df.copy().sort_values(date_col).reset_index(drop=True)
        codes = self._local_map(symbol)
        if not codes:
            print(f"      [!] {symbol}: 本地主力合约库暂无记录，本次不进行换月复权"
                  f"（运行基差/持仓更新后会自动补齐并修正）")
            return out, 0

        date_compacts = [_to_compact(v) for v in out[date_col]]
        code_seq = [codes.get(d) for d in date_compacts]
        if not any(code_seq):
            print(f"      [!] {symbol}: 主力合约记录未覆盖本批数据日期，本次不进行换月复权")
            return out, 0

        # 找出可确认的换月点：前后两个交易日的真实主力合约不同
        boundaries = []
        for i in range(1, len(code_seq)):
            prev = normalize_contract(code_seq[i - 1])
            cur = normalize_contract(code_seq[i])
            if prev and cur and prev != cur:
                boundaries.append(i)
        if not boundaries:
            return out, 0

        close_raw = pd.to_numeric(out[close_col], errors='coerce')
        factor = [1.0] * len(out)
        boundary_set = set(boundaries)
        # 从后往前累计：factor[i] 把第 i 行价格折算到当前主力合约口径
        for i in range(len(out) - 2, -1, -1):
            factor[i] = factor[i + 1]
            if (i + 1) in boundary_set:
                c_new = close_raw[i + 1]
                c_old = close_raw[i]
                if pd.notna(c_new) and pd.notna(c_old) and c_old != 0:
                    factor[i] *= (float(c_new) / float(c_old))

        for col in (open_col, high_col, low_col, close_col):
            if col in out.columns:
                raw = pd.to_numeric(out[col], errors='coerce')
                out[col] = raw * factor

        return out, len(boundaries)


if __name__ == "__main__":
    # 简单自测（不联网）：验证代码清洗逻辑
    test_cases = [
        ("rb2510", "rb2510"),
        ("cu1811", "cu1811"),
        ("MA509", "MA2509"),
        ("CY511", "CY2511"),
        ("TA601", "TA2601"),
        ("I2509", "I2509"),
        ("nan", None),
        ("", None),
        ("  JD2601 ", "JD2601"),
        ("V0", "V0"),
    ]
    ok = True
    for raw, expected in test_cases:
        got = normalize_contract(raw)
        flag = "✅" if got == expected else "❌"
        if got != expected:
            ok = False
        print(f"  {flag} {raw!r:>12} -> {got!r}")
    print("自测通过" if ok else "自测存在失败项")
