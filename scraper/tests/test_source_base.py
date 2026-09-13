from __future__ import annotations

import datetime as dt

import httpx
import pytest

from src.sources.base import FetchError, fetch_html, parse_cn_date, try_parse_cn_date


def test_parse_cn_date_preserves_explicit_iso_timezone() -> None:
    parsed = parse_cn_date("2026-07-17T12:28:27Z")
    assert parsed == dt.datetime(2026, 7, 17, 12, 28, 27, tzinfo=dt.UTC)


def test_parse_cn_date_treats_naive_datetime_as_china_time() -> None:
    parsed = parse_cn_date("2026-06-09 13:52:24")
    assert parsed == dt.datetime(2026, 6, 9, 5, 52, 24, tzinfo=dt.UTC)


def test_strict_date_parser_does_not_mark_invalid_metadata_as_precise() -> None:
    assert try_parse_cn_date("not a date") is None
    assert try_parse_cn_date("2026-99-99 25:61") is None


async def test_fetch_html_rejects_redirect_outside_source_hosts() -> None:
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(
            302,
            headers={"Location": "https://127.0.0.1/private"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(FetchError, match="outside allowed source hosts"):
            await fetch_html(
                client,
                "https://news.example.com/article",
                allowed_hosts={"news.example.com"},
            )
    assert requested_hosts == ["news.example.com"]
