from __future__ import annotations

import asyncio
import datetime as dt
import logging
import random
import time
from typing import Any, Protocol

import httpx

from .config import Settings
from .health import HealthStore, empty_cycle, utc_now
from .models import Post, PushResult
from .sources import FetchError, SourceFetcher
from .state import StateStore

logger = logging.getLogger(__name__)


class Pusher(Protocol):
    async def push(self, post: Post) -> bool | PushResult: ...


def is_stale_precise_article(
    post: Post, max_age_hours: int, *, now: dt.datetime | None = None
) -> bool:
    if not post.created_at_is_precise:
        return False
    now = now or dt.datetime.now(dt.UTC)
    return post.created_at < now - dt.timedelta(hours=max_age_hours)


class Monitor:
    """遍历数据源抓取当前列表，按 mid 去重后推送新条目。

    去重与微博版一致：StateStore 以 source key（= post.uid）为命名空间记录已见 mid。
    首次见到某源时静默播种全部条目、不推送，防冷启动刷屏。
    """

    def __init__(
        self,
        settings: Settings,
        fetchers: list[SourceFetcher],
        state: StateStore,
        pusher: Pusher,
        health: HealthStore,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._fetchers = fetchers
        self._state = state
        self._pusher = pusher
        self._health = health
        self._http = http_client

    async def run_forever(self) -> None:
        self._health.mark_starting(len(self._fetchers))
        while True:
            started = time.monotonic()
            try:
                summary = await self.run_cycle()
            except Exception as exc:
                logger.exception("cycle failed unexpectedly")
                summary = empty_cycle(len(self._fetchers))
                summary["failed"] = 1
                summary["last_error"] = _error("internal", exc)
                summary["internal_failed"] = True
            elapsed = time.monotonic() - started
            delay = max(self._settings.poll_interval_seconds - elapsed, 30.0)

            if _is_healthy(summary):
                self._health.write(
                    status="healthy",
                    cycle=_cycle_stats(summary),
                    next_cycle_at=utc_now() + dt.timedelta(seconds=delay),
                    last_error=None,
                    mark_healthy=True,
                )
            else:
                if summary.get("internal_failed"):
                    delay = max(delay, self._settings.upstream_error_rest_seconds)
                status = "failed" if summary.get("internal_failed") else "degraded"
                self._health.write(
                    status=status,
                    cycle=_cycle_stats(summary),
                    next_cycle_at=utc_now() + dt.timedelta(seconds=delay),
                    last_error=summary.get("last_error"),
                )
            logger.info("cycle done in %.0fs, next in %.0fs", elapsed, delay)
            await asyncio.sleep(delay)

    async def run_cycle(self) -> dict[str, Any]:
        fetchers = list(self._fetchers)
        random.shuffle(fetchers)
        summary: dict[str, Any] = empty_cycle(len(fetchers))
        self._health.write(status="starting", cycle=_cycle_stats(summary), last_error=None)

        for index, fetcher in enumerate(fetchers):
            summary["attempted"] += 1
            try:
                new, pushed, dropped = await self._poll_source(fetcher)
                summary["succeeded"] += 1
                summary["new"] += new
                summary["pushed"] += pushed
                summary["dropped"] += dropped
            except Exception as exc:
                summary["failed"] += 1
                summary["last_error"] = _error("source", exc)
                logger.warning(
                    "source poll failed: key=%s name=%s error=%s",
                    fetcher.key,
                    fetcher.name,
                    exc,
                )
            self._state.save()
            self._health.write(
                status="starting",
                cycle=_cycle_stats(summary),
                last_error=summary.get("last_error"),
            )
            if index < len(fetchers) - 1:
                await asyncio.sleep(
                    random.uniform(
                        self._settings.source_delay_min_seconds,
                        self._settings.source_delay_max_seconds,
                    )
                )

        logger.info(
            "cycle summary: sources=%d attempted=%d succeeded=%d new=%d pushed=%d "
            "dropped=%d failed=%d",
            summary["accounts_total"],
            summary["attempted"],
            summary["succeeded"],
            summary["new"],
            summary["pushed"],
            summary["dropped"],
            summary["failed"],
        )
        return summary

    def finish_once(self, summary: dict[str, Any]) -> None:
        """为 --once 落一个终态。"""
        if _is_healthy(summary):
            self._health.write(
                status="healthy",
                cycle=_cycle_stats(summary),
                next_cycle_at=None,
                last_error=None,
                mark_healthy=True,
            )
        else:
            status = "failed" if summary.get("internal_failed") else "degraded"
            self._health.write(
                status=status,
                cycle=_cycle_stats(summary),
                next_cycle_at=None,
                last_error=summary.get("last_error"),
            )

    async def _poll_source(self, fetcher: SourceFetcher) -> tuple[int, int, int]:
        key = fetcher.key
        now_iso = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        seeded = self._state.has_account(key)

        try:
            posts = await fetcher.fetch(self._http)
        except FetchError:
            raise
        except Exception as exc:  # 抓取器内部异常统一归为源失败，保留原异常
            raise FetchError(f"{key} fetch crashed: {exc}") from exc

        # 源内去重：同一 mid 只留一次
        deduped: list[Post] = []
        seen_ids: set[str] = set()
        for post in posts:
            if post.mid and post.mid not in seen_ids:
                seen_ids.add(post.mid)
                deduped.append(post)
        posts = deduped

        if not posts:
            if seeded:
                self._state.mark_seen(key, [], last_poll=now_iso)
            logger.info("no items parsed: key=%s", key)
            return 0, 0, 0

        if not seeded:
            self._state.mark_seen(key, [p.mid for p in posts], last_poll=now_iso)
            logger.info("source seeded: key=%s items=%d", key, len(posts))
            return 0, 0, 0

        # 去重（mid）是「有没有新内容」的主信号。详情页有精确发布时间时丢弃超龄
        # 文章；只有日/月粒度的上市日期不参与时效过滤。防刷屏仍靠每轮推送上限：
        # 新条目过多（列表结构突变、id 方案变化等异常）时只推最新的 cap 条。
        new_posts = [p for p in posts if not self._state.is_seen(key, p.mid)]
        discovered_count = len(new_posts)
        stale_posts = [
            post
            for post in new_posts
            if is_stale_precise_article(post, self._settings.max_article_age_hours)
        ]
        if stale_posts:
            self._state.mark_seen(key, [post.mid for post in stale_posts])
            logger.info(
                "source %s: dropped %d stale article(s) older than %dh",
                key,
                len(stale_posts),
                self._settings.max_article_age_hours,
            )
        stale_mids = {post.mid for post in stale_posts}
        new_posts = [post for post in new_posts if post.mid not in stale_mids]
        new_posts.sort(key=lambda p: p.created_at, reverse=True)
        cap = self._settings.max_new_pushes_per_cycle
        overflow = new_posts[cap:]
        new_posts = new_posts[:cap]
        if overflow:
            logger.warning(
                "source %s: new items exceed cap %d, marking %d overflow seen without push",
                key,
                cap,
                len(overflow),
            )
            self._state.mark_seen(key, [p.mid for p in overflow])

        pushed = 0
        dropped = len(stale_posts)
        # 批内按时间正序推，让飞书里新的排在下面（符合阅读顺序）
        for post in sorted(new_posts, key=lambda p: p.created_at):
            raw_result = await self._pusher.push(post)
            result = (
                raw_result
                if isinstance(raw_result, PushResult)
                else PushResult(handled=bool(raw_result), pushed=bool(raw_result))
            )
            if result.handled:
                pushed += int(result.pushed)
                dropped += int(result.dropped)
                self._state.mark_seen(key, [post.mid])
            else:
                logger.error("push failed, will retry next cycle: key=%s mid=%s", key, post.mid)

        self._state.mark_seen(key, [], last_poll=now_iso)
        if new_posts:
            logger.info(
                "source polled: key=%s new=%d pushed=%d dropped=%d",
                key,
                len(new_posts),
                pushed,
                dropped,
            )
        return discovered_count, pushed, dropped


def _error(kind: str, exc: BaseException) -> dict[str, str]:
    message = " ".join(str(exc).split())[:240]
    return {"kind": kind, "message": message or type(exc).__name__}


def _cycle_stats(summary: dict[str, Any]) -> dict[str, int | bool | str]:
    return {
        "accounts_total": int(summary.get("accounts_total", 0)),
        "attempted": int(summary.get("attempted", 0)),
        "succeeded": int(summary.get("succeeded", 0)),
        "failed": int(summary.get("failed", 0)),
        "new": int(summary.get("new", 0)),
        "pushed": int(summary.get("pushed", 0)),
        "dropped": int(summary.get("dropped", 0)),
        "rate_limited": bool(summary.get("rate_limited", False)),
        "source_requests": int(summary.get("source_requests", 0)),
        "source": str(summary.get("source", "")),
    }


def _is_healthy(summary: dict[str, Any]) -> bool:
    return bool(
        summary.get("attempted") == summary.get("accounts_total")
        and summary.get("succeeded") == summary.get("accounts_total")
        and not summary.get("failed")
    )
