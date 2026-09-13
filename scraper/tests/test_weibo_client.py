from __future__ import annotations

import datetime as dt

import httpx
import pytest

from src.config import Settings
from src.weibo_client import (
    RateLimitedError,
    WeiboClient,
    WeiboClientError,
    extract_mblogs,
    parse_followers,
    parse_hit,
    search_url,
)

_VISITOR_BODY = 'visitor_gray_callback({"retcode":20000000,"data":{"sub":"S","subp":"P"}});'


def _mblog(**overrides: object) -> dict:
    base = {
        "mid": "5327093825274209",
        "created_at": "Fri Aug 01 13:20:00 +0800 2026",
        "text": "一汽-大众<br/>正式官宣<a href='#'>全系双终身质保</a>",
        "reposts_count": 12,
        "comments_count": 30,
        "attitudes_count": 4426,
        "user": {"id": 1234, "screen_name": "白逸腾ten", "followers_count": "157.6万"},
    }
    base.update(overrides)
    return base


def _handler(search_response):
    def handler(request: httpx.Request) -> httpx.Response:
        if "genvisitor2" in str(request.url):
            return httpx.Response(200, text=_VISITOR_BODY)
        if request.url.path == "/":
            return httpx.Response(200, text="")
        return search_response(request)

    return handler


def test_parse_followers_handles_search_api_formatted_counts() -> None:
    # 搜索接口返回 "157.6万" 这类字符串，时间线接口返回整数，两种都要吃得下
    assert parse_followers("157.6万") == 1_576_000
    assert parse_followers("1.2亿") == 120_000_000
    assert parse_followers(4321) == 4321
    assert parse_followers("") == 0
    assert parse_followers("未知") == 0


def test_parse_hit_extracts_text_time_and_engagement() -> None:
    hit = parse_hit({}, _mblog())
    assert hit is not None
    assert hit.screen_name == "白逸腾ten"
    assert hit.followers == 1_576_000
    assert hit.engagement == 12 + 30 + 4426
    assert hit.text == "一汽-大众\n正式官宣全系双终身质保"
    assert hit.created_at == dt.datetime(
        2026, 8, 1, 13, 20, tzinfo=dt.timezone(dt.timedelta(hours=8))
    )
    assert hit.url == "https://m.weibo.cn/detail/5327093825274209"
    assert not hit.is_pinned
    assert not hit.text_truncated


def test_hit_url_prefers_desktop_link_when_bid_present() -> None:
    hit = parse_hit({}, _mblog(bid="PzX9qA1bc"))
    assert hit is not None
    assert hit.url == "https://weibo.com/1234/PzX9qA1bc"


def test_parse_hit_detects_pinned_and_truncated_posts() -> None:
    pinned = parse_hit({"profile_type_id": "proweibotop_"}, _mblog())
    assert pinned is not None and pinned.is_pinned
    pinned_title = parse_hit({}, _mblog(title={"text": "置顶"}))
    assert pinned_title is not None and pinned_title.is_pinned
    truncated = parse_hit({}, _mblog(isLongText=True))
    assert truncated is not None and truncated.text_truncated
    embedded = parse_hit({}, _mblog(isLongText=True, longText={"longTextContent": "全文"}))
    assert embedded is not None and not embedded.text_truncated and embedded.text == "全文"


def test_parse_hit_extracts_pics_preferring_large() -> None:
    hit = parse_hit(
        {},
        _mblog(
            pics=[
                {"url": "https://p.sinaimg.cn/thumb/a.jpg", "large": {"url": "https://p.sinaimg.cn/large/a.jpg"}},
                {"url": "https://p.sinaimg.cn/thumb/b.jpg", "large": "not a dict"},
                {"url": ""},
                "not a dict",
            ]
        ),
    )
    assert hit is not None
    assert hit.image_urls == (
        "https://p.sinaimg.cn/large/a.jpg",
        "https://p.sinaimg.cn/thumb/b.jpg",
    )


def test_parse_hit_caps_pics_and_tolerates_missing() -> None:
    many = [{"url": f"https://p.sinaimg.cn/{i}.jpg"} for i in range(12)]
    hit = parse_hit({}, _mblog(pics=many))
    assert hit is not None and len(hit.image_urls) == 9
    no_pics = parse_hit({}, _mblog())
    assert no_pics is not None and no_pics.image_urls == ()
    bad_pics = parse_hit({}, _mblog(pics="oops"))
    assert bad_pics is not None and bad_pics.image_urls == ()


@pytest.mark.parametrize("missing", ["mid", "created_at"])
def test_parse_hit_drops_entries_without_identity_or_time(missing: str) -> None:
    mblog = _mblog()
    mblog[missing] = ""
    assert parse_hit({}, mblog) is None


def test_extract_mblogs_reads_statuses_flat_and_grouped_cards() -> None:
    grouped = {
        "data": {
            "cards": [
                {"card_type": 9, "mblog": _mblog(mid="1")},
                {
                    "card_type": 11,
                    "card_group": [
                        {"card_type": 9, "mblog": _mblog(mid="2")},
                        {"card_type": 4},
                    ],
                },
                {"card_type": 11, "card_group": None},
            ]
        }
    }
    assert [m["mid"] for _, m in extract_mblogs(grouped)] == ["1", "2"]
    statuses = {"statuses": [_mblog(mid="3")]}
    assert [m["mid"] for _, m in extract_mblogs(statuses)] == ["3"]


def test_search_url_percent_encodes_the_container_query() -> None:
    url = search_url("腾势Z9S")
    assert "containerid=100103type%3D1%26q%3D%E8%85%BE%E5%8A%BFZ9S" in url


async def test_search_returns_parsed_hits() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": 1, "data": {"cards": [{"card_type": 9, "mblog": _mblog()}]}},
        )

    transport = httpx.MockTransport(_handler(respond))
    async with httpx.AsyncClient(transport=transport) as client:
        hits = await WeiboClient(Settings(), client).search("一汽大众 终身质保")
    assert len(hits) == 1
    assert hits[0].screen_name == "白逸腾ten"


async def test_timeline_uses_uid_container_and_parses_hits() -> None:
    seen_urls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={"ok": 1, "data": {"cards": [{"card_type": 9, "mblog": _mblog()}]}},
        )

    transport = httpx.MockTransport(_handler(respond))
    async with httpx.AsyncClient(transport=transport) as client:
        hits = await WeiboClient(Settings(), client).timeline("1644027280")
    assert len(hits) == 1
    assert any("containerid=1076031644027280" in url for url in seen_urls)


async def test_fetch_full_text_replaces_truncated_body() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert "statuses/extend" in str(request.url)
        return httpx.Response(200, json={"ok": 1, "data": {"longTextContent": "完整<br/>正文"}})

    transport = httpx.MockTransport(_handler(respond))
    async with httpx.AsyncClient(transport=transport) as client:
        weibo = WeiboClient(Settings(), client)
        await weibo.ensure_cookie()
        hit = parse_hit(
            {}, _mblog(isLongText=True, pics=[{"url": "https://p.sinaimg.cn/a.jpg"}])
        )
        assert hit is not None and hit.text_truncated
        full = await weibo.fetch_full_text(hit)
    assert full.text == "完整\n正文"
    assert not full.text_truncated
    assert full.image_urls == ("https://p.sinaimg.cn/a.jpg",)  # 补全长文不丢图


async def test_search_raises_rate_limited_on_visitor_challenge() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Sina Visitor System</html>")

    transport = httpx.MockTransport(_handler(respond))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RateLimitedError):
            await WeiboClient(Settings(), client).search("零跑 交付")


async def test_request_does_not_retry_past_the_configured_attempts() -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    transport = httpx.MockTransport(_handler(respond))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(WeiboClientError):
            await WeiboClient(Settings(request_retries=2), client).search("零跑 交付")
    assert calls == 3  # 首次 + 2 次重试
