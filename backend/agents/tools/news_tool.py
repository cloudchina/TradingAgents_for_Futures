"""新闻搜索工具：复用 news.py 双源接口（akshare + TianAPI）

直接调 news.search_contract_news(symbol, name, direction)，
让 config/news_config.json 的 source 字段控制双源合并（source="both" 默认）。
"""
from loguru import logger


def _resolve_name(symbol: str) -> str:
    """由 symbol（如 AU）拿中文名（如 黄金）。拿不到就用 symbol 本身。"""
    try:
        # 延迟导入，避免与 services 包循环导入
        from services.commodity_service import commodity_service
        info = commodity_service.get_commodity(symbol)
        if info and info.get("name"):
            return info["name"]
    except Exception as e:
        logger.warning(f"解析品种名失败 {symbol}: {e}")
    return symbol


def search_news_data(symbol: str, direction: str = "bullish", max_items: int = 5) -> str:
    """搜索某品种近期新闻，返回格式化文本给 LLM。

    Args:
        symbol: 品种代码，如 "AU"
        direction: "bullish"（看多）或 "bearish"（看空），影响搜索关键词
        max_items: 最多返回多少条（实际由 search_contract_news 内部限制为 3 条）

    Returns:
        多行新闻文本。无新闻时返回空字符串说明。
    """
    name = _resolve_name(symbol)
    try:
        # 延迟导入，避免与 services 包循环导入
        from modules.news import search_contract_news
        result = search_contract_news(symbol, name, direction)
    except Exception as e:
        msg = f"[新闻搜索失败] {symbol} {name} {direction}: {e}"
        logger.error(msg)
        return msg

    if not result:
        return f"[{symbol} {name}] 近期无相关新闻"
    return result
