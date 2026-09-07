"""ReAct 循环基类：Thought → Action → Observation → Final Answer

基于百炼 OpenAI 兼容 API 的 function-calling 实现 ReAct 模式。
- LLM 自主决定是否调工具（tool_choice="auto"）
- 有 tool_calls → 执行工具 → 把 observation 加回 messages → 继续
- 无 tool_calls → 视为 Final Answer
- 达 max_iterations 强制注入收尾指令
"""
import json
from typing import Callable, Dict, List, Any, Optional

from loguru import logger

from agents.llm_client import llm_client


class ReActAgent:
    """ReAct 循环基类"""

    def __init__(
        self,
        role_name: str,
        system_prompt: str,
        tools: List[Dict[str, Any]],
        tool_map: Dict[str, Callable],
        model: str = "qwen-plus",
        max_iterations: int = 6,
        temperature: float = 0.7,
    ):
        self.role_name = role_name
        self.system_prompt = system_prompt
        self.tools = tools
        self.tool_map = tool_map
        self.model = model
        self.max_iterations = max_iterations
        self.temperature = temperature

    def run(self, user_message: str) -> Dict[str, Any]:
        """执行一次完整 ReAct。

        Returns:
            {
                "final_answer": str,         # LLM 最终输出（期望是 JSON 字符串）
                "parsed": dict,              # 解析后的 dict（解析失败则 {"raw": answer}）
                "trace": [                   # 推理轨迹
                    {"thought": str, "action": str|None, "observation": str|None},
                    ...
                ]
            }
        """
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]
        trace: List[Dict[str, Any]] = []

        for step in range(self.max_iterations):
            try:
                msg = llm_client.chat(
                    model=self.model,
                    messages=messages,
                    tools=self.tools,
                    temperature=self.temperature,
                )
            except Exception as e:
                logger.error(f"[{self.role_name}] step={step} LLM 调用失败: {e}")
                return {
                    "final_answer": "",
                    "parsed": {"raw": "", "error": str(e)},
                    "trace": trace,
                }

            # 没有 tool_calls → Final Answer
            if not msg.tool_calls:
                thought = msg.content or ""
                trace.append({"thought": thought, "action": None, "observation": None})
                logger.info(f"[{self.role_name}] step={step} final_answer (iters={step + 1})")
                return {
                    "final_answer": thought,
                    "parsed": self._safe_json_parse(thought),
                    "trace": trace,
                }

            # 有 tool_calls → dispatch
            thought = msg.content or ""
            # 把 assistant 消息（含 tool_calls）加回 messages
            messages.append(self._serialize_assistant_msg(msg))

            for tc in msg.tool_calls:
                action_name = tc.function.name
                args = self._safe_json_parse(tc.function.arguments)
                observation = self._dispatch_tool(action_name, args)
                trace.append({
                    "thought": thought,
                    "action": action_name,
                    "action_args": args,
                    "observation": observation,
                })
                logger.info(f"[{self.role_name}] step={step} action={action_name}")
                # 把 tool 结果加回 messages（OpenAI 协议）
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": observation,
                })

        # 达上限强制收尾
        logger.warning(f"[{self.role_name}] 达 max_iterations={self.max_iterations}，强制收尾")
        messages.append({
            "role": "user",
            "content": "已达工具调用上限，请立即基于现有信息给出最终结论（纯 JSON 字符串）。",
        })
        try:
            msg = llm_client.chat(
                model=self.model,
                messages=messages,
                tools=None,  # 不再允许调工具
                temperature=self.temperature,
            )
            thought = msg.content or ""
            trace.append({"thought": thought, "action": None, "observation": None})
            return {
                "final_answer": thought,
                "parsed": self._safe_json_parse(thought),
                "trace": trace,
            }
        except Exception as e:
            logger.error(f"[{self.role_name}] 收尾调用失败: {e}")
            return {
                "final_answer": "",
                "parsed": {"raw": "", "error": str(e)},
                "trace": trace,
            }

    def _dispatch_tool(self, name: str, args: dict) -> str:
        """执行 tool_call，返回 observation 字符串（截断防爆）。"""
        fn = self.tool_map.get(name)
        if fn is None:
            return f"[错误] 未知工具: {name}"
        try:
            result = fn(**args) if isinstance(args, dict) else fn()
            text = str(result)
        except Exception as e:
            text = f"[工具执行错误] {name}: {e}"
            logger.error(text)
        # 截断防爆（最长 2000 字符）
        if len(text) > 2000:
            text = text[:2000] + "\n... (已截断)"
        return text

    @staticmethod
    def _serialize_assistant_msg(msg) -> dict:
        """把 OpenAI SDK 的 ChatCompletionMessage 转成可序列化的 dict。
        必须保留 tool_calls 字段，否则下一轮 LLM 会报错。
        """
        out: Dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        return out

    @staticmethod
    def _safe_json_parse(text: str) -> dict:
        """容错解析 JSON。失败时返回 {"raw": text}。"""
        if not text:
            return {"raw": ""}
        # 去掉可能的 markdown 代码块标记
        s = text.strip()
        if s.startswith("```"):
            # 去掉 ```json 或 ``` 开头和结尾 ```
            s = s.split("\n", 1)[-1] if "\n" in s else s.lstrip("`")
            s = s.removesuffix("```").strip()
        try:
            return json.loads(s)
        except Exception:
            # 尝试提取第一个 { ... } 块
            first = s.find("{")
            last = s.rfind("}")
            if first >= 0 and last > first:
                try:
                    return json.loads(s[first : last + 1])
                except Exception:
                    pass
            return {"raw": text}
