from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

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

# 目标页：汽车之家「上市新车」
# https://www.autohome.com.cn/newbrand/
# 服务端渲染，列表是规整的 dd.carinfo 块：a.pic 里含文章链接、懒加载封面图、
# span.title 标题；上市日期在每组 dl.all-list 的 dt.month 表头里（月粒度）。
#
# 坑记录：
#   1) 列表页编码是 gb2312/gb18030，必须用 encoding="gb18030" 解码，否则中文乱码。
#      （注意：news 详情页现已是 UTF-8，故详情用 encoding=None 让 httpx 自动识别。）
#   2) 封面图懒加载，真实地址在 <img ... data-src=...>，src 是 blank.gif 占位，
#      取图必须读 data-src。
#   3) mid 用文章链接里 /news/<yyyymm>/<id>.html 的数字 id，稳定不随标题变化。

LIST_URL = "https://www.autohome.com.cn/newbrand/"
_ARTICLE_HOSTS = frozenset({"www.autohome.com.cn"})

# 详情抓取并发上限与超时；控制并发避免拖慢整轮。
_DETAIL_CONCURRENCY = 5
_DETAIL_TIMEOUT = 10.0
_FULL_TEXT_MAX_CHARS = 12_000

# 列表页按月倒序排列，往往有 300+ 历史条目。我们只关心「最新出现的新车」，
# 新条目总在最前，故只取最新 N 条进入管线：既把每轮详情请求控制在 N 次，
# 又保证 scraper 每轮稳定只返回同一批顶部条目（旧条目永不进管线、不会二次刷屏）。
_MAX_LIST_ITEMS = 50

# 文章链接里的数字 id：/news/<yyyymm>/<id>.html
_ARTICLE_ID_RE = re.compile(r"/news/\d+/(\d+)\.html", re.IGNORECASE)
# dt.month 形如 "2026年07月"，提取年、月。
_MONTH_RE = re.compile(r"(\d{4})\D+(\d{1,2})")
# 懒加载占位图，取图时跳过。
_BLANK_IMG = "blank.gif"


@dataclass(frozen=True)
class ListItem:
    """列表页解析出的一条中间结构（纯解析产物，供 parse_list 返回）。"""

    article_id: str
    title: str
    url: str
    date_text: str  # 归一化后的上市日期文本，形如 "2026-07-01"
    image_url: str


def _abs_url(href: str) -> str:
    """补全为 https 绝对地址。"""
    href = (href or "").strip()
    if not href:
        return ""
    # 去掉 #pvareaid=... 之类锚点，避免污染 url / id。
    href = href.split("#", 1)[0].strip()
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http://"):
        return "https://" + href[len("http://") :]
    if href.startswith("https://"):
        return href
    if href.startswith("/"):
        return "https://www.autohome.com.cn" + href
    return href


def _article_id(url: str) -> str:
    """从文章链接解析源内稳定唯一数字 id（/news/<yyyymm>/<id>.html 取 <id>）。"""
    m = _ARTICLE_ID_RE.search(url)
    return m.group(1) if m else ""


def _normalize_month(text: str) -> str:
    """dt.month 文本（如 '2026年07月'）归一化成 parse_cn_date 可识别的 'YYYY-MM-01'。

    月粒度是列表页能提供的最细日期；解析不出时原样返回（交给 parse_cn_date 兜底）。
    """
    m = _MONTH_RE.search(text or "")
    if not m:
        return (text or "").strip()
    year, month = int(m.group(1)), int(m.group(2))
    return f"{year:04d}-{month:02d}-01"


def parse_list(html: str) -> list[ListItem]:
    """纯函数：解析「上市新车」列表页 HTML，返回 ListItem 列表（不发网络请求）。"""
    tree = HTMLParser(html)
    items: list[ListItem] = []
    for dl in tree.css("dl.all-list"):
        dt = dl.css_first("dt.month")
        date_text = _normalize_month(dt.text(strip=True)) if dt is not None else ""
        for dd in dl.css("dd.carinfo"):
            link = dd.css_first("a.pic")
            if link is None:
                continue
            url = _abs_url(link.attributes.get("href") or "")
            article_id = _article_id(url)
            if not url or not article_id or not is_allowed_https_url(url, _ARTICLE_HOSTS):
                continue

            title_node = dd.css_first("span.title")
            title = title_node.text(strip=True) if title_node is not None else ""
            if not title:
                continue

            image_url = ""
            img = dd.css_first("img")
            if img is not None:
                raw = (img.attributes.get("data-src") or "").strip()
                # 只取真实懒加载图，跳过 blank.gif 占位。
                if raw and _BLANK_IMG not in raw:
                    image_url = _abs_url(raw)

            items.append(
                ListItem(
                    article_id=article_id,
                    title=title,
                    url=url,
                    date_text=date_text,
                    image_url=image_url,
                )
            )
    return items


def _paragraphs(container: object) -> tuple[str, bool]:
    parts: list[str] = []
    for node in container.css("p"):
        text = re.sub(r"\s+", " ", node.text(strip=True)).strip()
        if text:
            parts.append(text)
    joined = "\n\n".join(parts)
    return joined[:_FULL_TEXT_MAX_CHARS], len(joined) > _FULL_TEXT_MAX_CHARS


def _structured_article(tree: HTMLParser) -> dict:
    """读取 Next.js 注入的文章对象；页面改版或 JSON 异常时安全降级。"""
    for script in tree.css("script"):
        if script.attributes.get("type") != "application/json":
            continue
        raw = script.text()
        if "articleContent" not in raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        props = data.get("props")
        if not isinstance(props, dict):
            continue
        page_props = props.get("pageProps")
        if not isinstance(page_props, dict):
            continue
        article = page_props.get("articleContent")
        if isinstance(article, dict):
            return article
    return {}


def parse_detail(html: str) -> ArticleDetail:
    """从详情页提取完整正文与真实发布时间；解析失败返回空详情。"""
    tree = HTMLParser(html)
    article = _structured_article(tree)
    published_at = None
    raw_date = article.get("publishDate")
    if isinstance(raw_date, str) and raw_date.strip():
        published_at = try_parse_cn_date(raw_date)

    raw_content = article.get("content")
    if isinstance(raw_content, str) and raw_content.strip():
        text, truncated = _paragraphs(HTMLParser(raw_content))
        if text:
            return ArticleDetail(text=text, published_at=published_at, truncated=truncated)

    # 当前渲染页正文位于 parent-container；其余选择器兼容历史模板。
    for sel in (
        "#parent-container",
        "div.article-content",
        "#articleContent",
        "div.article",
        "article",
    ):
        container = tree.css_first(sel)
        if container is None:
            continue
        text, truncated = _paragraphs(container)
        if text:
            return ArticleDetail(text=text, published_at=published_at, truncated=truncated)

    # 详情正文缺失时使用站点摘要降级，但仍保留精确发布时间。
    summary = article.get("summary")
    if isinstance(summary, str) and summary.strip():
        return ArticleDetail(
            summary=re.sub(r"\s+", " ", summary).strip(),
            published_at=published_at,
        )
    for sel in ('meta[name="description"]', 'meta[property="og:description"]'):
        meta = tree.css_first(sel)
        if meta is None:
            continue
        content = re.sub(r"\s+", " ", (meta.attributes.get("content") or "")).strip()
        if len(content) >= 20:
            return ArticleDetail(summary=content, published_at=published_at)
    return ArticleDetail(published_at=published_at)


async def _fetch_detail(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, item: ListItem
) -> ArticleDetail:
    """抓取单条完整正文与发布时间；失败退化成仅标题，绝不拖垮整源。"""
    async with sem:
        detail = ArticleDetail()
        try:
            # 详情页现为 UTF-8，encoding=None 让 httpx 依据 charset 自动解码。
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
            logger.debug("汽车之家详情无正文: %s", item.url)
        except FetchError as exc:
            logger.debug("汽车之家详情抓取失败: %s (%s)", item.url, exc)
        except Exception:  # 单条详情失败不得拖垮整源
            logger.warning("汽车之家详情解析异常: %s", item.url, exc_info=True)
    return detail


def _build_post(uid: str, name: str, item: ListItem, detail: ArticleDetail) -> Post:
    full_text = detail.text.strip()
    fallback = detail.summary.strip() or full_text[:200]
    text_plain = f"{item.title}\n\n{fallback}" if fallback else item.title
    return Post(
        uid=uid,
        screen_name=name,
        mid=f"autohome-{item.article_id}",
        url=item.url,
        created_at=detail.published_at or parse_cn_date(item.date_text),
        created_at_is_precise=detail.published_at is not None,
        title=item.title,
        text_plain=text_plain,
        full_text=full_text,
        image_urls=[item.image_url] if item.image_url else [],
    )


class AutohomeNewbrandSource:
    key = "autohome-newbrand"
    name = "汽车之家·上市新车"

    def __init__(self, name: str | None = None) -> None:
        if name:
            self.name = name

    async def fetch(self, client: httpx.AsyncClient) -> list[Post]:
        # 列表页是 gb2312/gb18030，必须显式指定编码解码。
        html = await fetch_html(
            client,
            LIST_URL,
            encoding="gb18030",
            referer="https://www.autohome.com.cn/",
            allowed_hosts=_ARTICLE_HOSTS,
        )
        items = parse_list(html)
        if not items:
            raise FetchError(f"汽车之家列表页解析不到任何条目: {LIST_URL}")
        # 只取最新 N 条（列表按月倒序，新车在最前）
        items = items[:_MAX_LIST_ITEMS]

        sem = asyncio.Semaphore(_DETAIL_CONCURRENCY)
        details = await asyncio.gather(*(_fetch_detail(client, sem, item) for item in items))
        return [
            _build_post(self.key, self.name, item, detail)
            for item, detail in zip(items, details, strict=True)
        ]
