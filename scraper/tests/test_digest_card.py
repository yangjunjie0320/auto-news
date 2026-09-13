from __future__ import annotations

import datetime as dt

from src.digest.card import add_doc_link, build_digest_cards
from src.digest.events import Brief, Event
from src.digest.store import DigestRecord
from src.digest.weibo import Discussion
from src.weibo_client import WeiboHit


def _record(mid: str, *, kind: str = "web", **overrides) -> DigestRecord:
    base = {
        "kind": kind,
        "mid": mid,
        "source": "易车·易车原创" if kind == "web" else "@42号车库",
        "title": f"这是一条足够长的{kind}素材标题{mid}",
        "summary": "- 要点一\n- 要点二",
        "label": "市场数据",
        "url": f"https://example.com/{mid}",
        "created_at": dt.datetime(2026, 8, 1, 2, 0, tzinfo=dt.UTC),
    }
    base.update(overrides)
    return DigestRecord(**base)


def _hit(text: str) -> WeiboHit:
    return WeiboHit(
        mid="777",
        uid="42",
        screen_name="测试博主",
        followers=100_000,
        verified=True,
        created_at=dt.datetime(2026, 8, 1, 3, 0, tzinfo=dt.UTC),
        text=text,
        engagement=10,
        is_repost=False,
    )


def _markdown_of(cards: list[dict]) -> str:
    return "\n".join(
        el["content"]
        for card in cards
        for el in card["body"]["elements"]
        if el.get("tag") == "markdown"
    )


def test_feature_card_has_title_and_zh_points_only() -> None:
    """详讯卡只放标题+中文缩写：微博引用与英文对照不进卡片。"""
    event = Event(
        title="零跑交付破10万",
        label="市场数据",
        records=[_record("w1"), _record("m1", kind="weibo")],
        brief=Brief(title="零跑7月交付101,267辆", pairs=[("中文要点", "English point")]),
    )
    discussions = {
        event.title: [Discussion(hit=_hit("第一段观点\n\n第二段观点"), angle="实车体验")]
    }
    cards = build_digest_cards(dt.date(2026, 8, 1), [event], discussions)
    markdown = _markdown_of(cards)
    assert "**[零跑7月交付101,267辆](https://example.com/w1)**" in markdown
    assert "- 中文要点" in markdown
    assert "English point" not in markdown
    assert "> " not in markdown
    assert cards[0]["header"]["title"]["content"].endswith("详讯")


def test_feature_without_brief_falls_back_to_summary() -> None:
    event = Event(title="零跑交付破10万", label="市场数据", records=[_record("w1")])
    markdown = _markdown_of(build_digest_cards(dt.date(2026, 8, 1), [event], {}))
    assert "**[零跑交付破10万](https://example.com/w1)**" in markdown
    assert "- 要点一" in markdown


def test_single_weibo_event_goes_to_short_card() -> None:
    feature = Event(title="详讯事件", label="市场数据", records=[_record("w1")])
    short = Event(
        title="短讯事件",
        label="行业观察",
        records=[_record("m1", kind="weibo", url="https://weibo.com/42/abc")],
    )
    cards = build_digest_cards(dt.date(2026, 8, 1), [feature, short], {})
    titles = [c["header"]["title"]["content"] for c in cards]
    assert any(t.endswith("详讯") for t in titles)
    assert any(t.endswith("短讯") for t in titles)
    short_cards = [c for c in cards if c["header"]["title"]["content"].endswith("短讯")]
    assert "https://weibo.com/42/abc" in _markdown_of(short_cards)
    assert "短讯事件" not in _markdown_of([c for c in cards if c not in short_cards])


def test_multi_weibo_event_is_feature() -> None:
    event = Event(
        title="纯博主事件",
        label="行业观察",
        records=[_record("m1", kind="weibo"), _record("m2", kind="weibo")],
    )
    cards = build_digest_cards(dt.date(2026, 8, 1), [event], {})
    assert cards[0]["header"]["title"]["content"].endswith("详讯")


def test_strays_merge_into_short_card() -> None:
    event = Event(title="详讯事件", label="市场数据", records=[_record("w1")])
    strays = [_record("m2", kind="weibo"), _record("m3", kind="weibo", title="短")]
    cards = build_digest_cards(dt.date(2026, 8, 1), [event], {}, strays=strays)
    short_cards = [c for c in cards if c["header"]["title"]["content"].endswith("短讯")]
    markdown = _markdown_of(short_cards)
    assert "https://example.com/m2" in markdown
    assert "https://example.com/m3" not in markdown  # 过短碎片被过滤


def test_add_doc_link_appends_to_first_card() -> None:
    cards = build_digest_cards(
        dt.date(2026, 8, 1),
        [Event(title="事件", label="市场数据", records=[_record("w1")])],
        {},
    )
    add_doc_link(cards, "https://feishu.cn/docx/abc")
    assert "[查看完整日报文档](https://feishu.cn/docx/abc)" in _markdown_of(cards[:1])
