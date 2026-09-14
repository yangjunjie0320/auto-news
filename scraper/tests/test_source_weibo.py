"""微博源：账号池与关键词搜索，走的是和新闻站一样的 SourceFetcher 协议。"""

import datetime as dt

import httpx
import pytest

from src.config import Settings
from src.sources.weibo import (
    PoolAccount,
    WeiboPoolSource,
    WeiboSearchSource,
    hit_to_post,
    load_pool,
)
from src.weibo_client import WeiboClientError, WeiboHit

CST = dt.timezone(dt.timedelta(hours=8))


def make_hit(**overrides) -> WeiboHit:
    base = {
        "mid": "5001",
        "uid": "2133500025",
        "screen_name": "某博主",
        "followers": 200_000,
        "verified": True,
        "created_at": dt.datetime(2026, 8, 1, 10, 0, tzinfo=CST),
        "text": "试驾了某新车，底盘比预期扎实",
        "engagement": 50,
        "is_repost": False,
    }
    base.update(overrides)
    return WeiboHit(**base)


def test_load_pool_missing_file_returns_empty(tmp_path):
    assert load_pool(tmp_path / "nope.yaml") == []


def test_load_pool_reads_accounts(tmp_path):
    p = tmp_path / "pool.yaml"
    p.write_text('accounts:\n  - {name: "甲", uid: "1"}\n', encoding="utf-8")
    assert load_pool(p) == [PoolAccount(name="甲", uid="1")]


def test_hit_to_post_marks_kind_weibo():
    post = hit_to_post(make_hit(), uid="weibo-pool", screen_name="微博·某博主")
    # kind 要一路透到归档，网站靠它区分新闻与观点
    assert post.kind == "weibo"
    assert post.uid == "weibo-pool"
    assert post.screen_name == "微博·某博主"
    # 微博没有标题，正文同时进 text_plain 和 full_text 供分类器重写 headline
    assert post.title == ""
    assert post.text_plain == post.full_text


async def test_pool_source_collects_all_accounts(tmp_path, monkeypatch):
    pool = tmp_path / "pool.yaml"
    pool.write_text(
        'accounts:\n  - {name: "甲", uid: "1"}\n  - {name: "乙", uid: "2"}\n', encoding="utf-8"
    )

    async def fake_timeline(self, uid, page=1):
        return [make_hit(mid=f"m{uid}")]

    monkeypatch.setattr("src.sources.weibo.WeiboClient.timeline", fake_timeline)
    monkeypatch.setattr("src.sources.weibo._sleep_between", _noop)

    settings = Settings(weibo_pool_file=str(pool))
    async with httpx.AsyncClient() as client:
        posts = await WeiboPoolSource(settings=settings).fetch(client)

    assert [p.mid for p in posts] == ["m1", "m2"]
    assert all(p.kind == "weibo" for p in posts)


async def test_pool_source_tolerates_partial_failure(tmp_path, monkeypatch):
    """34 个账号里挂一两个是常态，不该让整个源失败。"""
    pool = tmp_path / "pool.yaml"
    pool.write_text(
        'accounts:\n  - {name: "甲", uid: "1"}\n  - {name: "乙", uid: "2"}\n', encoding="utf-8"
    )

    async def flaky(self, uid, page=1):
        if uid == "1":
            raise WeiboClientError("boom")
        return [make_hit(mid="m2")]

    monkeypatch.setattr("src.sources.weibo.WeiboClient.timeline", flaky)
    monkeypatch.setattr("src.sources.weibo._sleep_between", _noop)

    settings = Settings(weibo_pool_file=str(pool))
    async with httpx.AsyncClient() as client:
        posts = await WeiboPoolSource(settings=settings).fetch(client)

    assert [p.mid for p in posts] == ["m2"]


async def test_pool_source_fails_when_all_accounts_fail(tmp_path, monkeypatch):
    pool = tmp_path / "pool.yaml"
    pool.write_text('accounts:\n  - {name: "甲", uid: "1"}\n', encoding="utf-8")

    async def always_fail(self, uid, page=1):
        raise WeiboClientError("boom")

    monkeypatch.setattr("src.sources.weibo.WeiboClient.timeline", always_fail)
    monkeypatch.setattr("src.sources.weibo._sleep_between", _noop)

    settings = Settings(weibo_pool_file=str(pool))
    async with httpx.AsyncClient() as client:
        with pytest.raises(Exception, match="全部抓取失败"):
            await WeiboPoolSource(settings=settings).fetch(client)


async def test_search_source_filters_low_signal(monkeypatch):
    """低互动且低粉丝的基本是营销号；两个条件是「且」，刚官宣的事件互动为 0。"""

    async def fake_search(self, query):
        return [
            make_hit(mid="keep-hi-engagement", engagement=99, followers=10),
            make_hit(mid="keep-hi-followers", engagement=0, followers=500_000),
            make_hit(mid="drop", engagement=0, followers=10),
        ]

    monkeypatch.setattr("src.sources.weibo.WeiboClient.search", fake_search)
    monkeypatch.setattr("src.sources.weibo._sleep_between", _noop)

    settings = Settings(weibo_search_queries=["腾势Z9S"])
    async with httpx.AsyncClient() as client:
        posts = await WeiboSearchSource(settings=settings).fetch(client)

    assert [p.mid for p in posts] == ["keep-hi-engagement", "keep-hi-followers"]


async def test_search_source_without_queries_returns_empty():
    settings = Settings(weibo_search_queries=[])
    async with httpx.AsyncClient() as client:
        assert await WeiboSearchSource(settings=settings).fetch(client) == []


async def _noop(settings):
    return None
