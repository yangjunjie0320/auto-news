from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from ..models import Post
from .base import (
    ArticleDetail,
    FetchError,
    fetch_html,
    is_allowed_https_url,
    parse_cn_date,
    try_parse_cn_date,
)

logger = logging.getLogger(__name__)

INDEX_URL = "https://auto.sina.com.cn/newcar/index.d.html"
_ARTICLE_HOSTS = frozenset({"auto.sina.com.cn"})

_DETAIL_CONCURRENCY = 5
_DETAIL_TIMEOUT = 10.0
_MAX_LIST_ITEMS = 30
_FULL_TEXT_MAX_CHARS = 12_000
_ARTICLE_ID_RE = re.compile(r"/detail-([a-z0-9]+)\.shtml", re.IGNORECASE)
_INTRO_TAIL_RE = re.compile(r"\s*查看全文\s*>*\s*$")


@dataclass(frozen=True)
class SinaNewsItem:
    mid: str
    url: str
    title: str
    date_text: str
    intro: str = ""
    image_url: str = ""


def _abs_url(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http://"):
        return "https://" + href[len("http://") :]
    if href.startswith("https://"):
        return href
    return urljoin(INDEX_URL, href)


def parse_list(html: str) -> list[SinaNewsItem]:
    """解析专题页左侧真实新闻流，排除右侧推荐阅读。"""
    tree = HTMLParser(html)
    items: list[SinaNewsItem] = []
    seen: set[str] = set()
    for node in tree.css("div.con div.single div.s-left"):
        link = node.css_first("h3 a")
        if link is None:
            continue
        url = _abs_url(link.attributes.get("href") or "")
        match = _ARTICLE_ID_RE.search(url)
        title = re.sub(r"\s+", " ", link.text(strip=True)).strip()
        if not title or match is None or not is_allowed_https_url(url, _ARTICLE_HOSTS):
            continue
        mid = f"sina-news-{match.group(1).lower()}"
        if mid in seen:
            continue
        seen.add(mid)

        date_node = node.css_first("span.time")
        date_text = date_node.text(strip=True) if date_node is not None else ""

        intro_node = node.css_first("p.intro")
        intro = intro_node.text(strip=True) if intro_node is not None else ""
        intro = _INTRO_TAIL_RE.sub("", re.sub(r"\s+", " ", intro)).strip()

        image_url = ""
        image = node.css_first("a.img img")
        if image is not None:
            image_url = _abs_url(
                image.attributes.get("data-original") or image.attributes.get("src") or ""
            )

        items.append(
            SinaNewsItem(
                mid=mid,
                url=url,
                title=title,
                date_text=date_text,
                intro=intro,
                image_url=image_url,
            )
        )
    return items


def _paragraphs(container: object) -> tuple[str, bool]:
    parts: list[str] = []
    for node in container.css("p"):
        text = re.sub(r"\s+", " ", node.text(strip=True)).strip()
        if text and not text.startswith(("责任编辑", "新浪汽车公众号")):
            parts.append(text)
    joined = "\n\n".join(parts)
    if len(joined) < 80:
        raw = container.text(separator="\n", strip=True)
        lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines()]
        joined = "\n\n".join(
            line
            for line in dict.fromkeys(lines)
            if line and not line.startswith(("责任编辑", "新浪汽车公众号"))
        )
    return joined[:_FULL_TEXT_MAX_CHARS], len(joined) > _FULL_TEXT_MAX_CHARS


def parse_detail(html: str) -> ArticleDetail:
    tree = HTMLParser(html)
    published_at = None
    # 新浪部分页面的 article:published_time 与正文可见时间冲突；应以页面
    # 显示的 span.date 为准，meta 只作为旧模板兜底。
    date_node = tree.css_first("span.date")
    if date_node is not None:
        match = re.search(
            r"\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?",
            date_node.text(strip=True),
        )
        if match:
            published_at = try_parse_cn_date(match.group(0))
    if published_at is None:
        for meta in tree.css("meta"):
            if meta.attributes.get("property") != "article:published_time":
                continue
            raw = meta.attributes.get("content") or ""
            if raw.strip():
                published_at = try_parse_cn_date(raw)
                break

    container = tree.css_first("#artibody")
    if container is None:
        return ArticleDetail(published_at=published_at)
    text, truncated = _paragraphs(container)
    return ArticleDetail(text=text, published_at=published_at, truncated=truncated)


async def _fetch_detail(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, item: SinaNewsItem
) -> ArticleDetail:
    async with sem:
        detail = ArticleDetail()
        try:
            html = await fetch_html(
                client,
                item.url,
                timeout=_DETAIL_TIMEOUT,
                referer=INDEX_URL,
                allowed_hosts=_ARTICLE_HOSTS,
            )
            detail = parse_detail(html)
            if detail.text:
                return detail
        except FetchError as exc:
            logger.debug("新浪详情抓取失败，回退列表摘要: %s (%s)", item.url, exc)
        except Exception:
            logger.warning("新浪详情解析异常，回退列表摘要: %s", item.url, exc_info=True)
    return ArticleDetail(
        published_at=(
            detail.published_at or (try_parse_cn_date(item.date_text) if item.date_text else None)
        )
    )


def _build_post(key: str, name: str, item: SinaNewsItem, detail: ArticleDetail) -> Post:
    full_text = detail.text.strip()
    fallback = item.intro.strip()
    text_plain = f"{item.title}\n\n{fallback}" if fallback else item.title
    return Post(
        uid=key,
        screen_name=name,
        mid=item.mid,
        url=item.url,
        created_at=detail.published_at or parse_cn_date(item.date_text),
        created_at_is_precise=detail.published_at is not None,
        title=item.title,
        text_plain=text_plain,
        full_text=full_text,
        image_urls=[item.image_url] if item.image_url else [],
    )


class SinaNewcarNewsSource:
    key = "sina-newcar-news"
    name = "新浪汽车·新车资讯"

    def __init__(self, name: str | None = None) -> None:
        if name:
            self.name = name

    async def fetch(self, client: httpx.AsyncClient) -> list[Post]:
        html = await fetch_html(
            client,
            INDEX_URL,
            referer="https://auto.sina.com.cn/",
            allowed_hosts=_ARTICLE_HOSTS,
        )
        items = parse_list(html)[:_MAX_LIST_ITEMS]
        if not items:
            raise FetchError(f"sina newcar news: no items parsed from {INDEX_URL}")
        sem = asyncio.Semaphore(_DETAIL_CONCURRENCY)
        details = await asyncio.gather(*(_fetch_detail(client, sem, item) for item in items))
        return [
            _build_post(self.key, self.name, item, detail)
            for item, detail in zip(items, details, strict=True)
        ]
