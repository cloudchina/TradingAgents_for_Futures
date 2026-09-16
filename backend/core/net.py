"""网络层统一治理

问题：akshare 绝大多数接口内部使用 requests 且不传 timeout，一旦交易所官网被
反爬拦截后长时间不响应（或连接半开），整个更新任务会被无限期阻塞，
只有外层的 6 小时总超时兜底。

方案：在进程启动时为 requests.Session.request 注入默认超时。
akshare 的 requests.get / Session.request 最终都会走到这里，因此一次补丁即可覆盖
全部第三方数据源调用；已显式传 timeout 的调用不受影响。
"""
import requests
from loguru import logger

DEFAULT_TIMEOUT = 30
_installed = False


def install_default_timeout(seconds: int = DEFAULT_TIMEOUT) -> None:
    """为 requests（含 akshare 内部调用）注入默认超时，幂等。"""
    global _installed
    if _installed:
        return

    original_request = requests.Session.request

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", seconds)
        return original_request(self, method, url, **kwargs)

    request.__doc__ = original_request.__doc__
    requests.Session.request = request
    _installed = True
    logger.debug(f"已为 requests 注入默认超时: {seconds}s（akshare 等第三方数据源通用）")
