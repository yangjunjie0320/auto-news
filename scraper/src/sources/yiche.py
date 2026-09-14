from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

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

# 目标页：易车「易车原创报道」个人主页全部内容
# https://i.yiche.com/u61014816/!all/
# 首屏服务端渲染，含约 10 条 div.article-item（标题/日期/链接/缩略图/摘要）。
#
# 坑记录：
#   1) 详情页 news.yiche.com 有时返回腾讯 WAF 验证码；直连取不到正文时，
#      按相同路径读取汽车产经镜像，仍失败才降级到列表页 a.desc。
#   2) 缩略图懒加载，真实地址在 <img ... data-original=...>，src 是 loading 占位图。
#   3) mid 用文章链接里 <articleId>.html 的数字 id，稳定不随标题变化。

LIST_URL = "https://i.yiche.com/u61014816/!all/"
_LIST_HOSTS = frozenset({"i.yiche.com"})
_ARTICLE_HOSTS = frozenset({"news.yiche.com"})
_MIRROR_HOSTS = frozenset({"www.autoreport.cn"})

# 详情抓取并发上限；WAF 场景下多为超时/被拦，控制并发避免拖慢整轮。
_DETAIL_CONCURRENCY = 5
_DETAIL_TIMEOUT = 10.0
_FULL_TEXT_MAX_CHARS = 12_000
_MIRROR_ORIGIN = "https://www.autoreport.cn"

# 列表 desc 末尾的「...查看更多>」引导文案，摘要里去掉。
_DESC_TAIL_RE = re.compile(r"\.*\s*查看更多\s*>?\s*$")
# 文章链接里的数字 id：/……/<articleId>.html
_ARTICLE_ID_RE = re.compile(r"/(\w+)\.html?(?:[?#]|$)")


@dataclass(frozen=True)
class ListItem:
    """列表页解析出的一条中间结构（纯解析产物，供 parse_list 返回）。"""

    article_id: str
    title: str
    url: str
    date_text: str
    image_url: str
    desc: str  # 列表页自带摘要（可能为空）


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
    if href.startswith("/"):
        return "https://i.yiche.com" + href
    return href


def _article_id(url: str) -> str:
    """从文章链接解析源内稳定唯一 id。取 <id>.html 段，优先数字文件名。"""
    m = _ARTICLE_ID_RE.search(url)
    if m:
        return m.group(1)
    # 兜底：取路径最后一段（去掉 query/fragment），避免用会变的标题。
    tail = re.split(r"[?#]", url, maxsplit=1)[0].rstrip("/").rsplit("/", 1)[-1]
    return tail


def _clean_desc(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    text = _DESC_TAIL_RE.sub("", text).strip()
    return text


def parse_list(html: str) -> list[ListItem]:
    """纯函数：解析首屏列表页 HTML，返回 ListItem 列表（不发网络请求）。"""
    tree = HTMLParser(html)
    items: list[ListItem] = []
    for node in tree.css("div.article-item"):
        link = node.css_first("h2.title a")
        if link is None:
            link = node.css_first("a.click-item")
        if link is None:
            continue
        url = _abs_url(link.attributes.get("href") or "")
        if not url or not is_allowed_https_url(url, _ARTICLE_HOSTS):
            continue
        title = link.text(strip=True)
        if not title:
            continue
        article_id = _article_id(url)
        if not article_id:
            continue

        span = node.css_first("div.bottom-right span")
        date_text = span.text(strip=True) if span is not None else ""

        img = node.css_first("img.lazyload") or node.css_first("img")
        image_url = ""
        if img is not None:
            raw = (
                img.attributes.get("data-original")
                or img.attributes.get("data-webp")
                or img.attributes.get("src")
                or ""
            )
            # 跳过懒加载占位图
            if raw and "loading" not in raw:
                image_url = _abs_url(raw)

        desc_node = node.css_first("a.desc")
        desc = _clean_desc(desc_node.text(strip=True)) if desc_node is not None else ""

        items.append(
            ListItem(
                article_id=article_id,
                title=title,
                url=url,
                date_text=date_text,
                image_url=image_url,
                desc=desc,
            )
        )
    return items


def _published_at(tree: HTMLParser) -> dt.datetime | None:
    for script in tree.css("script"):
        if script.attributes.get("type") != "application/ld+json":
            continue
        try:
            data = json.loads(script.text())
        except (json.JSONDecodeError, TypeError):
            continue
        records = data if isinstance(data, list) else [data]
        for record in records:
            if not isinstance(record, dict):
                continue
            raw = record.get("datePublished")
            if isinstance(raw, str) and raw.strip():
                return try_parse_cn_date(raw)
    return None


def _paragraphs(container: object) -> tuple[str, bool]:
    parts: list[str] = []
    for node in container.css("p"):
        text = re.sub(r"\s+", " ", node.text(strip=True)).strip()
        if not text:
            continue
        if text.startswith(("打开易车App", "责任编辑", "相关阅读")):
            continue
        parts.append(text)
    joined = "\n\n".join(parts)
    if len(joined) < 80:
        # 汽车产经镜像页正文大量位于嵌套 div，而不是 p。
        raw = container.text(separator="\n", strip=True)
        lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines()]
        parts = [
            line
            for line in lines
            if line and not line.startswith(("打开易车App", "责任编辑", "相关阅读"))
        ]
        joined = "\n\n".join(dict.fromkeys(parts))
    return joined[:_FULL_TEXT_MAX_CHARS], len(joined) > _FULL_TEXT_MAX_CHARS


def parse_detail(html: str) -> ArticleDetail:
    """从详情页提取完整正文与真实发布时间；解析失败返回空详情。"""
    tree = HTMLParser(html)
    published_at = _published_at(tree)

    if published_at is None:
        info = tree.css_first("div.article-information")
        if info is not None:
            match = re.search(
                r"\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?",
                info.text(strip=True),
            )
            if match:
                published_at = try_parse_cn_date(match.group(0))

    # 当前详情页正文位于 news-detail-main；其余选择器兼容历史模板。
    for sel in (
        "div.news-detail-main",
        "div.article-content",
        "div.article_content",
        "div.articleContent",
        "div.art-content",
        "article",
        "div.content",
    ):
        container = tree.css_first(sel)
        if container is None:
            continue
        text, truncated = _paragraphs(container)
        if text:
            return ArticleDetail(text=text, published_at=published_at, truncated=truncated)

    # WAF/旧模板下正文不可用时，以 meta description 作降级内容。
    meta = tree.css_first('meta[name="description"]')
    if meta is not None:
        content = re.sub(r"\s+", " ", (meta.attributes.get("content") or "").strip())
        if len(content) >= 20:
            return ArticleDetail(summary=content, published_at=published_at)
    return ArticleDetail(published_at=published_at)


def _mirror_url(url: str) -> str:
    return f"{_MIRROR_ORIGIN}{urlsplit(url).path}"


async def _fetch_detail(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, item: ListItem
) -> ArticleDetail:
    """抓取正文与发布时间；失败/为空回退列表摘要，绝不拖垮整源。"""
    async with sem:
        detail = ArticleDetail()
        try:
            html = await fetch_html(
                client,
                item.url,
                timeout=_DETAIL_TIMEOUT,
                referer=LIST_URL,
                allowed_hosts=_ARTICLE_HOSTS,
            )
            detail = parse_detail(html)
            if detail.text:
                return detail
            logger.debug("易车详情无正文，尝试汽车产经镜像: %s", item.url)
        except FetchError as exc:
            logger.debug("易车详情抓取失败，尝试汽车产经镜像: %s (%s)", item.url, exc)
        except Exception:  # 单条详情失败不得拖垮整源
            logger.warning("易车详情解析异常，尝试汽车产经镜像: %s", item.url, exc_info=True)

        try:
            mirror = _mirror_url(item.url)
            html = await fetch_html(
                client,
                mirror,
                timeout=_DETAIL_TIMEOUT,
                referer=_MIRROR_ORIGIN,
                allowed_hosts=_MIRROR_HOSTS,
            )
            mirrored = parse_detail(html)
            if mirrored.text:
                return ArticleDetail(
                    text=mirrored.text,
                    summary=mirrored.summary or detail.summary,
                    published_at=mirrored.published_at or detail.published_at,
                    truncated=mirrored.truncated,
                )
            detail = ArticleDetail(
                summary=detail.summary or mirrored.summary,
                published_at=detail.published_at or mirrored.published_at,
            )
        except FetchError as exc:
            logger.debug("易车镜像抓取失败，回退列表 desc: %s (%s)", item.url, exc)
        except Exception:
            logger.warning("易车镜像解析异常，回退列表 desc: %s", item.url, exc_info=True)
    return detail


def _build_post(uid: str, name: str, item: ListItem, detail: ArticleDetail) -> Post:
    full_text = detail.text.strip()
    fallback = item.desc.strip() or detail.summary.strip() or full_text[:200]
    text_plain = f"{item.title}\n\n{fallback}" if fallback else item.title
    return Post(
        uid=uid,
        screen_name=name,
        mid=f"yiche-{item.article_id}",
        url=item.url,
        created_at=detail.published_at or parse_cn_date(item.date_text),
        created_at_is_precise=detail.published_at is not None,
        title=item.title,
        text_plain=text_plain,
        full_text=full_text,
        image_urls=[item.image_url] if item.image_url else [],
    )


class YicheUserSource:
    key = "yiche-u61014816"
    name = "易车·易车原创"

    def __init__(self, name: str | None = None) -> None:
        if name:
            self.name = name

    async def fetch(self, client: httpx.AsyncClient) -> list[Post]:
        html = await fetch_html(
            client,
            LIST_URL,
            referer="https://i.yiche.com/",
            allowed_hosts=_LIST_HOSTS,
        )
        items = parse_list(html)
        if not items:
            raise FetchError(f"易车列表页解析不到任何条目: {LIST_URL}")

        sem = asyncio.Semaphore(_DETAIL_CONCURRENCY)
        details = await asyncio.gather(*(_fetch_detail(client, sem, item) for item in items))
        return [
            _build_post(self.key, self.name, item, detail)
            for item, detail in zip(items, details, strict=True)
        ]
