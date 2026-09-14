"""微博数据源：人工筛选的账号池，以及固定关键词搜索。

与三个新闻站的区别在性质：新闻站提供的是官方事实（发布、销量、政策），
微博提供的是增量观点（实车体验、渠道见闻、质疑、分析）。两者都落进同一份归档，
靠 `kind` 字段区分，网站和 RSS 可以按来源筛选。

抓取节奏也不同：微博用访客 cookie，抓太勤会被限流，所以 sources.yaml 里
给它单独配了 `interval_seconds: 86400`（每天一次），由 monitor 按源判断是否到点。
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel

from ..config import Settings
from ..models import Post
from ..weibo_client import WeiboClient, WeiboClientError, WeiboHit
from .base import FetchError

logger = logging.getLogger(__name__)


class PoolAccount(BaseModel):
    name: str
    uid: str


def load_pool(path: str | Path) -> list[PoolAccount]:
    pool_path = Path(path)
    if not pool_path.exists():
        logger.warning("pool file not found: %s", pool_path)
        return []
    data = yaml.safe_load(pool_path.read_text(encoding="utf-8")) or {}
    return [PoolAccount(**item) for item in data.get("accounts", [])]


def hit_to_post(hit: WeiboHit, *, uid: str, screen_name: str) -> Post:
    """转成统一数据契约，后面的分类、翻译、归档与新闻站条目走同一条路。

    微博没有「标题」，title 留空，分类器会据正文重写一个 headline。
    """
    return Post(
        uid=uid,
        screen_name=screen_name,
        mid=hit.mid,
        url=hit.url,
        created_at=hit.created_at,
        text_plain=hit.text,
        full_text=hit.text,
        image_urls=list(hit.image_urls),
        kind="weibo",
    )


async def _sleep_between(settings: Settings) -> None:
    """账号之间随机延迟。访客 cookie 没有配额，连续快打很容易被限流。"""
    await asyncio.sleep(
        random.uniform(settings.weibo_delay_min_seconds, settings.weibo_delay_max_seconds)
    )


class WeiboPoolSource:
    """人工筛选的博主账号池，每个账号抓时间线第一页。"""

    key = "weibo-pool"
    name = "微博·账号池"

    def __init__(self, name: str | None = None, *, settings: Settings) -> None:
        if name:
            self.name = name
        self._settings = settings

    async def fetch(self, client: httpx.AsyncClient) -> list[Post]:
        accounts = load_pool(self._settings.weibo_pool_file)
        if not accounts:
            raise FetchError(f"账号池为空或不存在: {self._settings.weibo_pool_file}")

        weibo = WeiboClient(self._settings, client)
        posts: list[Post] = []
        failed = 0
        for index, account in enumerate(accounts):
            if index:
                await _sleep_between(self._settings)
            try:
                hits = await weibo.timeline(account.uid)
            except WeiboClientError as exc:
                # 单个账号失败不该让整个源失败：34 个账号里挂一两个是常态
                failed += 1
                logger.warning("weibo timeline failed: name=%s uid=%s %s",
                               account.name, account.uid, exc)
                continue
            for hit in hits:
                posts.append(
                    hit_to_post(hit, uid=self.key, screen_name=f"微博·{account.name}")
                )

        if failed == len(accounts):
            raise FetchError(f"账号池全部抓取失败（{failed} 个）")
        logger.info("weibo pool: %d 个账号，%d 条，失败 %d", len(accounts), len(posts), failed)
        return posts


class WeiboSearchSource:
    """固定关键词搜索。

    关键词必须是专有名词（品牌名、车型名）：实测「新能源汽车」这类泛词
    搜回来的基本是广告和科普，15 条里只有 2～3 条相关。
    """

    key = "weibo-search"
    name = "微博·关键词"

    def __init__(self, name: str | None = None, *, settings: Settings) -> None:
        if name:
            self.name = name
        self._settings = settings

    async def fetch(self, client: httpx.AsyncClient) -> list[Post]:
        queries = [q.strip() for q in self._settings.weibo_search_queries if q.strip()]
        if not queries:
            return []

        weibo = WeiboClient(self._settings, client)
        posts: list[Post] = []
        failed = 0
        for index, query in enumerate(queries):
            if index:
                await _sleep_between(self._settings)
            try:
                hits = await weibo.search(query)
            except WeiboClientError as exc:
                failed += 1
                logger.warning("weibo search failed: query=%s %s", query, exc)
                continue
            for hit in hits:
                # 搜索结果噪声远大于账号池：低互动且低粉丝的基本是营销号。
                # 刚官宣的事件互动量普遍为 0，所以粉丝量兜底，不能只看互动。
                if (
                    hit.engagement < self._settings.weibo_min_engagement
                    and hit.followers < self._settings.weibo_min_followers
                ):
                    continue
                posts.append(hit_to_post(hit, uid=self.key, screen_name=f"微博·{hit.screen_name}"))

        if queries and failed == len(queries):
            raise FetchError(f"关键词搜索全部失败（{failed} 个）")
        logger.info("weibo search: %d 个词，%d 条，失败 %d", len(queries), len(posts), failed)
        return posts
