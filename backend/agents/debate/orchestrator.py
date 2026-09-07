"""辩论编排器（DebateOrchestrator）

多空辩论状态机：
- Round 1: Bull 独立 ReAct → Bear 看到 Bull 结论后 ReAct → Macro 裁判
- Round 2+: Bull 看到 Bear 上一轮反驳 → Bear 看到 Bull 本轮反驳 → Macro 裁判
- 终止条件：max_rounds=0 跳过；consensus=True 立即停；round >= max_rounds 停
"""
from typing import List, Dict, Any, Optional

from loguru import logger

from agents.react_agent import ReActAgent
from agents.tools.tool_specs import ALL_TOOLS
from agents.tools.data_reader import read_module_data
from agents.tools.news_tool import search_news_data
from agents.prompts.system_prompts import BULL_ANALYST_PROMPT, BEAR_ANALYST_PROMPT
from agents.debate.consensus import check_consensus


# 共用工具映射表
TOOL_MAP = {
    "read_module_data": read_module_data,
    "search_news_data": search_news_data,
}


class DebateOrchestrator:
    """多空辩论编排器"""

    def __init__(self, model: str, max_rounds: int, symbol: str, modules: List[str]):
        self.model = model
        self.max_rounds = max_rounds
        self.symbol = symbol
        self.modules = modules

        # 构造 Bull / Bear agent（共用工具集）
        self.bull_agent = ReActAgent(
            role_name="BullAnalyst",
            system_prompt=BULL_ANALYST_PROMPT,
            tools=ALL_TOOLS,
            tool_map=TOOL_MAP,
            model=model,
        )
        self.bear_agent = ReActAgent(
            role_name="BearAnalyst",
            system_prompt=BEAR_ANALYST_PROMPT,
            tools=ALL_TOOLS,
            tool_map=TOOL_MAP,
            model=model,
        )

    def run(self) -> Dict[str, Any]:
        """执行完整辩论。

        Returns:
            {
                "rounds": 配置的轮数,
                "actual_rounds": 实际跑了几轮,
                "terminated_reason": "consensus"|"max_rounds"|"skipped",
                "bull_final": {...}, "bear_final": {...},
                "consensus_checks": [...],   # 每轮裁判结果
                "history": [{                # 完整历史，给前端展示
                    "round": 1,
                    "bull": {"view": {...}, "trace": [...]},
                    "bear": {"view": {...}, "trace": [...]},
                    "referee": {...}
                }],
                "winner": "bullish"|"bearish"|"split"
            }
        """
        # 跳过辩论（debate_rounds=0）
        if self.max_rounds <= 0:
            logger.info(f"[DebateOrchestrator] max_rounds=0，跳过辩论，各跑一次 ReAct 收尾")
            bull_res = self._run_bull(round_idx=1, prev_round=None)
            bear_res = self._run_bear(round_idx=1, bull_view=bull_res["view"])
            return {
                "rounds": self.max_rounds,
                "actual_rounds": 1,
                "terminated_reason": "skipped",
                "bull_final": bull_res["view"],
                "bear_final": bear_res["view"],
                "consensus_checks": [],
                "history": [
                    {
                        "round": 1,
                        "bull": bull_res,
                        "bear": bear_res,
                        "referee": None,
                    }
                ],
                "winner": self._decide_winner(bull_res["view"], bear_res["view"]),
            }

        history: List[Dict[str, Any]] = []
        consensus_checks: List[Dict[str, Any]] = []
        terminated_reason = "max_rounds"
        bull_final: Dict[str, Any] = {}
        bear_final: Dict[str, Any] = {}

        for round_idx in range(1, self.max_rounds + 1):
            logger.info(f"[DebateOrchestrator] 开始 round={round_idx}/{self.max_rounds}")
            prev_round = history[-1] if history else None

            # Bull 先发言（Round 2+ 看到 Bear 上一轮反驳）
            bull_res = self._run_bull(round_idx, prev_round)
            # Bear 看到 Bull 本轮结论后反驳
            bear_res = self._run_bear(round_idx, bull_view=bull_res["view"])

            # Macro 裁判
            referee = check_consensus(bull_res["view"], bear_res["view"], self.model)
            consensus_checks.append(referee)

            history.append({
                "round": round_idx,
                "bull": bull_res,
                "bear": bear_res,
                "referee": referee,
            })

            bull_final = bull_res["view"]
            bear_final = bear_res["view"]

            # 达成共识 → 立即停
            if referee.get("consensus"):
                terminated_reason = "consensus"
                logger.info(
                    f"[DebateOrchestrator] round={round_idx} 达成共识，提前终止"
                )
                break

        return {
            "rounds": self.max_rounds,
            "actual_rounds": len(history),
            "terminated_reason": terminated_reason,
            "bull_final": bull_final,
            "bear_final": bear_final,
            "consensus_checks": consensus_checks,
            "history": history,
            "winner": self._decide_winner(bull_final, bear_final),
        }

    def _run_bull(self, round_idx: int, prev_round: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """执行 Bull 的 ReAct。

        Round 1: 独立分析
        Round 2+: 看到 Bear 上一轮反驳
        """
        if prev_round is None:
            user_msg = (
                f"品种代码: {self.symbol}\n"
                f"可用分析模块: {', '.join(self.modules)}\n"
                f"请调用 read_module_data 查询至少 2 个模块的数据后给出看多观点。"
            )
        else:
            bear_prev = prev_round.get("bear", {})
            bear_view = bear_prev.get("view", {}) if "view" in bear_prev else bear_prev.get("parsed", {})
            user_msg = (
                f"品种代码: {self.symbol}\n"
                f"=== Round {round_idx - 1} Bear 的反驳 ===\n"
                f"方向: {bear_view.get('direction')}\n"
                f"置信度: {bear_view.get('confidence')}\n"
                f"论点: {bear_view.get('summary')}\n"
                f"关键证据: {bear_view.get('key_evidence')}\n"
                f"=== 请回应 Bear 的反驳，可复查数据，给出本轮看多观点 ==="
            )
        return self._wrap_agent_result(self.bull_agent.run(user_msg))

    def _run_bear(self, round_idx: int, bull_view: Dict[str, Any]) -> Dict[str, Any]:
        """执行 Bear 的 ReAct，看到 Bull 本轮结论后反驳。"""
        user_msg = (
            f"品种代码: {self.symbol}\n"
            f"=== Round {round_idx} Bull 的论点 ===\n"
            f"方向: {bull_view.get('direction')}\n"
            f"置信度: {bull_view.get('confidence')}\n"
            f"论点: {bull_view.get('summary')}\n"
            f"关键证据: {bull_view.get('key_evidence')}\n"
            f"=== 请反驳 Bull 的论点，可查数据，给出本轮看空观点 ==="
        )
        return self._wrap_agent_result(self.bear_agent.run(user_msg))

    @staticmethod
    def _wrap_agent_result(agent_res: Dict[str, Any]) -> Dict[str, Any]:
        """把 ReActAgent.run 返回的结构包装成 history 元素格式。

        agent_res: {"final_answer": str, "parsed": dict, "trace": [...]}
        返回: {"view": dict, "trace": [...], "final_answer": str}
        """
        return {
            "view": agent_res.get("parsed", {}),
            "trace": agent_res.get("trace", []),
            "final_answer": agent_res.get("final_answer", ""),
        }

    @staticmethod
    def _decide_winner(bull: Dict[str, Any], bear: Dict[str, Any]) -> str:
        """按双方置信度加权定胜负。"""
        try:
            b_conf = float(bull.get("confidence", 0.0))
            s_conf = float(bear.get("confidence", 0.0))
        except Exception:
            return "split"

        if b_conf > s_conf + 0.1:
            return "bullish"
        if s_conf > b_conf + 0.1:
            return "bearish"
        return "split"
