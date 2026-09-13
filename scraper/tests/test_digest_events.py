from __future__ import annotations

import datetime as dt

from src.digest.events import Event, _apply_matches, _clean_points, _parse_events
from src.digest.store import DigestRecord


def _record(mid: str, *, kind: str = "web", label: str = "市场数据", **overrides) -> DigestRecord:
    base = {
        "kind": kind,
        "mid": mid,
        "source": "易车·易车原创" if kind == "web" else "@42号车库",
        "title": f"标题{mid}",
        "summary": f"- 要点{mid}",
        "label": label,
        "url": f"https://example.com/{mid}",
        "created_at": dt.datetime(2026, 8, 1, 10, 0, tzinfo=dt.UTC),
    }
    base.update(overrides)
    return DigestRecord(**base)


def test_parse_events_keeps_missed_articles_as_fallback_events() -> None:
    records = [_record("1"), _record("2"), _record("3")]
    data = {"events": [{"title": "事件A", "articles": [1, 2], "queries": ["零跑 交付"]}]}
    events = _parse_events(data, records)
    assert [e.title for e in events] == ["事件A", "标题3"]
    assert [r.mid for r in events[0].records] == ["1", "2"]


def test_parse_events_honors_skip_but_events_win_conflicts() -> None:
    records = [_record("1"), _record("2"), _record("3"), _record("4")]
    data = {
        "skip": [2, 3, 9, "x"],
        "events": [{"title": "事件A", "articles": [1, 3], "queries": []}],
    }
    events = _parse_events(data, records)
    # 2 被剔除且不兜底补回；3 同时在事件里，以事件为准；4 漏掉仍兜底
    assert [e.title for e in events] == ["事件A", "标题4"]
    assert [r.mid for r in events[0].records] == ["1", "3"]


def test_parse_events_ignores_duplicate_and_out_of_range_indexes() -> None:
    records = [_record("1"), _record("2")]
    data = {
        "events": [
            {"title": "A", "articles": [1, 1, 9], "queries": []},
            {"title": "B", "articles": [1, 2], "queries": []},
        ]
    }
    events = _parse_events(data, records)
    assert [r.mid for r in events[0].records] == ["1"]
    assert [r.mid for r in events[1].records] == ["2"]


def test_parse_events_reads_roundup_points() -> None:
    records = [_record("1"), _record("2")]
    data = {
        "events": [
            {
                "title": "7月交付盘点",
                "kind": "roundup",
                "articles": [1, 2],
                "points": ["- 零跑 101,267（首破10万）", "蔚来 35,934", ""],
                "queries": ["零跑 10万"],
            }
        ]
    }
    events = _parse_events(data, records)
    assert events[0].kind == "roundup"
    assert events[0].points == ["零跑 101,267（首破10万）", "蔚来 35,934"]


def test_clean_points_caps_length_and_dedupes() -> None:
    points = _clean_points(["a" * 100, "a" * 100, 42, "b"])
    assert points == ["a" * 60, "b"]


def test_event_primary_prefers_web_records() -> None:
    web = _record("w1", summary="- 很长的要点内容")
    weibo = _record("m1", kind="weibo", summary="- 更长更长更长的微博要点内容")
    event = Event(title="t", label="市场数据", records=[weibo, web])
    assert event.primary.mid == "w1"
    assert [r.mid for r in event.weibo_records] == ["m1"]


def test_apply_matches_attaches_and_creates_new_events() -> None:
    events = [
        Event(
            title="零跑7月交付破10万",
            label="市场数据",
            records=[_record("w1", title="零跑7月交付101267台")],
        )
    ]
    batch = [
        _record("m1", kind="weibo", title="零跑这个交付成绩说明渠道下沉见效了"),
        _record("m2", kind="weibo", label="谍照申报"),
        _record("m3", kind="weibo", label="谍照申报"),
        _record("m4", kind="weibo"),
    ]
    data = {
        "drop": [4, 99],
        "assignments": [{"weibo": 1, "event": 1}, {"weibo": 9, "event": 1}],
        "new_events": [{"title": "新车谍照曝光", "weibos": [2, 3]}],
    }
    new_events, consumed = _apply_matches(data, events, batch)
    assert [r.mid for r in events[0].records] == ["w1", "m1"]
    assert len(new_events) == 1
    assert new_events[0].title == "新车谍照曝光"
    assert new_events[0].label == "谍照申报"
    # m4 被 drop：纯复述既不挂事件也不进散装观点
    assert consumed == {"m1", "m2", "m3", "m4"}


def test_apply_matches_discards_roundup_assignments() -> None:
    roundup = Event(
        title="新势力7月交付盘点",
        label="市场数据",
        records=[_record("w1")],
        kind="roundup",
        points=["零跑 101,267"],
    )
    batch = [_record("m1", kind="weibo", title="零跑7月交付101267台，同比+102%")]
    data = {"assignments": [{"weibo": 1, "event": 1}]}
    _, consumed = _apply_matches(data, [roundup], batch)
    # 挂向盘点的一律按复述丢弃：消费掉但不入事件
    assert consumed == {"m1"}
    assert [r.mid for r in roundup.records] == ["w1"]


def test_apply_matches_rejects_lexically_unrelated_assignment() -> None:
    event = Event(
        title="一汽-大众全系双终身质保",
        label="产品发布",
        records=[_record("w1", title="一汽-大众推出全系双终身质保")],
    )
    batch = [_record("m1", kind="weibo", title="红旗天工08上市 权益价17.99万")]
    data = {"assignments": [{"weibo": 1, "event": 1, "event_title": "一汽-大众全系双终身质保"}]}
    _, consumed = _apply_matches(data, [event], batch)
    # 标题抄对但内容无关：词面防线拒绝，落回散装观点
    assert consumed == set()
    assert [r.mid for r in event.records] == ["w1"]


def test_apply_matches_rejects_assignment_with_mismatched_title() -> None:
    events = [Event(title="一汽-大众全系双终身质保", label="产品发布", records=[_record("w1")])]
    batch = [
        _record("m1", kind="weibo", title="一汽-大众终身质保覆盖备件"),
        _record("m2", kind="weibo", title="一汽-大众终身质保对经销商是利好"),
    ]
    data = {
        "assignments": [
            {"weibo": 1, "event": 1, "event_title": "红旗天工08上市"},
            {"weibo": 2, "event": 1, "event_title": "一汽-大众全系双终身质保"},
        ]
    }
    _, consumed = _apply_matches(data, events, batch)
    # 标题对不上的挂载作废，微博落回散装观点
    assert consumed == {"m2"}
    assert [r.mid for r in events[0].records] == ["w1", "m2"]


def test_apply_matches_does_not_double_consume_a_weibo() -> None:
    events = [
        Event(
            title="零跑交付破10万",
            label="市场数据",
            records=[_record("w1", title="零跑7月交付101267台")],
        )
    ]
    batch = [_record("m1", kind="weibo", title="零跑交付破10万的渠道原因分析")]
    data = {
        "assignments": [{"weibo": 1, "event": 1}],
        "new_events": [{"title": "重复", "weibos": [1]}],
    }
    new_events, consumed = _apply_matches(data, events, batch)
    assert consumed == {"m1"}
    assert new_events == []
