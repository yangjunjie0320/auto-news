"""日报卡片渲染。

结构：导语卡 → 详讯卡 → 短讯卡。详讯（有网站文章或多条微博的事件）按热度
排序，卡片只放标题和中文缩写——英文对照、图片、原文在飞书文档里；短讯
（单条微博的事件 + 散装观点）合并成简短列表。多事件顶到飞书单卡 30 KB
上限时拆卡连发。
"""

from __future__ import annotations

import datetime as dt

from ..card import CARD_PAYLOAD_MAX_BYTES, CST, _card_payload_bytes, _clean_card_text, _truncate
from .events import Event
from .store import DigestRecord
from .weibo import Discussion

_EVENTS_PER_CARD = 8
_TITLE_MAX_CHARS = 60
_POINT_MAX_CHARS = 100
_MAX_STRAYS = 12


def heat(event: Event, discussions: list[Discussion]) -> tuple[int, dt.datetime]:
    """热度：覆盖网站数 + 微博数。并列时新的在前。"""
    return len(event.records) + len(discussions), event.primary.created_at


def _summary_points(event: Event) -> list[str]:
    points: list[str] = []
    for line in event.primary.summary.splitlines():
        point = line.strip().lstrip("-*•· ").strip()
        if point and point not in points:
            points.append(_truncate(_clean_card_text(point), _POINT_MAX_CHARS))
        if len(points) >= 3:
            break
    return points


def event_points_zh(event: Event) -> list[str]:
    """中文缩写：精修产物优先；降级链 brief → 盘点 points → 主源摘要。"""
    if event.brief and event.brief.pairs:
        return [zh for zh, _ in event.brief.pairs]
    if event.points:
        return list(event.points)
    return _summary_points(event)


def event_display_title(event: Event) -> str:
    """展示标题：精修标题优先。event.title 不改——discussions 以它为键。"""
    return (event.brief.title if event.brief else "") or event.title


def _source_line(event: Event) -> str:
    """来源行。盘点事件成员多且常来自同一站点，按站点聚合计数。"""
    counts: dict[str, int] = {}
    first_url: dict[str, str] = {}
    for record in [event.primary, *event.others]:
        name = _clean_card_text(record.source)
        counts[name] = counts.get(name, 0) + 1
        first_url.setdefault(name, record.url)
    parts = []
    for name, count in counts.items():
        text = (
            f"[{name}]({first_url[name]})"
            if name != _clean_card_text(event.primary.source)
            else name
        )
        parts.append(f"{text} ×{count}" if count > 1 else text)
    meta = event.primary.created_at.astimezone(CST).strftime("%H:%M")
    return f"<font color='grey'>{meta} · {' / '.join(parts)}</font>"


def _feature_elements(event: Event) -> list[dict[str, object]]:
    """详讯块：标题 + 中文缩写 + 来源行。微博原文与英文对照只进文档。"""
    title = _truncate(_clean_card_text(event_display_title(event)), _TITLE_MAX_CHARS)
    lines = [f"**[{title}]({event.primary.url})**"]
    for point in event_points_zh(event):
        lines.append(f"- {_truncate(_clean_card_text(point), _POINT_MAX_CHARS)}")
    lines.append(_source_line(event))
    return [{"tag": "markdown", "content": "\n".join(lines)}]


def _stray_worth_showing(record: DigestRecord) -> bool:
    """过滤无上下文的碎片：转发评论（//@、回复@）和过短的只言片语单独看没有信息量。"""
    text = record.title.strip()
    if "//@" in text or text.startswith("回复@"):
        return False
    return len(text) >= 15


def _short_records(events: list[Event], strays: list[DigestRecord]) -> list[DigestRecord]:
    """短讯素材：单微博事件的成员 + 散装观点（按时间倒序取前 N），按时间排序。"""
    records = [e.primary for e in events if not e.is_feature]
    worthy = [r for r in strays if _stray_worth_showing(r)]
    records += sorted(worthy, key=lambda r: r.created_at, reverse=True)[:_MAX_STRAYS]
    return sorted(records, key=lambda r: r.created_at)


def _short_elements(records: list[DigestRecord]) -> list[list[dict[str, object]]]:
    blocks = []
    for record in records:
        name = _clean_card_text(record.source)
        text = _truncate(_clean_card_text(record.title), 80)
        moment = record.created_at.astimezone(CST).strftime("%H:%M")
        blocks.append(
            [
                {
                    "tag": "markdown",
                    "content": (
                        f"[{name}]({record.url}) {text}\n"
                        f"<font color='grey'>{moment}</font> · [原文]({record.url})"
                    ),
                }
            ]
        )
    return blocks


def _card(title: str, template: str, elements: list[dict[str, object]]) -> dict:
    return {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "body": {"elements": elements},
    }


def _split_to_fit(title: str, template: str, blocks: list[list[dict]]) -> list[dict]:
    """按事件块装卡，装不下就开新卡，保证每张都在飞书大小上限内。"""
    cards: list[dict] = []
    current: list[dict[str, object]] = []
    for block in blocks:
        candidate = current + block
        if current and (
            len(candidate) > _EVENTS_PER_CARD * 2
            or _card_payload_bytes(_card(title, template, candidate)) > CARD_PAYLOAD_MAX_BYTES
        ):
            cards.append(_card(title, template, current))
            current = list(block)
        else:
            current = candidate
    if current:
        cards.append(_card(title, template, current))
    return cards


def build_doc_link_card(day: dt.date, url: str, event_count: int) -> dict:
    """单独发到发布群的文档链接卡片。"""
    date_text = day.strftime("%m月%d日")
    return _card(
        f"{date_text} 汽车资讯日报",
        "carmine",
        [
            {"tag": "markdown", "content": f"[查看完整日报文档]({url})"},
            {
                "tag": "markdown",
                "content": f"<font color='grey'>共 {event_count} 个事件</font>",
            },
        ],
    )


def add_doc_link(cards: list[dict], url: str) -> None:
    """把文档链接放进首卡末尾。首卡通常是导语卡，没有导语时是第一张详讯卡。"""
    if not cards:
        return
    cards[0]["body"]["elements"].append(
        {"tag": "markdown", "content": f"[查看完整日报文档]({url})"}
    )


def build_digest_cards(
    day: dt.date,
    events: list[Event],
    discussions: dict[str, list[Discussion]],
    *,
    intro: str = "",
    strays: list[DigestRecord] | None = None,
) -> list[dict]:
    """渲染整份日报。返回按顺序发送的卡片列表。"""
    date_text = day.strftime("%m月%d日")
    cards: list[dict] = []

    if intro:
        cards.append(
            _card(
                f"{date_text} 汽车资讯日报",
                "carmine",
                [
                    {"tag": "markdown", "content": _clean_card_text(intro)},
                    {
                        "tag": "markdown",
                        "content": (
                            f"<font color='grey'>共 {len(events)} 个事件 · "
                            f"{sum(len(e.records) for e in events)} 篇报道</font>"
                        ),
                    },
                ],
            )
        )

    features = sorted(
        (e for e in events if e.is_feature),
        key=lambda e: heat(e, discussions.get(e.title, [])),
        reverse=True,
    )
    if features:
        title = f"{date_text} · 详讯" if intro else f"{date_text} 汽车资讯日报 · 详讯"
        blocks = [_feature_elements(e) for e in features]
        cards.extend(_split_to_fit(title, "blue", blocks))

    shorts = _short_records(events, strays or [])
    if shorts:
        title = f"{date_text} · 短讯" if intro else f"{date_text} 汽车资讯日报 · 短讯"
        cards.extend(_split_to_fit(title, "grey", _short_elements(shorts)))

    return cards
