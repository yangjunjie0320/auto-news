import datetime as dt

from src.models import Post
from src.monitor import Monitor, is_stale_precise_article


def _post(created_at: dt.datetime, *, precise: bool) -> Post:
    return Post(
        uid="source",
        mid="mid",
        url="https://example.com/article",
        created_at=created_at,
        created_at_is_precise=precise,
    )


def test_only_precisely_dated_old_articles_are_stale() -> None:
    now = dt.datetime(2026, 7, 18, 12, tzinfo=dt.UTC)
    old = now - dt.timedelta(hours=73)
    recent = now - dt.timedelta(hours=71)
    assert is_stale_precise_article(_post(old, precise=True), 72, now=now)
    assert not is_stale_precise_article(_post(recent, precise=True), 72, now=now)
    assert not is_stale_precise_article(_post(old, precise=False), 72, now=now)


class _FakeState:
    """只实现 _is_due 需要的那部分。"""

    def __init__(self, last: dt.datetime | None) -> None:
        self._last = last

    def last_poll(self, uid: str) -> dt.datetime | None:
        return self._last


class _FakeFetcher:
    key = "weibo-pool"

    def __init__(self, interval: int | None) -> None:
        self.interval_seconds = interval


def _monitor_with(last: dt.datetime | None) -> Monitor:
    m = Monitor.__new__(Monitor)
    m._state = _FakeState(last)
    return m


def test_source_without_interval_is_always_due():
    assert _monitor_with(None)._is_due(_FakeFetcher(None)) is True


def test_never_polled_source_is_due():
    assert _monitor_with(None)._is_due(_FakeFetcher(86400)) is True


def test_source_within_interval_is_skipped():
    """微博每天一次：刚抓过 1 小时，本轮必须跳过，否则访客 cookie 会被限流。"""
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
    assert _monitor_with(recent)._is_due(_FakeFetcher(86400)) is False


def test_source_past_interval_is_due():
    stale = dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)
    assert _monitor_with(stale)._is_due(_FakeFetcher(86400)) is True
