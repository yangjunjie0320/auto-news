from __future__ import annotations

import datetime as dt
from pathlib import Path

from src.sources.base import ArticleDetail
from src.sources.sina_news import SinaNewsItem, _build_post, parse_detail, parse_list

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "sina_newcar.html"


def test_parse_news_list_uses_left_stream_only() -> None:
    items = parse_list(FIXTURE.read_text(encoding="utf-8"))
    assert items
    assert len({item.mid for item in items}) == len(items)
    first = items[0]
    assert first.mid == "sina-news-iniiccuy4162542"
    assert first.title == "24.99/27.99万元 阿维塔07 L开启预售"
    assert first.date_text == "2026-07-18 01:53:02"
    assert first.url.startswith("https://auto.sina.com.cn/")
    assert "阿维塔07 L正式公布" in first.intro
    assert first.image_url.startswith("https://")


def test_parse_news_list_rejects_external_article_host() -> None:
    html = """
    <div class="con"><div class="single"><div class="s-left">
      <h3><a href="https://evil.example/detail-iniiccuy4162542.shtml">恶意外链文章</a></h3>
    </div></div></div>
    """
    assert parse_list(html) == []


def test_parse_news_list_accepts_relative_article_url() -> None:
    html = """
    <div class="con"><div class="single"><div class="s-left">
      <h3><a href="/news/detail-iniiccuy4162542.shtml">相对链接文章</a></h3>
    </div></div></div>
    """
    items = parse_list(html)
    assert items[0].url == (
        "https://auto.sina.com.cn/news/detail-iniiccuy4162542.shtml"
    )


def test_parse_news_detail_full_text_and_time() -> None:
    html = """
    <html><head>
      <meta property="article:published_time" content="2026-07-18 01:53:02">
    </head><body>
      <div id="artibody">
        <p>第一段包含车型预售价与上市日期等完整信息。</p>
        <p>售价9.99万元。</p>
        <p>最后一段包含动力系统与续航信息。</p>
        <p>责任编辑：测试</p>
      </div>
    </body></html>
    """
    detail = parse_detail(html)
    assert all(text in detail.text for text in ("第一段", "售价9.99万元", "最后一段"))
    assert "责任编辑" not in detail.text
    assert detail.published_at is not None
    cst = dt.timezone(dt.timedelta(hours=8))
    assert detail.published_at.astimezone(cst).strftime("%Y-%m-%d %H:%M:%S") == (
        "2026-07-18 01:53:02"
    )


def test_visible_sina_time_wins_when_meta_conflicts() -> None:
    html = """
    <html><head>
      <meta property="article:published_time" content="2026-07-17T16:50:39+08:00">
    </head><body>
      <span class="date">2026-07-18 01:53:02</span>
      <div id="artibody"><p>正文内容。</p></div>
    </body></html>
    """
    detail = parse_detail(html)
    assert detail.published_at is not None
    cst = dt.timezone(dt.timedelta(hours=8))
    assert detail.published_at.astimezone(cst).strftime("%Y-%m-%d %H:%M:%S") == (
        "2026-07-18 01:53:02"
    )


def test_invalid_list_time_is_not_marked_precise() -> None:
    item = SinaNewsItem(
        mid="sina-news-test",
        url="https://auto.sina.com.cn/news/detail-test.shtml",
        title="测试文章",
        date_text="invalid",
    )
    post = _build_post("sina-newcar-news", "新浪汽车", item, ArticleDetail())
    assert post.created_at_is_precise is False
