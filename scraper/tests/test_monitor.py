import datetime as dt

from src.models import Post
from src.monitor import is_stale_precise_article


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
