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

# ─────────────────────────────────────────────────────────────
# 记忆体系工具（阶段 3，见 memory-system-plan.md 四.阶段3）
# ─────────────────────────────────────────────────────────────

MEMORY_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_memory",
        "description": (
            "检索该品种的历史分析结论与复盘结果（方向、置信度、事后是否命中）。"
            "用于核对自己的观点是否前后一致、历史胜率如何。**按需调用**，"
            "记忆上下文已给出的近期结论不要重复查询。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "品种代码，如 RB / JM"},
                "days": {"type": "integer", "default": 10, "description": "回看天数，默认 10"},
                "top_k": {"type": "integer", "default": 5, "description": "返回条数，默认 5"},
            },
            "required": ["symbol"],
        },
    },
}

RELATED_SYMBOLS_TOOL = {
    "type": "function",
    "function": {
        "name": "get_related_symbols",
        "description": (
            "查询该品种的关联品种（产业链上下游 / 替代 / 套利对）及实测相关性、领先滞后关系。"
            "用于跨品种交叉验证，例如分析螺纹时参考热卷与铁矿。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "品种代码，如 RB / JM"},
            },
            "required": ["symbol"],
        },
    },
}

RECORD_INSIGHT_TOOL = {
    "type": "function",
    "function": {
        "name": "record_insight",
        "description": (
            "沉淀一条可复用的规律到长期记忆（如'库存去化+基差走强 → 后续 5 日上涨'）。"
            "落库为 draft，需后续校验通过才会被引用。单次分析最多调用 2 次，请只记最重要的。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "品种代码，如 RB / JM"},
                "claim": {"type": "string", "description": "规律描述，一句话，需可被后续验证"},
                "category": {
                    "type": "string",
                    "enum": ["规律", "季节性", "结构", "风险"],
                    "default": "规律",
                    "description": "规律类别",
                },
            },
            "required": ["symbol", "claim"],
        },
    },
}

MEMORY_TOOLS = [MEMORY_SEARCH_TOOL, RELATED_SYMBOLS_TOOL, RECORD_INSIGHT_TOOL]

ALL_TOOLS = [DATA_READER_TOOL, NEWS_SEARCH_TOOL] + MEMORY_TOOLS
