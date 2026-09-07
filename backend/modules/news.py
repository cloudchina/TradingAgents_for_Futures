"""
News search via TianAPI — fetches futures market news headlines.
"""
import json
import os
import logging
import re
from datetime import date, timedelta, datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

TIAN_API_URL = "https://apis.tianapi.com/caijing/index"
TIAN_URL_ID = 136  # futures category news

# Synonyms for client-side keyword matching
_SYNONYMS = {
    "原油": ["原油", "石油", "油价"],
    "黄金": ["黄金", "金价", "贵金属"],
    "白银": ["白银", "银价"],
    "豆粕": ["豆粕", "大豆", "豆类"],
    "白糖": ["白糖", "食糖"],
    "铜": ["铜", "铜价", "有色金属"],
    "铝": ["铝", "铝价", "有色金属"],
    "锌": ["锌", "锌价", "有色金属"],
    "螺纹钢": ["螺纹钢", "螺纹", "钢材", "钢铁"],
    "铁矿石": ["铁矿石", "铁矿", "矿石"],
    "PTA": ["PTA", "精对苯二甲酸", "聚酯"],
    "甲醇": ["甲醇"],
    "天然橡胶": ["橡胶", "天然橡胶"],
    "棉花": ["棉花", "棉价"],
    "棕榈油": ["棕榈油", "棕榈", "油脂"],
    "豆油": ["豆油", "油脂"],
    "菜籽油": ["菜籽油", "菜油", "油脂"],
    "玉米": ["玉米"],
    "淀粉": ["淀粉"],
}

# Mapping from Chinese futures name to akshare futures_news_shmet category
_AKSHARE_CATEGORY_MAP = {
    "铜": "铜",
    "铝": "铝",
    "锌": "锌",
    "铅": "铅",
    "镍": "镍",
    "锡": "锡",
    "黄金": "贵金属",
    "白银": "贵金属",
    "金价": "贵金属",
    "银价": "贵金属",
}


def load_config():
    """Load news_config.json.

    搜索顺序（解决不同部署环境下 CWD 不同导致配置找不到的问题）：
      1. 环境变量 NEWS_CONFIG_PATH 指定路径；
      2. 仓库根目录 config/news_config.json（本地开发，相对本文件定位）；
      3. backend/config/news_config.json（随代码打包，供 Docker 镜像使用）；
      4. 相对当前工作目录的 config/news_config.json（兼容旧启动方式）。
    任何候选路径不存在时返回 {"enabled": False}，避免误开。
    """
    candidates = [
        os.environ.get("NEWS_CONFIG_PATH") or "",
        str(Path(__file__).resolve().parents[2] / "config" / "news_config.json"),
        str(Path(__file__).resolve().parents[1] / "config" / "news_config.json"),
        os.path.join(os.getcwd(), "config", "news_config.json"),
    ]
    for path in candidates:
        if not path:
            continue
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                logger.debug(f"Loaded news config from {path}")
                return cfg
            except Exception as e:
                logger.error(f"Failed to load news config {path}: {e}")
                return {"enabled": False}
    logger.warning("news_config.json not found under any candidate path; news search disabled")
    return {"enabled": False}


def _get_tian_api_key() -> str:
    """TIAN_API_KEY 通过系统环境变量注入（Windows/Linux 均不写进代码仓库）。"""
    key = os.environ.get("TIAN_API_KEY", "").strip()
    if not key:
        logger.warning("TIAN_API_KEY not set in environment; TianAPI news source will return empty")
    return key


def _fetch_recent_news(num: int = 40) -> list:
    """Fetch latest futures news from TianAPI (unfiltered)."""
    api_key = _get_tian_api_key()
    if not api_key:
        return []
    try:
        params = {
            "key": api_key,
            "urlid": TIAN_URL_ID,
            "num": min(num, 40),
            "page": 1,
        }
        resp = requests.get(TIAN_API_URL, params=params, timeout=15)
        res = resp.json()
        if res.get("code") != 200:
            logger.error(f"TianAPI error: {res.get('msg')}")
            return []
        return res["result"]["newslist"]
    except Exception as e:
        logger.error(f"News fetch error: {e}")
        return []


def _match_any(text: str, keywords: list) -> bool:
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            return True
    return False


def _parse_akshare_content(text: str) -> tuple:
    """Extract title from 【...】 and snippet from remainder."""
    m = re.match(r"【(.+?)】(.*)", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()[:200]
    # Fallback: first 50 chars as title, next 200 as snippet
    return text[:50], text[50:250] if len(text) > 50 else ""


def _resolve_akshare_categories(symbol_names: set) -> list:
    """Determine which akshare categories to query based on symbol Chinese names."""
    categories = set()
    has_unmapped = False
    for name in symbol_names:
        cat = _AKSHARE_CATEGORY_MAP.get(name)
        if cat:
            categories.add(cat)
        else:
            has_unmapped = True
    if has_unmapped or not categories:
        categories.add("全部")
    return list(categories)


def _fetch_akshare_news(symbol_names: set = None, days: int = 5) -> list:
    """Fetch news from akshare futures_news_shmet, filtered by date.

    Returns list of {title, description, url, ctime} matching TianAPI format.
    """
    try:
        import akshare as ak
    except ImportError:
        logger.warning("akshare not installed, falling back to TianAPI")
        return []

    if symbol_names:
        categories = _resolve_akshare_categories(symbol_names)
    else:
        categories = ["全部"]

    cutoff = datetime.now() - timedelta(days=days)
    seen = set()
    results = []

    for cat in categories:
        try:
            df = ak.futures_news_shmet(symbol=cat)
        except Exception as e:
            logger.warning(f"akshare fetch error for category '{cat}': {e}")
            continue

        if df is None or df.empty:
            continue

        for _, row in df.iterrows():
            pub_time = row.get("发布时间")
            content = str(row.get("内容", ""))

            # Date filter
            if pub_time is not None:
                if hasattr(pub_time, "to_pydatetime"):
                    pub_dt = pub_time.to_pydatetime()
                elif isinstance(pub_time, datetime):
                    pub_dt = pub_time
                else:
                    continue
                # Strip timezone for comparison with naive cutoff
                if hasattr(pub_dt, "tzinfo") and pub_dt.tzinfo is not None:
                    pub_dt = pub_dt.replace(tzinfo=None)
                if pub_dt < cutoff:
                    continue
                ctime_str = pub_dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                ctime_str = ""

            title, snippet = _parse_akshare_content(content)
            if not title:
                continue

            # Dedup within akshare results
            dedup_key = title[:20]
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            results.append({
                "title": title,
                "description": snippet,
                "url": "",
                "ctime": ctime_str,
            })

    return results


def search_news(query: str, num: int = 5, today_only: bool = False) -> list:
    """Search for news via TianAPI. Returns list of {title, snippet, link}.

    Args:
        today_only: If True, only return news from today.
    """
    cfg = load_config()
    if not cfg.get("enabled"):
        logger.debug("News search disabled")
        return []

    keywords = [kw.strip() for kw in query.split() if len(kw.strip()) >= 1]
    if not keywords:
        return []

    # Expand with synonyms
    expanded = []
    for kw in keywords:
        expanded.extend(_SYNONYMS.get(kw, [kw]))
    keywords = list(set(expanded))

    source = cfg.get("source", "tianapi")
    akshare_days = cfg.get("akshare_days", 5)

    news_list = []
    if source in ("tianapi", "both"):
        news_list.extend(_fetch_recent_news(40))
    if source in ("akshare", "both"):
        ak_items = _fetch_akshare_news(days=akshare_days)
        if source == "both":
            existing = {item.get("title", "")[:20] for item in news_list}
            for item in ak_items:
                if item.get("title", "")[:20] not in existing:
                    news_list.append(item)
        else:
            news_list = ak_items

    cutoff = date.today().isoformat() if today_only else ""

    results = []
    for item in news_list:
        ctime = item.get("ctime", "")
        if today_only and not ctime.startswith(cutoff):
            continue
        title = item.get("title", "")
        desc = item.get("description", "")
        if _match_any(title + desc, keywords):
            results.append({
                "title": title,
                "snippet": desc,
                "link": item.get("url", ""),
            })
            if len(results) >= num:
                break

    return results


def search_contract_news(symbol: str, name: str, direction: str) -> str:
    """Search news for a specific contract. Returns formatted string for LLM prompt."""
    dir_cn = "多头" if direction == "bullish" else "空头"
    query = f"{name} {dir_cn}"
    results = search_news(query, num=3)

    if not results:
        return ""

    lines = [f"### {symbol} {name} 相关新闻"]
    for r in results:
        lines.append(f"- {r['title']}: {r['snippet'][:120]}")
    return "\n".join(lines) + "\n"


# 🔧 清理(P2)：移除迁移遗留的死函数 get_contract_news / get_market_sentiment，
# 现行新闻检索统一走 search_news + search_contract_news（news_tool 调用）。
