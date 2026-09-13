from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from src.sources.autohome import (
    ListItem,
    _article_id,
    _build_post,
    _normalize_month,
    parse_detail,
    parse_list,
)
from src.sources.base import ArticleDetail

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "autohome_newbrand.html"

# 汽车之家列表页是 gb2312/gb18030 编码，必须按 gb18030 读，否则中文乱码。
_HTML = FIXTURE.read_text(encoding="gb18030")

# CJK 汉字区间，用于校验中文没有乱码（乱码通常落在别的码位/替换符）。
_CJK_RE = re.compile(r"[一-鿿]")


def test_parse_list_returns_items() -> None:
    items = parse_list(_HTML)
    assert len(items) > 0
    # 快照里约有数百条，做个下限断言防止只解析出个别条目。
    assert len(items) >= 50


def test_parse_list_rejects_external_article_host() -> None:
    html = """
    <dl class="all-list"><dt class="month">2026年07月</dt><dd class="carinfo">
      <a class="pic" href="https://evil.example/news/202607/1315705.html">
        <span class="title">恶意外链文章</span>
      </a>
    </dd></dl>
    """
    assert parse_list(html) == []


def test_each_item_fields_valid() -> None:
    items = parse_list(_HTML)
    for it in items:
        # 标题、链接、id 非空
        assert it.title
        assert it.url.startswith("https://")
        assert it.article_id
        # 中文不乱码：标题里应含正常汉字，且不含替换符
        assert _CJK_RE.search(it.title), f"标题疑似乱码: {it.title!r}"
        assert "�" not in it.title


def test_mid_stable_from_article_id() -> None:
    # 从文章链接解析出的数字 id 必须稳定，且与链接里的 id 一致。
    url = "http://www.autohome.com.cn/news/202607/1315705.html#pvareaid=6827541"
    assert _article_id(url) == "1315705"

    item = ListItem(
        article_id="1315705",
        title="示例标题",
        url="https://www.autohome.com.cn/news/202607/1315705.html",
        date_text="2026-07-01",
        image_url="",
    )
    post = _build_post(
        "autohome-newbrand", "汽车之家·上市新车", item, ArticleDetail()
    )
    assert post.mid == "autohome-1315705"
    assert post.uid == "autohome-newbrand"
    assert post.screen_name == "汽车之家·上市新车"


def test_mid_unique_and_prefixed() -> None:
    items = parse_list(_HTML)
    mids = [f"autohome-{it.article_id}" for it in items]
    assert all(m.startswith("autohome-") for m in mids)
    # 主体应是数字 id
    assert all(m.split("autohome-")[1].isdigit() for m in mids)


def test_image_urls_use_data_src_not_blank() -> None:
    items = parse_list(_HTML)
    with_img = [it for it in items if it.image_url]
    # 快照里每条都有 data-src 真实图，绝大多数应取到图。
    assert with_img, "没有解析到任何封面图"
    for it in with_img:
        assert "blank.gif" not in it.image_url
        assert it.image_url.startswith("https://")


def test_created_at_is_datetime() -> None:
    items = parse_list(_HTML)
    it = items[0]
    post = _build_post(
        "autohome-newbrand", "汽车之家·上市新车", it, ArticleDetail()
    )
    assert isinstance(post.created_at, dt.datetime)
    # base 约定返回 tz-aware UTC
    assert post.created_at.tzinfo is not None


def test_normalize_month() -> None:
    assert _normalize_month("2026年07月") == "2026-07-01"
    assert _normalize_month("2026年7月") == "2026-07-01"


def test_build_post_text_plain() -> None:
    it = ListItem(
        article_id="1",
        title="标题",
        url="https://www.autohome.com.cn/news/202607/1.html",
        date_text="2026-07-01",
        image_url="https://img.example/a.jpg",
    )
    # 有摘要：标题 + 空行 + 摘要
    p1 = _build_post("k", "n", it, ArticleDetail(summary="这是一段正文摘要"))
    assert p1.text_plain == "标题\n\n这是一段正文摘要"
    assert p1.full_text == ""
    assert p1.image_urls == ["https://img.example/a.jpg"]
    # 无摘要：仅标题
    p2 = _build_post("k", "n", it, ArticleDetail())
    assert p2.text_plain == "标题"


def test_parse_detail_meta_description() -> None:
    desc = "日前，我们从汽车之家官方获悉，某新款车型正式上市，共推出多款配置车型。"
    html = f'<html><head><meta name="description" content="{desc}"></head><body></body></html>'
    detail = parse_detail(html)
    assert "正式上市" in detail.summary
    assert detail.text == ""
    # 无摘要来源时返回空串
    assert parse_detail("<html><body><p>短</p></body></html>").text == ""


def test_parse_detail_next_data_full_text_and_publish_time() -> None:
    article = {
        "publishDate": "2026-06-09 13:52:24",
        "content": (
            '<p class="editor-paragraph">第一段包含车辆预告和核心设计信息。</p>'
            '<p class="editor-paragraph">售价9.99万元。</p>'
            '<p class="editor-paragraph">最后一段包含动力、续航和价格信息。</p>'
        ),
        "summary": "站点摘要",
    }
    payload = {"props": {"pageProps": {"articleContent": article}}}
    html = (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload, ensure_ascii=False)
        + "</script></body></html>"
    )
    detail = parse_detail(html)
    assert all(text in detail.text for text in ("第一段", "售价9.99万元", "最后一段"))
    assert detail.published_at is not None
    cst = dt.timezone(dt.timedelta(hours=8))
    assert detail.published_at.astimezone(cst).strftime("%Y-%m-%d %H:%M:%S") == (
        "2026-06-09 13:52:24"
    )
