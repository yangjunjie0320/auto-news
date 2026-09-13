"""微博客户端：账号池时间线抓取 + 事件搜索，供日报使用。

从 weibo-monitor 移植的精简版，游客 cookie。weibo-monitor 生产环境即以游客
cookie 轮询时间线，此路径有长期生产背书；搜索实测（2026-08-01）游客态每查询
稳定返回 10~15 条。因此不移植登录 cookie、浏览器 profile 与熔断持久化——
日报每天只跑一批，失败整体降级为纯网站日报。

搜索用综合型（type=1，按热度）：实测质量远好于实时型（type=61），后者
中位互动量接近 0，半数以上是零互动的低质内容。
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import html
import json
import logging
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, replace
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

# 每个游客会话固定一个 UA（请求间换 UA 反而像机器人）
USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
]

MOBILE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://m.weibo.cn/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

_VISITOR_URL = "https://visitor.passport.weibo.cn/visitor/genvisitor2"
_VISITOR_CALLBACK_RE = re.compile(r"visitor_gray_callback\((\{.*\})\);?")


class WeiboClientError(Exception):
    pass


class RateLimitedError(WeiboClientError):
    """captcha/visitor 挑战：IP 级限流，重试无益，调用方应放弃本轮微博抓取。"""


@dataclass(frozen=True)
class WeiboHit:
    """一条微博（搜索命中或时间线条目），只保留日报需要的字段。"""

    mid: str
    uid: str
    screen_name: str
    followers: int
    verified: bool
    created_at: dt.datetime
    text: str
    engagement: int
    is_repost: bool
    is_pinned: bool = False
    text_truncated: bool = False
    bid: str = ""
    image_urls: tuple[str, ...] = ()

    @property
    def url(self) -> str:
        # bid 可组桌面端链接；缺失时退回移动端详情页（桌面打开也可用）
        if self.bid and self.uid:
            return f"https://weibo.com/{self.uid}/{self.bid}"
        return f"https://m.weibo.cn/detail/{self.mid}"


def parse_weibo_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(str(value), "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None


def html_to_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(
        r"<img\b[^>]*\balt=[\"']([^\"']*)[\"'][^>]*>", lambda m: m.group(1), text, flags=re.I
    )
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_followers(value: Any) -> int:
    """搜索接口的 followers_count 是 "431.3万" 这类格式化字符串，时间线接口是整数。"""
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        if text.endswith("万"):
            return int(float(text[:-1]) * 10_000)
        if text.endswith("亿"):
            return int(float(text[:-1]) * 100_000_000)
        return int(float(text))
    except ValueError:
        return 0


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def extract_mblogs(data: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """提取 (card, mblog) 对，兼容 statuses 形与 card_type 9/11（嵌套组）。"""
    statuses = data.get("statuses")
    if isinstance(statuses, list):
        return [({}, item) for item in statuses if isinstance(item, dict)]
    results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for card in data.get("data", {}).get("cards", []) or []:
        if card.get("card_type") == 11:
            for child in card.get("card_group") or []:
                if child.get("card_type") == 9 and isinstance(child.get("mblog"), dict):
                    results.append((child, child["mblog"]))
        if card.get("card_type") == 9 and isinstance(card.get("mblog"), dict):
            results.append((card, card["mblog"]))
    return results


def _is_pinned(card: dict[str, Any], mblog: dict[str, Any]) -> bool:
    if card.get("profile_type_id") == "proweibotop_":
        return True
    title = mblog.get("title")
    return isinstance(title, dict) and title.get("text") == "置顶"


def _long_text(mblog: dict[str, Any], extend: dict[str, Any] | None) -> str:
    if extend and extend.get("longTextContent"):
        return str(extend["longTextContent"])
    long_text = mblog.get("longText") or {}
    return str(
        (long_text.get("longTextContent") if isinstance(long_text, dict) else "")
        or mblog.get("longTextContent")
        or mblog.get("raw_text")
        or mblog.get("text_raw")
        or mblog.get("text")
        or ""
    )


def _pic_urls(mblog: dict[str, Any]) -> tuple[str, ...]:
    """提取配图 URL，优先 large 原图。搜索与时间线接口结构一致：
    pics = [{"url": 缩略图, "large": {"url": 原图}}]。解析失败等于无图。"""
    pics = mblog.get("pics")
    if not isinstance(pics, list):
        return ()
    urls: list[str] = []
    for pic in pics:
        if not isinstance(pic, dict):
            continue
        large = pic.get("large")
        url = (large.get("url") if isinstance(large, dict) else "") or pic.get("url") or ""
        if isinstance(url, str) and url.startswith("http"):
            urls.append(url)
    return tuple(urls[:9])


def parse_hit(
    card: dict[str, Any],
    mblog: dict[str, Any],
    extend: dict[str, Any] | None = None,
) -> WeiboHit | None:
    created_at = parse_weibo_datetime(mblog.get("created_at"))
    mid = str(mblog.get("mid") or mblog.get("idstr") or mblog.get("id") or "")
    if not created_at or not mid:
        return None
    user = mblog.get("user") or {}
    has_long_text = bool(
        mblog.get("longTextContent")
        or (isinstance(mblog.get("longText"), dict) and mblog["longText"].get("longTextContent"))
        or (extend and extend.get("longTextContent"))
    )
    return WeiboHit(
        mid=mid,
        uid=str(user.get("id") or ""),
        screen_name=str(user.get("screen_name") or ""),
        followers=parse_followers(user.get("followers_count")),
        verified=bool(user.get("verified")),
        created_at=created_at,
        text=html_to_text(_long_text(mblog, extend)),
        engagement=(
            _int(mblog.get("reposts_count"))
            + _int(mblog.get("comments_count"))
            + _int(mblog.get("attitudes_count"))
        ),
        is_repost=bool(mblog.get("retweeted_status")),
        is_pinned=_is_pinned(card, mblog),
        text_truncated=bool(mblog.get("isLongText") or mblog.get("truncated"))
        and not has_long_text,
        bid=str(mblog.get("bid") or mblog.get("mblogid") or ""),
        image_urls=_pic_urls(mblog),
    )


def search_url(query: str) -> str:
    containerid = f"100103type=1&q={query}"
    return (
        "https://m.weibo.cn/api/container/getIndex?"
        f"containerid={urllib.parse.quote(containerid, safe='')}&page_type=searchall"
    )


class WeiboClient:
    """m.weibo.cn container API 客户端，游客 cookie。"""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http_client
        self._cookie = ""
        self._ua = random.choice(USER_AGENTS)

    def _headers(self) -> dict[str, str]:
        headers = dict(MOBILE_HEADERS)
        headers["User-Agent"] = self._ua
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    async def ensure_cookie(self) -> None:
        if not self._cookie:
            await self.refresh_visitor_cookie()

    async def refresh_visitor_cookie(self) -> None:
        self._ua = random.choice(USER_AGENTS)  # 新游客身份配新 UA
        payload = {
            "cb": "visitor_gray_callback",
            "ver": "20250916",
            "request_id": f"auto_news_monitor_{int(time.time() * 1000)}",
            "tid": "",
            "from": "weibo",
            "webdriver": "false",
            "rid": str(int(time.time() * 1000)),
            "return_url": "https://m.weibo.cn/",
        }
        resp = await self._http.post(
            _VISITOR_URL,
            data=payload,
            headers=self._headers(),
            timeout=self._settings.request_timeout,
        )
        if resp.status_code in {403, 418, 429, 432}:
            raise RateLimitedError(f"visitor endpoint HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise WeiboClientError(f"visitor endpoint HTTP {resp.status_code}")
        match = _VISITOR_CALLBACK_RE.search(resp.text)
        if not match:
            raise WeiboClientError("invalid visitor response")
        try:
            data = json.loads(match.group(1))
            sub = data["data"]["sub"]
            subp = data["data"]["subp"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise WeiboClientError("visitor response missing cookie fields") from exc
        self._cookie = f"SUB={sub}; SUBP={subp}"
        logger.info("weibo visitor cookie refreshed")
        await self._warmup()

    async def _warmup(self) -> None:
        with contextlib.suppress(httpx.HTTPError):
            await self._http.get("https://m.weibo.cn/", headers=self._headers(), timeout=10)

    async def search(self, query: str) -> list[WeiboHit]:
        """搜一个关键词。限流抛 RateLimitedError，其余异常抛 WeiboClientError。"""
        await self.ensure_cookie()
        data = await self._request_json(search_url(query))
        hits = [hit for card, mblog in extract_mblogs(data) if (hit := parse_hit(card, mblog))]
        logger.info("weibo search: query=%s hits=%d", query, len(hits))
        return hits

    async def timeline(self, uid: str, page: int = 1) -> list[WeiboHit]:
        """抓一个账号时间线的一页。"""
        await self.ensure_cookie()
        params: dict[str, str | int] = {
            "type": "uid",
            "value": uid,
            "containerid": f"107603{uid}",
        }
        if page > 1:
            params["page"] = page
        query = urllib.parse.urlencode(params)
        data = await self._request_json(f"https://m.weibo.cn/api/container/getIndex?{query}")
        return [hit for card, mblog in extract_mblogs(data) if (hit := parse_hit(card, mblog))]

    async def fetch_full_text(self, hit: WeiboHit) -> WeiboHit:
        """补全被截断的长文正文；失败返回原 hit（截断版可用，不阻塞）。"""
        if not hit.text_truncated:
            return hit
        try:
            data = await self._request_json(
                f"https://m.weibo.cn/statuses/extend?id={urllib.parse.quote(hit.mid)}"
            )
        except RateLimitedError:
            raise
        except WeiboClientError as exc:
            logger.warning("long text fetch failed: mid=%s error=%s", hit.mid, exc)
            return hit
        extend = data.get("data") or {}
        long_text = extend.get("longTextContent")
        if not long_text:
            return hit
        return replace(hit, text=html_to_text(long_text), text_truncated=False)

    async def _request_json(self, url: str) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                return await self._request_json_once(url)
            except RateLimitedError:
                raise  # IP 级封控，重试与换游客身份都会加重封控
            except (httpx.HTTPError, WeiboClientError) as exc:
                if attempt >= self._settings.request_retries:
                    raise WeiboClientError(f"weibo request failed: {exc}") from exc
                attempt += 1
                await asyncio.sleep(0.8 * attempt)

    async def _request_json_once(self, url: str) -> dict[str, Any]:
        try:
            resp = await self._http.get(
                url, headers=self._headers(), timeout=self._settings.request_timeout
            )
        except httpx.HTTPError as exc:
            raise WeiboClientError(f"transport error: {type(exc).__name__}") from exc

        if resp.status_code in {403, 418, 429, 432}:
            raise RateLimitedError(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise WeiboClientError(f"HTTP {resp.status_code}")
        if not resp.content:
            raise WeiboClientError("empty response")

        try:
            parsed = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            if "Sina Visitor System" in resp.text:
                raise RateLimitedError("visitor challenge") from None
            raise WeiboClientError("invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise WeiboClientError("unexpected JSON shape: expected object")

        challenge = " ".join(
            str(parsed.get(key, "")) for key in ("url", "msg", "message", "errmsg")
        ).lower()
        if parsed.get("ok") != 1:
            if "captcha" in challenge or "visitor" in challenge:
                raise RateLimitedError("challenge response")
            raise WeiboClientError(f"unexpected API status: ok={parsed.get('ok')!r}")
        return parsed
