"""多 Agent ReAct 辩论模块

提供基于百炼 OpenAI 兼容 API 的 ReAct 单 Agent 与多 Agent 辩论编排能力。
- llm_client: 百炼客户端单例（同步）
- react_agent: ReAct 循环基类
- tools: 数据读取 / 新闻搜索工具
- prompts: 7 个角色的中文 system prompt
- debate: 辩论编排器 + 共识判定
"""
