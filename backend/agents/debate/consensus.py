"""共识判定：MacroAnalyst 裁判调用

不走 ReAct，单次 LLM 调用（裁判只看双方论点，不需要查数据）。
代码兜底防 LLM 幻觉：方向相反则强制 consensus=False。
"""
import json
from typing import Dict, Any

from loguru import logger

from agents.llm_client import llm_client
from agents.prompts.system_prompts import MACRO_REFEREE_PROMPT


def check_consensus(bull_view: Dict[str, Any], bear_view: Dict[str, Any], model: str) -> Dict[str, Any]:
    """判定本轮多空双方是否达成共识。

    Args:
        bull_view: {"direction":"long","confidence":0.65,"summary":"..."}
        bear_view: 同结构
        model: LLM 模型名

    Returns:
        {
            "consensus": bool,
            "bull_dir": str, "bear_dir": str,
            "confidence_gap": float,
            "reason": str,
            "next_action": "continue"|"stop"
        }
    """
    bull_dir = bull_view.get("direction", "neutral")
    bear_dir = bear_view.get("direction", "neutral")
    bull_conf = float(bull_view.get("confidence", 0.0))
    bear_conf = float(bear_view.get("confidence", 0.0))
    gap = round(abs(bull_conf - bear_conf), 3)

    # 构造裁判输入
    user_msg = (
        f"本轮辩论结果如下，请判定是否达成共识：\n\n"
        f"【多头分析师】\n方向: {bull_dir}\n置信度: {bull_conf}\n"
        f"论点摘要: {bull_view.get('summary', '')}\n"
        f"关键证据: {json.dumps(bull_view.get('key_evidence', []), ensure_ascii=False)}\n\n"
        f"【空头分析师】\n方向: {bear_dir}\n置信度: {bear_conf}\n"
        f"论点摘要: {bear_view.get('summary', '')}\n"
        f"关键证据: {json.dumps(bear_view.get('key_evidence', []), ensure_ascii=False)}"
    )

    try:
        msg = llm_client.chat(
            model=model,
            messages=[
                {"role": "system", "content": MACRO_REFEREE_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            tools=None,
            temperature=0.2,  # 裁判要稳定，温度调低
        )
        text = msg.content or ""
        # 复用 ReActAgent 的 JSON 解析逻辑（避免循环依赖，内联简化版）
        parsed = _safe_json_parse(text)
    except Exception as e:
        logger.error(f"[MacroAnalyst] 裁判调用失败: {e}")
        # 失败时走纯规则判定
        parsed = {}

    # 代码兜底：方向相反则强制 consensus=False（防 LLM 幻觉）
    rule_consensus = (bull_dir == bear_dir) and (gap <= 0.2)
    llm_consensus = bool(parsed.get("consensus", False)) if bull_dir == bear_dir else False

    # 优先采纳规则判定（更可靠），LLM 仅提供 reason
    final_consensus = rule_consensus
    reason = parsed.get("reason") or (
        f"方向{'一致' if bull_dir == bear_dir else '相反'}，置信度差={gap}"
        + ("，达成共识" if rule_consensus else "，未达成共识")
    )

    return {
        "consensus": final_consensus,
        "bull_dir": bull_dir,
        "bear_dir": bear_dir,
        "confidence_gap": gap,
        "reason": reason,
        "next_action": "stop" if final_consensus else "continue",
    }


def _safe_json_parse(text: str) -> dict:
    """容错解析 JSON（与 ReActAgent._safe_json_parse 同逻辑，内联避免循环导入）"""
    if not text:
        return {}
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s.lstrip("`")
        s = s.removesuffix("```").strip()
    try:
        return json.loads(s)
    except Exception:
        first = s.find("{")
        last = s.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(s[first : last + 1])
            except Exception:
                pass
        return {}
