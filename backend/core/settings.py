"""应用配置管理"""
import os
from pathlib import Path
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

    # LLM 运行时配置（前端「LLM 配置」页面可热改，无配置时以下面三项为默认值）
    LLM_MODEL: str = Field(default="qwen-plus", description="默认LLM模型名称")
    LLM_CONFIG_PATH: str = Field(
        default="",
        description="LLM运行时配置文件路径，默认 <DATA_ROOT_DIR>/../config/llm_config.json",
    )

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
