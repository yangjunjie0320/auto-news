from __future__ import annotations

from ..models import Source
from .autohome import AutohomeNewbrandSource
from .base import FetchError, SourceFetcher, fetch_html, parse_cn_date, try_parse_cn_date
from .sina import SinaNewcarSource
from .sina_news import SinaNewcarNewsSource
from .yiche import YicheUserSource

# key -> 抓取器类。sources.yaml 里按 key 启用，构造时注入 name。
_REGISTRY: dict[str, type] = {
    YicheUserSource.key: YicheUserSource,
    SinaNewcarSource.key: SinaNewcarSource,
    SinaNewcarNewsSource.key: SinaNewcarNewsSource,
    AutohomeNewbrandSource.key: AutohomeNewbrandSource,
}


def build_fetchers(sources: list[Source]) -> list[SourceFetcher]:
    fetchers: list[SourceFetcher] = []
    for src in sources:
        if not src.enabled:
            continue
        cls = _REGISTRY.get(src.key)
        if cls is None:
            raise ValueError(f"unknown source key: {src.key} (known: {list(_REGISTRY)})")
        fetchers.append(cls(name=src.name))
    return fetchers


__all__ = [
    "FetchError",
    "SourceFetcher",
    "build_fetchers",
    "fetch_html",
    "parse_cn_date",
    "try_parse_cn_date",
]
