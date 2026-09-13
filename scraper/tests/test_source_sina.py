from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from src.sources.sina import SinaItem, parse_list

FIXTURE = (
    Path(__file__).parent / "fixtures" / "sources" / "sina_newcar.html"
)


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def items(html: str) -> list[SinaItem]:
    return parse_list(html)


def test_parse_list_nonempty(items: list[SinaItem]) -> None:
    assert len(items) > 0


def test_every_item_has_core_fields(items: list[SinaItem]) -> None:
    for it in items:
        assert it.title.strip(), f"empty title: {it}"
        assert it.url.startswith("https://"), f"url not absolute https: {it.url}"
        assert it.mid.startswith("sina-"), f"unexpected mid: {it.mid}"
        # mid 里的数字 id 来自链接，稳定不变
        assert it.mid.removeprefix("sina-").isdigit(), f"mid id not numeric: {it.mid}"


def test_mid_unique_and_stable(html: str) -> None:
    first = parse_list(html)
    second = parse_list(html)
    mids = [it.mid for it in first]
    assert len(mids) == len(set(mids)), "mid 应当唯一"
    # 同一份 HTML 解析两次结果完全一致（稳定）
    assert mids == [it.mid for it in second]


def test_created_at_is_datetime(items: list[SinaItem]) -> None:
    from src.sources.base import parse_cn_date

    for it in items:
        created = parse_cn_date(it.date_text)
        assert isinstance(created, dt.datetime)
        assert created.tzinfo is not None


def test_known_item_present(items: list[SinaItem]) -> None:
    by_mid = {it.mid: it for it in items}
    # 快照里第一条：五菱星光L / db.auto.sina.com.cn/7045/
    assert "sina-7045" in by_mid
    wuling = by_mid["sina-7045"]
    assert wuling.title == "五菱星光L"
    assert wuling.url == "https://db.auto.sina.com.cn/7045/"
    assert wuling.date_text == "2026-07-16"
    assert wuling.image_urls and wuling.image_urls[0].startswith("https://")
