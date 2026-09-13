from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from src.sources.yiche import ListItem, _mirror_url, parse_detail, parse_list

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "yiche_all.html"


@pytest.fixture(scope="module")
def list_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_parse_list_returns_items(list_html: str) -> None:
    items = parse_list(list_html)
    assert len(items) > 0
    assert all(isinstance(it, ListItem) for it in items)


def test_parse_list_rejects_external_article_host() -> None:
    html = """
    <div class="article-item">
      <h2 class="title"><a href="https://evil.example/x/123.html">恶意外链文章</a></h2>
    </div>
    """
    assert parse_list(html) == []


def test_parse_list_fields_nonempty(list_html: str) -> None:
    items = parse_list(list_html)
    for it in items:
        assert it.title, "title 不应为空"
        assert it.url.startswith("https://"), f"url 应为完整 https 链接: {it.url}"
        assert it.article_id, "article_id 不应为空"
        # mid 由 article_id 派生，应稳定且不含标题
        assert it.title not in it.article_id


def test_mid_stable_across_parses(list_html: str) -> None:
    ids1 = [it.article_id for it in parse_list(list_html)]
    ids2 = [it.article_id for it in parse_list(list_html)]
    assert ids1 == ids2
    # article_id 唯一
    assert len(ids1) == len(set(ids1))


def test_created_at_is_datetime(list_html: str) -> None:
    from src.sources.base import parse_cn_date

    for it in parse_list(list_html):
        created = parse_cn_date(it.date_text)
        assert isinstance(created, dt.datetime)
        assert created.tzinfo is not None


def test_first_item_expected_values(list_html: str) -> None:
    items = parse_list(list_html)
    first = items[0]
    assert first.article_id == "01111564362"
    assert first.url == (
        "https://news.yiche.com/duochedaogou/20260718/01111564362.html"
    )
    assert first.image_url.startswith("https://")


def test_parse_detail_returns_article_detail() -> None:
    # 空 / 无正文页面应返回空详情而非抛异常
    assert parse_detail("<html><body></body></html>").text == ""
    html = '<html><head><meta name="description" content="' + "测" * 50 + '"></head></html>'
    detail = parse_detail(html)
    assert detail.text == ""
    assert detail.summary == "测" * 50


def test_parse_detail_full_text_and_exact_time() -> None:
    html = """
    <html><body>
      <script type="application/ld+json">
        {"@type":"NewsArticle","datePublished":"2026-07-17T20:28:27+08:00"}
      </script>
      <div class="news-detail-main">
        <p>第一段包含新车发布、配置和价格等重要信息。</p>
        <p>售价9.99万元。</p>
        <p>最后一段包含动力系统、续航和上市安排。</p>
        <p>责任编辑: 测试</p>
      </div>
    </body></html>
    """
    detail = parse_detail(html)
    assert all(text in detail.text for text in ("第一段", "售价9.99万元", "最后一段"))
    assert "责任编辑" not in detail.text
    assert detail.published_at is not None
    cst = dt.timezone(dt.timedelta(hours=8))
    assert detail.published_at.astimezone(cst).strftime("%Y-%m-%d %H:%M:%S") == (
        "2026-07-17 20:28:27"
    )


def test_parse_mirror_div_text_and_mirror_url() -> None:
    html = """
    <div class="article-information">2026-07-18 09:30</div>
    <div class="article-content">
      <div class="js-reply-content-line">镜像正文第一段，包含足够长的车型信息和价格信息。</div>
      <div class="js-reply-content-line">镜像正文第二段，包含足够长的动力信息和续航信息。</div>
    </div>
    """
    detail = parse_detail(html)
    assert "镜像正文第一段" in detail.text and "镜像正文第二段" in detail.text
    assert _mirror_url(
        "https://news.yiche.com/xinchexiaoxi/20260717/20111562275.html?x=1"
    ) == "https://www.autoreport.cn/xinchexiaoxi/20260717/20111562275.html"
