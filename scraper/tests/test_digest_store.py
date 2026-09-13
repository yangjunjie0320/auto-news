from __future__ import annotations

import datetime as dt
import json

from src.digest.store import DigestRecord, DigestStore


def test_load_day_accepts_legacy_lines_without_new_fields(tmp_path) -> None:
    """旧格式 JSONL（无 full_text/image_urls）必须能照常加载。"""
    legacy = {
        "kind": "web",
        "mid": "m1",
        "source": "易车",
        "title": "旧素材",
        "summary": "- 要点",
        "label": "市场数据",
        "url": "https://example.com/1",
        "created_at": "2026-08-01T10:00:00+08:00",
    }
    path = tmp_path / "2026-08-01.jsonl"
    path.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
    records = DigestStore(tmp_path).load_day(dt.date(2026, 8, 1))
    assert len(records) == 1
    assert records[0].full_text == ""
    assert records[0].image_urls == []


def test_append_roundtrips_full_text_and_images(tmp_path) -> None:
    store = DigestStore(tmp_path)
    record = DigestRecord(
        mid="m2",
        source="易车",
        title="新素材",
        created_at=dt.datetime(2026, 8, 1, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))),
        full_text="完整正文",
        image_urls=["https://p.bitautoimg.com/a.jpg"],
    )
    store.append(record)
    loaded = store.load_day(dt.date(2026, 8, 1))
    assert loaded[0].full_text == "完整正文"
    assert loaded[0].image_urls == ["https://p.bitautoimg.com/a.jpg"]
