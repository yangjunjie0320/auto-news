from __future__ import annotations

from ..config import Settings
from ..models import Source
from .autohome import AutohomeNewbrandSource
from .base import FetchError, SourceFetcher, fetch_html, parse_cn_date, try_parse_cn_date
from .sina import SinaNewcarSource
from .sina_news import SinaNewcarNewsSource
from .weibo import WeiboPoolSource, WeiboSearchSource
from .yiche import YicheUserSource

# key -> 抓取器类。sources.yaml 里按 key 启用，构造时注入 name。
_REGISTRY: dict[str, type] = {
    YicheUserSource.key: YicheUserSource,
    SinaNewcarSource.key: SinaNewcarSource,
    SinaNewcarNewsSource.key: SinaNewcarNewsSource,
    AutohomeNewbrandSource.key: AutohomeNewbrandSource,
    WeiboPoolSource.key: WeiboPoolSource,
    WeiboSearchSource.key: WeiboSearchSource,
}

# 需要 Settings 的抓取器（账号池路径、搜索词、限流延迟都在配置里）
_NEEDS_SETTINGS = {WeiboPoolSource.key, WeiboSearchSource.key}


def build_fetchers(sources: list[Source], settings: Settings | None = None) -> list[SourceFetcher]:
    fetchers: list[SourceFetcher] = []
    for src in sources:
        if not src.enabled:
            continue
        cls = _REGISTRY.get(src.key)
        if cls is None:
            raise ValueError(f"unknown source key: {src.key} (known: {list(_REGISTRY)})")
        if src.key in _NEEDS_SETTINGS:
            if settings is None:
                raise ValueError(f"source {src.key} requires settings")
            fetcher = cls(name=src.name, settings=settings)
        else:
            fetcher = cls(name=src.name)
        # 抓取节奏和来源类型是配置属性，不是抓取器的实现细节，
        # 由 sources.yaml 决定并挂到实例上供 monitor 与 pipeline 读取。
        fetcher.interval_seconds = src.interval_seconds
        fetcher.kind = src.kind
        fetchers.append(fetcher)
    return fetchers


__all__ = [
    "FetchError",
    "SourceFetcher",
    "build_fetchers",
    "fetch_html",
    "parse_cn_date",
    "try_parse_cn_date",
]
