"""无人值守单次自动分析脚本（供 cron / Windows 任务计划程序调用）

用法示例：
    # 方式1：直接命令行传参
    python run_scheduled_task.py --commodities AU,RB,I --modules technical,term_structure,basis --auto-word --no-update-data

    # 方式2：JSON 配置文件（与 ScheduledConfig 结构一致）
    python run_scheduled_task.py --config path/to/scheduled_config.json

    # 方式3：环境变量
    set SCHEDULED_COMMODITIES=AU,RB,I
    set SCHEDULED_ANALYSIS_MODULES=technical,term_structure
    set SCHEDULED_UPDATE_DATA=false
    set SCHEDULED_AUTO_WORD=true
    set SCHEDULED_AUTO_EMAIL=true
    set SCHEDULED_AI_MODEL=qwen-plus
    python run_scheduled_task.py

运行前需配置系统环境变量：DASHSCOPE_API_KEY（模型）、如需发邮件再配置 SMTP_*，
如需更新数据模块的取数则确保网络可达（akshare）。
"""
import argparse
import json
import os
import sys
from pathlib import Path

# 保证以“仓库根/任意 cwd”运行都能导入 backend 包
_BACKEND_DIR = str(Path(__file__).resolve().parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from loguru import logger  # noqa: E402

from models.analysis import ScheduledConfig  # noqa: E402
from services.scheduled_service import run_scheduled_cycle_once  # noqa: E402

DEFAULT_MODULES = [
    "inventory", "positioning", "term_structure", "technical", "basis", "news",
]


def _parse_bool(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on", "是")


def build_config_from_args(argv=None) -> ScheduledConfig:
    parser = argparse.ArgumentParser(description="商品期货 AI 无人值守自动分析")
    parser.add_argument("--config", help="JSON 配置文件路径（ScheduledConfig 结构）")
    parser.add_argument("--commodities", help="品种代码，逗号分隔，如 AU,RB,I")
    parser.add_argument("--modules", help="分析模块，逗号分隔")
    parser.add_argument("--mode", choices=["complete_flow", "analyst_only"], default="complete_flow")
    parser.add_argument("--ai-model", default="qwen-plus")
    parser.add_argument("--debate-rounds", type=int, default=3)
    parser.add_argument("--update-data", dest="update_data", action="store_true", default=None,
                        help="分析前自动更新数据")
    parser.add_argument("--no-update-data", dest="update_data", action="store_false")
    parser.add_argument("--auto-word", dest="auto_word", action="store_true", default=None)
    parser.add_argument("--no-auto-word", dest="auto_word", action="store_false")
    parser.add_argument("--auto-email", dest="auto_email", action="store_true", default=None)
    parser.add_argument("--no-auto-email", dest="auto_email", action="store_false")
    args = parser.parse_args(argv)

    config_dict = {}
    # 1) 配置文件优先
    if args.config:
        cfg_path = Path(args.config)
        if not cfg_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {cfg_path}")
        config_dict = json.loads(cfg_path.read_text(encoding="utf-8"))

    # 2) 环境变量
    commodities_env = os.environ.get("SCHEDULED_COMMODITIES", "")
    modules_env = os.environ.get("SCHEDULED_ANALYSIS_MODULES", "")
    config_dict.setdefault("commodities", [c.strip() for c in commodities_env.split(",") if c.strip()])
    if modules_env:
        config_dict["analysis_modules"] = [m.strip() for m in modules_env.split(",") if m.strip()]
    config_dict.setdefault("analysis_mode", os.environ.get("SCHEDULED_MODE", "complete_flow"))
    config_dict.setdefault("ai_model", os.environ.get("SCHEDULED_AI_MODEL", "qwen-plus"))
    config_dict.setdefault("debate_rounds", int(os.environ.get("SCHEDULED_DEBATE_ROUNDS", "3")))
    config_dict.setdefault("auto_word", _parse_bool(os.environ.get("SCHEDULED_AUTO_WORD", "false")))
    config_dict.setdefault("auto_email", _parse_bool(os.environ.get("SCHEDULED_AUTO_EMAIL", "false")))
    config_dict.setdefault(
        "update_data_before_analysis",
        _parse_bool(os.environ.get("SCHEDULED_UPDATE_DATA", "true")),
    )

    # 3) 命令行参数覆盖
    if args.commodities:
        config_dict["commodities"] = [c.strip() for c in args.commodities.split(",") if c.strip()]
    if args.modules:
        config_dict["analysis_modules"] = [m.strip() for m in args.modules.split(",") if m.strip()]
    if args.update_data is not None:
        config_dict["update_data_before_analysis"] = args.update_data
    if args.auto_word is not None:
        config_dict["auto_word"] = args.auto_word
    if args.auto_email is not None:
        config_dict["auto_email"] = args.auto_email
    config_dict["analysis_mode"] = args.mode
    config_dict["ai_model"] = args.ai_model
    config_dict["debate_rounds"] = args.debate_rounds
    config_dict["enabled"] = True

    # 默认值
    config_dict.setdefault("analysis_modules", list(DEFAULT_MODULES))
    config_dict.setdefault("commodities", [])
    if not config_dict["commodities"]:
        raise SystemExit("未指定分析品种：请通过 --commodities / SCHEDULED_COMMODITIES / --config 提供")

    return ScheduledConfig(**config_dict)


def main() -> int:
    logger.add(sys.stderr, level="INFO")
    try:
        config = build_config_from_args()
    except SystemExit as e:
        print(str(e) if str(e) else "", file=sys.stderr)
        return 2
    except Exception as e:
        logger.error(f"配置解析失败: {e}")
        return 2

    logger.info(f"开始无人值守分析: 品种={config.commodities}, 模块={config.analysis_modules}")
    ok = run_scheduled_cycle_once(config, timeout=60 * 60 * 6)
    if ok:
        logger.info("无人值守分析全流程完成")
        return 0
    logger.error("无人值守分析未能在超时时间内完成")
    return 1


if __name__ == "__main__":
    sys.exit(main())
