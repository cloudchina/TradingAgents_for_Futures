"""OpenAI function-calling 工具 schema 定义

百炼 compatible-mode 原生支持 OpenAI 的 tools 参数，
直接传给 chat.completions.create 即可让模型自主决定调哪个工具。
"""

DATA_READER_TOOL = {
    "type": "function",
    "function": {
        "name": "read_module_data",
        "description": (
            "读取指定分析模块的本地 CSV 数据（最近 N 行 + 数值列摘要）。"
            "可用模块: inventory(库存), positioning(持仓席位), term_structure(期限结构), "
            "technical_analysis(技术指标含MA/EMA/RSI/MACD/布林带/KDJ/ATR), basis(基差), receipt(仓单)。"
            "技术指标列已在数据更新时计算好，直接读取即可，无需重算。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "enum": [
                        "inventory",
                        "positioning",
                        "term_structure",
                        "technical_analysis",
                        "basis",
                        "receipt",
                    ],
                    "description": "分析模块名",
                },
                "symbol": {"type": "string", "description": "品种代码，如 AU / RB / CU"},
                "tail_rows": {
                    "type": "integer",
                    "default": 30,
                    "minimum": 5,
                    "maximum": 120,
                    "description": "取最近 N 行，默认 30",
                },
            },
            "required": ["module", "symbol"],
        },
    },
}

NEWS_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_news_data",
        "description": (
            "搜索某品种近期的相关新闻（基于 akshare 上海金属网 + TianAPI 财经新闻双源，"
            "自动做同义词扩展、日期过滤、去重）。用于补充新闻面证据。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "品种代码，如 AU"},
                "direction": {
                    "type": "string",
                    "enum": ["bullish", "bearish"],
                    "default": "bullish",
                    "description": "搜索方向：bullish 搜看多相关新闻，bearish 搜看空相关新闻",
                },
                "max_items": {"type": "integer", "default": 5},
            },
            "required": ["symbol"],
        },
    },
}

ALL_TOOLS = [DATA_READER_TOOL, NEWS_SEARCH_TOOL]
