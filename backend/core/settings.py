"""应用配置管理"""
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """全局配置"""

    # API配置
    DASHSCOPE_API_KEY: str = Field(default="", description="百炼API密钥")
    BAILIAN_BASE_URL: str = Field(
        default="https://llm-4l8sihiptwf8zykd.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        description="百炼基础URL",
    )
    SERPER_API_KEY: str = Field(default="", description="Serper API密钥")

    # SMTP 邮件配置（auto_email 自动发送使用；普通配置放 backend/.env 即可，系统环境变量可覆盖）
    SMTP_HOST: str = Field(default="", description="SMTP 服务器地址，如 smtp.qq.com")
    SMTP_PORT: int = Field(default=465, description="SMTP 端口（465=SSL；587/25=STARTTLS）")
    SMTP_USER: str = Field(default="", description="SMTP 发件账号")
    SMTP_PASSWORD: str = Field(default="", description="SMTP 授权码/密码")
    SMTP_FROM: str = Field(default="", description="SMTP 发件人显示地址（可选，默认同 SMTP_USER）")
    SMTP_TO: str = Field(default="", description="SMTP 收件人，多个用逗号/分号分隔")

    # 服务配置
    HOST: str = Field(default="0.0.0.0", description="服务监听地址")
    PORT: int = Field(default=8000, description="服务端口")
    DEBUG: bool = Field(default=True, description="调试模式")

    # 数据路径
    DATA_ROOT_DIR: str = Field(default="/app/data/qihuo/database", description="数据根目录")
    RESULTS_DIR: str = Field(default="/app/data/qihuo/trading_agents_results", description="结果目录")
    LOGS_DIR: str = Field(default="/app/data/logs", description="日志目录")
    CACHE_DIR: str = Field(default="/app/data/qihuo/cache/analysis", description="缓存目录")

    # 系统配置
    DEFAULT_DAYS_BACK: int = Field(default=3, description="默认回溯天数")
    MAX_NEWS_PER_CATEGORY: int = Field(default=50, description="每类最大新闻数")
    REQUEST_DELAY: float = Field(default=1.0, description="API请求间隔(秒)")

    # 支持的期货品种
    SUPPORTED_COMMODITIES: Dict[str, Any] = Field(default_factory=lambda: {
        "铜": {"symbol": "CU", "exchange": "SHFE", "category": "有色金属"},
        "铝": {"symbol": "AL", "exchange": "SHFE", "category": "有色金属"},
        "锌": {"symbol": "ZN", "exchange": "SHFE", "category": "有色金属"},
        "铅": {"symbol": "PB", "exchange": "SHFE", "category": "有色金属"},
        "镍": {"symbol": "NI", "exchange": "SHFE", "category": "有色金属"},
        "锡": {"symbol": "SN", "exchange": "SHFE", "category": "有色金属"},
        "黄金": {"symbol": "AU", "exchange": "SHFE", "category": "贵金属"},
        "白银": {"symbol": "AG", "exchange": "SHFE", "category": "贵金属"},
        "螺纹钢": {"symbol": "RB", "exchange": "SHFE", "category": "黑色系"},
        "热卷": {"symbol": "HC", "exchange": "SHFE", "category": "黑色系"},
        "铁矿石": {"symbol": "I", "exchange": "DCE", "category": "黑色系"},
        "焦炭": {"symbol": "J", "exchange": "DCE", "category": "黑色系"},
        "焦煤": {"symbol": "JM", "exchange": "DCE", "category": "黑色系"},
        "原油": {"symbol": "SC", "exchange": "INE", "category": "能源化工"},
        "燃料油": {"symbol": "FU", "exchange": "SHFE", "category": "能源化工"},
        "沥青": {"symbol": "BU", "exchange": "SHFE", "category": "能源化工"},
        "橡胶": {"symbol": "RU", "exchange": "SHFE", "category": "能源化工"},
        "豆粕": {"symbol": "M", "exchange": "DCE", "category": "农产品"},
        "豆油": {"symbol": "Y", "exchange": "DCE", "category": "农产品"},
        "棕榈油": {"symbol": "P", "exchange": "DCE", "category": "农产品"},
        "玉米": {"symbol": "C", "exchange": "DCE", "category": "农产品"},
        "白糖": {"symbol": "SR", "exchange": "CZCE", "category": "农产品"},
        "棉花": {"symbol": "CF", "exchange": "CZCE", "category": "农产品"},
        "PTA": {"symbol": "TA", "exchange": "CZCE", "category": "能源化工"},
        "甲醇": {"symbol": "MA", "exchange": "CZCE", "category": "能源化工"},
        "玻璃": {"symbol": "FG", "exchange": "CZCE", "category": "能源化工"},
        "纯碱": {"symbol": "SA", "exchange": "CZCE", "category": "能源化工"},
        "尿素": {"symbol": "UR", "exchange": "CZCE", "category": "能源化工"},
        "苹果": {"symbol": "AP", "exchange": "CZCE", "category": "农产品"},
        "红枣": {"symbol": "CJ", "exchange": "CZCE", "category": "农产品"},
        "菜粕": {"symbol": "RM", "exchange": "CZCE", "category": "农产品"},
        "菜籽油": {"symbol": "OI", "exchange": "CZCE", "category": "农产品"},
        "烧碱": {"symbol": "SH", "exchange": "CZCE", "category": "能源化工"},
        "对二甲苯": {"symbol": "PX", "exchange": "CZCE", "category": "能源化工"},
        "氧化铝": {"symbol": "AO", "exchange": "SHFE", "category": "有色金属"},
        "丁二烯橡胶": {"symbol": "BR", "exchange": "SHFE", "category": "能源化工"},
    })

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()


def get_data_path(*parts: str) -> Path:
    """获取数据目录下的路径"""
    return Path(settings.DATA_ROOT_DIR) / Path(*parts)


def get_results_path(*parts: str) -> Path:
    """获取结果目录下的路径"""
    return Path(settings.RESULTS_DIR) / Path(*parts)


def get_cache_path(*parts: str) -> Path:
    """获取缓存目录下的路径"""
    return Path(settings.CACHE_DIR) / Path(*parts)


def get_all_symbols() -> List[str]:
    """获取所有支持的品种代码"""
    return [info["symbol"] for info in settings.SUPPORTED_COMMODITIES.values()]


def get_symbol_name(symbol: str) -> str:
    """根据代码获取品种名称"""
    for name, info in settings.SUPPORTED_COMMODITIES.items():
        if info["symbol"] == symbol.upper():
            return name
    return symbol.upper()
