"""数据读取工具：读取分析模块本地 CSV

关键策略：只读 CSV，不实例化 updater，不写新分析逻辑。
- 复用 data_service.MODULE_CONFIG 拿路径信息（subdir / data_file）
- 技术指标已由 updater 在数据更新时算好并存入 ohlc_data.csv，agent 读 CSV 即可拿 MA/RSI/MACD
- 数值列附"最新值 + 5日均值"摘要，省去 LLM 自己算
"""
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from core.settings import settings

# AnalysisModule 枚举值 → MODULE_CONFIG key 归一
# 枚举用 "technical"，MODULE_CONFIG 用 "technical_analysis"；"news" 单独走 news_tool
MODULE_NAME_MAP = {
    "technical": "technical_analysis",
    "inventory": "inventory",
    "positioning": "positioning",
    "term_structure": "term_structure",
    "basis": "basis",
    "receipt": "receipt",
}


def _get_module_config():
    """延迟导入 MODULE_CONFIG，避免与 services 包循环导入。"""
    from services.data_service import MODULE_CONFIG
    return MODULE_CONFIG


def _resolve_path(module: str, symbol: str) -> Optional[Path]:
    """根据 MODULE_CONFIG 拼出 CSV 路径。

    路径格式：DATA_ROOT_DIR / {subdir} / {symbol} / {data_file}
    """
    cfg = _get_module_config().get(module)
    if not cfg:
        return None
    return Path(settings.DATA_ROOT_DIR) / cfg["subdir"] / symbol / cfg["data_file"]


def _summarize(df: pd.DataFrame, numeric_cols: list) -> str:
    """生成数值列最新值 + 5日均值摘要。"""
    lines = []
    if not numeric_cols:
        return ""
    tail5 = df.tail(5)
    for col in numeric_cols:
        try:
            latest = df[col].iloc[-1]
            avg5 = tail5[col].mean()
            lines.append(f"  {col}: 最新值={latest:.4f} | 5日均值={avg5:.4f}")
        except Exception:
            continue
    return "\n".join(lines)


def _format_table(df: pd.DataFrame, max_rows: int = 30) -> str:
    """格式化 CSV 为 markdown 表格，超长时保留首尾各 5 行 + 省略号。"""
    if len(df) > max_rows:
        head = df.head(5)
        tail = df.tail(5)
        parts = [head.to_markdown(index=False), "\n... (省略中间行) ...\n", tail.to_markdown(index=False)]
        return "\n".join(parts)
    return df.to_markdown(index=False)


def read_module_data(module: str, symbol: str, tail_rows: int = 30) -> str:
    """读取指定分析模块的本地 CSV 数据。

    Args:
        module: 模块名（inventory/positioning/term_structure/technical_analysis/basis/receipt）
        symbol: 品种代码，如 "AU"
        tail_rows: 取最近 N 行，默认 30

    Returns:
        文本摘要字符串，含列名 + 表格 + 数值摘要。出错时返回错误说明字符串。
    """
    # 归一模块名
    real_module = MODULE_NAME_MAP.get(module, module)
    path = _resolve_path(real_module, symbol)
    if path is None or not path.exists():
        msg = f"[模块: {module} | 品种: {symbol}] 无 CSV 数据（路径: {path}）"
        logger.warning(msg)
        return msg

    try:
        df = pd.read_csv(path)
    except Exception as e:
        msg = f"[模块: {module} | 品种: {symbol}] CSV 读取失败: {e}"
        logger.error(msg)
        return msg

    if df.empty:
        return f"[模块: {module} | 品种: {symbol}] CSV 为空"

    # 取最近 N 行
    if len(df) > tail_rows:
        df_show = df.tail(tail_rows).copy()
    else:
        df_show = df.copy()

    header = f"[模块: {module} | 品种: {symbol} | 文件: {path.name}] 共 {len(df)} 行，展示最近 {len(df_show)} 行"
    table = _format_table(df_show, max_rows=tail_rows)

    # 数值列摘要
    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    summary = _summarize(df, numeric_cols)
    summary_block = f"\n\n数值列摘要:\n{summary}" if summary else ""

    return f"{header}\n\n列: {', '.join(df.columns)}\n\n{table}{summary_block}"
