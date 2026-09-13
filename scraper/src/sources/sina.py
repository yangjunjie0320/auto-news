from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

from ..models import Post
from .base import FetchError, fetch_html, parse_cn_date

logger = logging.getLogger(__name__)

# 目标页：新浪汽车「新车上市」专题
# https://auto.sina.com.cn/newcar/index.d.html
# 完全服务端渲染的 CMS 专题页（UTF-8），含「新车日历」上市日期+车型+车型频道链接，
# 以及预售/上市快讯。无需翻页。
#
# 抓的是「新车日历」（div.coming-soon > ul.tmlist > li）：这块信息最完整，
# 每条都稳定带「上市日期 + 车型名 + 车型频道链接 + 封面图」。DOM 结构：
#   <li>
#     <p class="tit"><span class="date">2026-07-16</span><i class="label">刚上市</i></p>
#     <div class="fragment clearfix">
#       <a class="img fL clearfix" href="https://db.auto.sina.com.cn/7045/">
#         <img src="//auto.sinaimg.cn/...jpg" alt="五菱星光L"/>
#       </a>
#       <div class="intro fR">
#         <p class="name"><a href="http://db.auto.sina.com.cn/7045/">五菱星光L</a></p>
#         <p class="butt"><a href="...">车型频道</a></p>
#       </div>
#     </div>
#   </li>
# 车型频道页不是独立文章、没有可抓的正文摘要，因此只做列表解析、不进详情页，
# text_plain 只放标题。mid 用链接里的车型数字 id（sina-<id>），稳定不变。

INDEX_URL = "https://auto.sina.com.cn/newcar/index.d.html"

# 从 db.auto.sina.com.cn/<id>/ 里解析车型数字 id
_ID_RE = re.compile(r"db\.auto\.sina\.com\.cn/(\d+)")


@dataclass(frozen=True)
class SinaItem:
    """新车日历里的一条：车型名 + 车型频道链接 + 上市日期（+ 状态标签/封面图）。"""

    mid: str
    url: str
    title: str
    date_text: str
    label: str = ""
    image_urls: list[str] = field(default_factory=list)


def _abs_url(href: str) -> str:
    """把 //host/path 或 http://host/path 补全成 https 完整链接。"""
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http://"):
        return "https://" + href[len("http://") :]
    return href


def parse_list(html: str) -> list[SinaItem]:
    """纯解析：从专题页 HTML 抽出「新车日历」条目。不发网络请求。

    跳过缺少车型 id、链接或标题的残缺条目；返回顺序即页面顺序。
    """
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    items: list[SinaItem] = []
    seen: set[str] = set()

    for li in tree.css("ul.tmlist li"):
        name_a = li.css_first("p.name a")
        if name_a is None:
            continue
        title = (name_a.text() or "").strip()
        href = name_a.attributes.get("href") or ""
        m = _ID_RE.search(href)
        if not title or m is None:
            continue
        car_id = m.group(1)
        mid = f"sina-{car_id}"
        if mid in seen:
            continue
        seen.add(mid)

        date_node = li.css_first("span.date")
        date_text = (date_node.text() or "").strip() if date_node else ""

        label_node = li.css_first("i.label")
        label = (label_node.text() or "").strip() if label_node else ""

        image_urls: list[str] = []
        img = li.css_first("a.img img")
        if img is not None:
            src = img.attributes.get("src") or img.attributes.get("data-src") or ""
            src = _abs_url(src)
            if src:
                image_urls.append(src)

        items.append(
            SinaItem(
                mid=mid,
                url=_abs_url(href),
                title=title,
                date_text=date_text,
                label=label,
                image_urls=image_urls,
            )
        )

    return items


def _to_post(item: SinaItem, name: str, key: str) -> Post:
    return Post(
        uid=key,
        screen_name=name,
        mid=item.mid,
        url=item.url,
        created_at=parse_cn_date(item.date_text),
        title=item.title,
        # 车型频道页无独立正文摘要，text_plain 只放标题
        text_plain=item.title,
        image_urls=list(item.image_urls),
    )


class SinaNewcarSource:
    key = "sina-newcar"
    name = "新浪汽车·新车上市"

    def __init__(self, name: str | None = None) -> None:
        if name:
            self.name = name

    async def fetch(self, client: httpx.AsyncClient) -> list[Post]:
        html = await fetch_html(
            client,
            INDEX_URL,
            referer="https://auto.sina.com.cn/",
            allowed_hosts={"auto.sina.com.cn"},
        )
        items = parse_list(html)
        if not items:
            raise FetchError(f"sina newcar: no items parsed from {INDEX_URL}")
        posts = [_to_post(it, self.name, self.key) for it in items]
        logger.info("sina newcar parsed %d posts", len(posts))
        return posts
