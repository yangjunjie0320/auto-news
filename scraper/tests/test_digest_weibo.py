from __future__ import annotations

import datetime as dt
from dataclasses import replace

from src.digest.weibo import Discussion, _complete_picks
from src.weibo_client import RateLimitedError, WeiboHit


def _hit(mid: str, *, truncated: bool = False) -> WeiboHit:
    return WeiboHit(
        mid=mid,
        uid="42",
        screen_name="测试博主",
        followers=100_000,
        verified=True,
        created_at=dt.datetime(2026, 8, 6, 10, 0, tzinfo=dt.UTC),
        text="截断的正文",
        engagement=10,
        is_repost=False,
        text_truncated=truncated,
    )


class FakeClient:
    def __init__(self, *, rate_limited: bool = False) -> None:
        self.rate_limited = rate_limited
        self.fetched: list[str] = []

    async def fetch_full_text(self, hit: WeiboHit) -> WeiboHit:
        if self.rate_limited:
            raise RateLimitedError("HTTP 432")
        self.fetched.append(hit.mid)
        return replace(hit, text="补全后的完整长文", text_truncated=False)


async def test_complete_picks_fetches_only_truncated() -> None:
    picks = [
        Discussion(hit=_hit("1", truncated=True), angle="实车体验"),
        Discussion(hit=_hit("2"), angle="渠道消息"),
    ]
    client = FakeClient()
    assert await _complete_picks(picks, client) is True
    assert client.fetched == ["1"]
    assert picks[0].hit.text == "补全后的完整长文"
    assert picks[1].hit.text == "截断的正文"


async def test_complete_picks_keeps_truncated_on_rate_limit() -> None:
    picks = [Discussion(hit=_hit("1", truncated=True), angle="")]
    assert await _complete_picks(picks, FakeClient(rate_limited=True)) is False
    assert picks[0].hit.text_truncated  # 截断版照常可用
