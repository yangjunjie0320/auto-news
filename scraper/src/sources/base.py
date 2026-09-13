from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from ..models import Post

logger = logging.getLogger(__name__)

# 中国时区。站点上的日期都是北京时间，统一转成带时区的 UTC 存 created_at。
CST = dt.timezone(dt.timedelta(hours=8))

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5


class FetchError(RuntimeError):
    """抓取失败（网络错误、非 2xx、解析不出任何条目）。由 monitor 归为单源失败。"""


@dataclass(frozen=True)
class ArticleDetail:
    """详情页增强结果；发布时间存在时必须是 tz-aware UTC。"""

    text: str = ""
    summary: str = ""
    published_at: dt.datetime | None = None
    truncated: bool = False


class SourceFetcher(Protocol):
    """一个数据源抓取器。每轮 monitor 调用 fetch() 拿当前列表快照。

    实现约定：
    - key / name：与 sources.yaml 中配置一致；构造时注入。
    - fetch() 返回「当前列表页能看到的条目」，无需自己判断新旧/去重/时效，
      monitor 负责按 mid 去重、冷启动播种、时效过滤。
    - 每个 Post 必须填齐：uid=key、screen_name=name、mid（源内稳定唯一）、
      url、created_at（tz-aware UTC）、title、text_plain（标题+摘要）、image_urls；
      文章源应尽量补充 full_text 与精确发布时间。
    - 抓取失败抛 FetchError；返回空列表表示「本轮列表为空」（会被 monitor 记录但不算异常）。
    """

    key: str
    name: str

    async def fetch(self, client: httpx.AsyncClient) -> list[Post]: ...


async def fetch_html(
    client: httpx.AsyncClient,
    url: str,
    *,
    encoding: str | None = None,
    timeout: float = 20.0,
    referer: str | None = None,
    allowed_hosts: Collection[str] | None = None,
) -> str:
    """GET 一个页面并解码成 str。

    encoding：显式指定时用它解码（如汽车之家用 "gb18030"）；否则用 httpx 的
    自动检测（apparent_encoding / 响应头）。失败抛 FetchError（保留原异常）。
    """
    headers = {"User-Agent": DEFAULT_UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer
    try:
        if allowed_hosts is None:
            resp = await client.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        else:
            current_url = url
            for redirect_count in range(_MAX_REDIRECTS + 1):
                if not is_allowed_https_url(current_url, allowed_hosts):
                    raise FetchError(f"GET target is outside allowed source hosts: {current_url}")
                resp = await client.get(
                    current_url,
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=False,
                )
                if resp.status_code not in _REDIRECT_STATUSES:
                    break
                if redirect_count >= _MAX_REDIRECTS:
                    raise FetchError(f"GET {url} exceeded {_MAX_REDIRECTS} redirects")
                location = resp.headers.get("location")
                if not location:
                    raise FetchError(f"GET {current_url} redirected without Location")
                current_url = urljoin(str(resp.url), location)
        resp.raise_for_status()
    except FetchError:
        raise
    except httpx.HTTPError as exc:
        raise FetchError(f"GET {url} failed: {exc}") from exc
    if encoding:
        return resp.content.decode(encoding, errors="replace")
    return resp.text


def is_allowed_https_url(url: str, allowed_hosts: Collection[str]) -> bool:
    """限制详情请求为指定站点的标准 HTTPS URL，避免列表内容触发任意请求。"""
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme.lower() == "https"
        and hostname in {host.lower() for host in allowed_hosts}
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
    )


def parse_cn_date(text: str, *, now: dt.datetime | None = None) -> dt.datetime:
    """解析站点上的中文/数字日期或时间，返回 tz-aware UTC datetime。

    支持带时区 ISO 8601、"2026-07-16 13:52:24"、"2026/07/16"、
    "7月16日"、"07-16" 等。无显式时区按北京时间解释；无年份时按当前年份
    补齐（跨年时若结果比现在晚半年以上，回退到去年）。解析失败返回 now，
    避免因日期问题丢帖，交给去重兜底。
    """
    normalized_now = now or dt.datetime.now(tz=CST)
    normalized_now = (
        normalized_now.replace(tzinfo=CST)
        if normalized_now.tzinfo is None
        else normalized_now.astimezone(CST)
    )
    parsed = try_parse_cn_date(text, now=normalized_now)
    if parsed is not None:
        return parsed
    logger.debug("parse_cn_date fallback to now: %r", text)
    return normalized_now.astimezone(dt.UTC)


def try_parse_cn_date(text: str, *, now: dt.datetime | None = None) -> dt.datetime | None:
    """严格解析站点时间；无效输入返回 None，供详情页判断时间是否精确。"""
    now = now or dt.datetime.now(tz=CST)
    now = now.replace(tzinfo=CST) if now.tzinfo is None else now.astimezone(CST)
    s = (text or "").strip()

    # 先保留 ISO 8601 自带的时区；无时区的标准格式按北京时间解释。
    try:
        parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    else:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CST)
        return parsed.astimezone(dt.UTC)

    m = re.search(
        r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})"
        r"(?:(?:日)?[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?",
        s,
    )
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = int(m.group(4) or 0)
        minute = int(m.group(5) or 0)
        second = int(m.group(6) or 0)
        return _to_utc(y, mo, d, hour, minute, second)

    m = re.search(r"(\d{1,2})[-/月.](\d{1,2})", s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        try:
            cand = dt.datetime(now.year, mo, d, tzinfo=CST)
        except ValueError:
            return None
        # 无年份：若补出的日期比现在晚半年以上，多半是去年的条目
        if cand - now > dt.timedelta(days=183):
            cand = cand.replace(year=now.year - 1)
        return cand.astimezone(dt.UTC)

    return None


def _to_utc(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
) -> dt.datetime | None:
    try:
        return dt.datetime(year, month, day, hour, minute, second, tzinfo=CST).astimezone(dt.UTC)
    except ValueError:
        return None
