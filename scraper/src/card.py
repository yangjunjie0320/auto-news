from __future__ import annotations

import copy
import datetime as dt
import json
import re

from .models import Post

TEXT_FOLD_THRESHOLD = 500
TEXT_PREVIEW_CHARS = 300
REPOST_PREVIEW_CHARS = 300
# 飞书单卡总上限为 30 KB。正文单独控制在 12 KB，为标题、摘要、图片和 JSON
# 转义保留足够余量（引号、反斜杠、换行序列化后会额外占字节）。
FULL_TEXT_CARD_MAX_BYTES = 12_000
CARD_PAYLOAD_MAX_BYTES = 28_000

# 卡片时间按北京时间展示（created_at 内部统一存 UTC）
CST = dt.timezone(dt.timedelta(hours=8))

# 卡片头直接展示数据源；业务分类仅用于筛选和归档。
_SOURCE_TEMPLATES = {
    "yiche-u61014816": "blue",
    "sina-newcar-news": "red",
    "autohome-newbrand": "orange",
}
_AT_TAG_RE = re.compile(r"<\s*(/?)\s*at\b", re.IGNORECASE)
# 尺寸写法 4200*1800*1560 里的星号会被飞书 markdown 解释成斜体，换成乘号
_DIMENSION_STAR_RE = re.compile(r"(?<=\d)\s*\*\s*(?=\d)")


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    total = int(seconds)
    if total >= 3600:
        return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
    return f"{total // 60}:{total % 60:02d}"


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    shortened = encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()
    return shortened + "…", True


def _clean_card_text(text: str) -> str:
    """清理控制字符，并阻止外部正文被解释为飞书 @ 标签或斜体。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 0x20)
    text = _DIMENSION_STAR_RE.sub("×", text)
    return _AT_TAG_RE.sub(lambda match: f"＜{match.group(1)}at", text)


def _card_payload_bytes(card: dict) -> int:
    return len(json.dumps(card, ensure_ascii=False).encode("utf-8"))


def _shrink_markdown_to_fit(card: dict, element: dict[str, object] | None) -> bool:
    """按最终 JSON 字节数二分回缩折叠正文；返回正文是否被进一步截断。"""
    if element is None or _card_payload_bytes(card) <= CARD_PAYLOAD_MAX_BYTES:
        return False
    original = element.get("content")
    if not isinstance(original, str) or not original:
        return False

    low, high = 0, len(original)
    best: str | None = None
    while low <= high:
        middle = (low + high) // 2
        candidate = original[:middle].rstrip()
        if middle < len(original):
            candidate += "…"
        element["content"] = candidate
        if _card_payload_bytes(card) <= CARD_PAYLOAD_MAX_BYTES:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1

    element["content"] = best if best is not None else "…"
    return element["content"] != original


def _forward_button(post: Post) -> dict[str, object]:
    """转发按钮。点击走 card.action.trigger 回调（长连接监听）。"""
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": "转发"},
        "type": "primary",
        "width": "default",
        "behaviors": [
            {
                "type": "callback",
                "value": {"action": "forward", "mid": post.mid, "uid": post.uid},
            }
        ],
    }


def _is_forward_button(element: dict) -> bool:
    for behavior in element.get("behaviors", []):
        value = behavior.get("value") or {}
        if behavior.get("type") == "callback" and value.get("action") == "forward":
            return True
    return False


def mark_forwarded(card: dict) -> dict:
    """把转发按钮替换成"已转发"文案，供 patch 原卡片用。"""
    card = copy.deepcopy(card)
    elements = card.get("body", {}).get("elements", [])
    replacement = {"tag": "markdown", "content": "**已转发**"}
    for index, element in enumerate(elements):
        if _is_forward_button(element):
            elements[index] = replacement
            return card
    elements.append(replacement)
    return card


def build_post_card(
    post: Post,
    image_key: str | None = None,
    label: str = "",
    *,
    summary: str = "",
    headline: str = "",
    with_forward: bool = True,
) -> dict:
    source_name = _truncate(_clean_card_text(post.screen_name), 80)
    header_title = source_name or _truncate(_clean_card_text(label), 80) or "汽车资讯"

    # 文章标题置顶（优先用事实化重写标题）；时间、（转发标记）和原文链接紧随其后。
    title = _clean_card_text(headline or post.title).strip()
    elements: list[dict[str, object]] = []
    if title:
        elements.append({"tag": "markdown", "content": f"**{title}**"})

    meta_parts = [post.created_at.astimezone(CST).strftime("%m-%d %H:%M")]
    if post.is_repost:
        meta_parts.append("转发")
    link_text = "原文" if post.title else "原帖"
    meta_parts.append(f"[{link_text}]({post.url})")
    elements.append({"tag": "markdown", "content": " · ".join(meta_parts)})

    # 文章场景：要点摘要在外层，详情页全文始终默认折叠。
    # 正文开头去重按原始标题算（重写标题不会出现在正文里）。
    original_title = _clean_card_text(post.title).strip()
    body = _clean_card_text(post.text_plain).strip()
    if original_title and body.startswith(original_title):
        body = body[len(original_title) :].strip()
    full_text = _clean_card_text(post.full_text).strip()
    summary_text = _clean_card_text(summary).strip()
    card_text_truncated = False
    folded_text_element: dict[str, object] | None = None
    if full_text:
        if not summary_text:
            summary_text = _truncate(full_text, TEXT_PREVIEW_CHARS)
        if summary_text and summary_text != title:
            elements.append({"tag": "markdown", "content": summary_text})
        card_full_text, card_text_truncated = _truncate_utf8(full_text, FULL_TEXT_CARD_MAX_BYTES)
        folded_text_element = {"tag": "markdown", "content": card_full_text}
        elements.append(
            {
                "tag": "collapsible_panel",
                "expanded": False,
                "header": {"title": {"tag": "markdown", "content": "**查看完整文案**"}},
                "elements": [folded_text_element],
            }
        )
    else:
        text = body
        if summary_text and summary_text != title:
            elements.append({"tag": "markdown", "content": summary_text})
        if text and len(text) > TEXT_FOLD_THRESHOLD:
            if not summary_text:
                elements.append(
                    {
                        "tag": "markdown",
                        "content": _truncate(text, TEXT_PREVIEW_CHARS),
                    }
                )
            card_text, was_truncated = _truncate_utf8(text, FULL_TEXT_CARD_MAX_BYTES)
            card_text_truncated = card_text_truncated or was_truncated
            folded_text_element = {"tag": "markdown", "content": card_text}
            elements.append(
                {
                    "tag": "collapsible_panel",
                    "expanded": False,
                    "header": {"title": {"tag": "markdown", "content": "**展开全文**"}},
                    "elements": [folded_text_element],
                }
            )
        elif text and not summary_text:
            elements.append({"tag": "markdown", "content": text})

    truncation_note_added = False
    if post.text_truncated or card_text_truncated:
        elements.append(
            {
                "tag": "markdown",
                "content": f"*长文正文可能被截断，请[查看{link_text}]({post.url})。*",
            }
        )
        truncation_note_added = True

    if post.is_repost:
        quoted = _truncate(_clean_card_text(post.retweeted_text_plain), REPOST_PREVIEW_CHARS)
        author_name = _truncate(_clean_card_text(post.retweeted_screen_name), 80)
        author = f"@{author_name}" if author_name else "原帖"
        lines = "\n".join(f"> {line}" for line in f"转发自 {author}：\n{quoted}".splitlines())
        elements.append({"tag": "markdown", "content": lines})

    if post.video:
        video_line = "视频"
        if post.video.title:
            video_line += f"：{_truncate(_clean_card_text(post.video.title), 160)}"
        duration = _fmt_duration(post.video.duration)
        if duration:
            video_line += f"（{duration}）"
        elements.append({"tag": "markdown", "content": video_line})

    if image_key:
        # 折叠面板内必须用 markdown 图片语法：img 组件在面板里渲染时有时无
        total = len(post.image_urls)
        panel_title = f"**查看图片（共 {total} 张）**" if total > 1 else "**查看图片**"
        elements.append(
            {
                "tag": "collapsible_panel",
                "expanded": False,
                "header": {"title": {"tag": "markdown", "content": panel_title}},
                "elements": [{"tag": "markdown", "content": f"![图片]({image_key})"}],
            }
        )
    elif post.image_urls:
        note = f"图片 {len(post.image_urls)} 张（未能上传）"
        elements.append({"tag": "markdown", "content": note})

    if with_forward:
        elements.append(_forward_button(post))

    card = {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "template": _SOURCE_TEMPLATES.get(post.uid, "blue"),
        },
        "body": {"elements": elements},
    }
    payload_truncated = _shrink_markdown_to_fit(card, folded_text_element)
    if payload_truncated and not truncation_note_added:
        note = {
            "tag": "markdown",
            "content": f"*长文正文可能被截断，请[查看{link_text}]({post.url})。*",
        }
        insert_at = len(elements)
        if elements and elements[-1].get("tag") == "button":
            insert_at -= 1
        elements.insert(insert_at, note)
        _shrink_markdown_to_fit(card, folded_text_element)

    if _card_payload_bytes(card) > CARD_PAYLOAD_MAX_BYTES:
        # 极端情况下（例如异常超长 URL/ID）宁可退化为小卡片，也不要发送必失败载荷。
        fallback_note = "内容过长，卡片正文已省略。"
        if len(post.url.encode("utf-8")) <= 2_000:
            fallback_note = f"内容过长，卡片正文已省略，请[查看{link_text}]({post.url})。"
        fallback_elements: list[dict[str, object]] = []
        if title:
            fallback_elements.append({"tag": "markdown", "content": f"**{_truncate(title, 120)}**"})
        fallback_elements.append({"tag": "markdown", "content": fallback_note})
        card["body"] = {"elements": fallback_elements}

    return card
