import datetime as dt
import json

from src.card import build_post_card
from src.models import Post, VideoInfo

CST = dt.timezone(dt.timedelta(hours=8))


def make_post(**overrides) -> Post:
    base = {
        "uid": "42",
        "screen_name": "测试博主",
        "mid": "m1",
        "bid": "Babc",
        "url": "https://weibo.com/42/Babc",
        "created_at": dt.datetime(2026, 7, 1, 12, 30, tzinfo=CST),
        "text_plain": "今天试驾了一台新车",
        "source": "微博网页版",
        "reposts_count": 12,
        "comments_count": 34,
        "attitudes_count": 56789,
    }
    base.update(overrides)
    return Post(**base)


def find_forward_button(card: dict) -> dict:
    return next(el for el in card["body"]["elements"] if el["tag"] == "button")


def test_source_is_header_and_article_title_is_first():
    post = make_post(
        uid="yiche-u61014816",
        screen_name="易车·易车原创",
        title="长城H10正式开启预售",
        text_plain="长城H10正式开启预售\n\n新车已公布预售信息",
        url="https://news.yiche.com/example.html",
    )
    card = build_post_card(post, label="产品发布")
    assert card["schema"] == "2.0"
    assert card["header"]["title"]["content"] == "易车·易车原创"
    assert card["header"]["template"] == "blue"
    title = card["body"]["elements"][0]["content"]
    assert title == "**长城H10正式开启预售**"
    meta = card["body"]["elements"][1]["content"]
    assert "易车·易车原创" not in meta
    assert "07-01 12:30" in meta
    assert "[原文](https://news.yiche.com/example.html)" in meta
    dumped = json.dumps(card, ensure_ascii=False)
    assert "产品发布" not in dumped
    assert "新车已公布预售信息" in dumped
    # 互动数、发送方式、地理位置都不展示
    assert "转发 12" not in dumped
    assert "56789" not in dumped and "5.7万" not in dumped
    assert "微博网页版" not in dumped


def test_headline_replaces_title_and_body_dedup_uses_original():
    post = make_post(
        title="全国最高直降2.90万元，传祺GS4新车近期优惠热销",
        text_plain="全国最高直降2.90万元，传祺GS4新车近期优惠热销\n\n正文内容",
        url="https://news.yiche.com/example.html",
    )
    card = build_post_card(post, label="行业观察", headline="传祺GS4 全国最高优惠2.9万")
    elements = card["body"]["elements"]
    assert elements[0]["content"] == "**传祺GS4 全国最高优惠2.9万**"
    # 正文开头的原始标题仍被去重
    texts = " ".join(str(el.get("content", "")) for el in elements)
    assert texts.count("全国最高直降2.90万元") == 0
    assert "正文内容" in texts


def test_forward_button_and_mark_forwarded():
    from src.card import mark_forwarded

    card = build_post_card(make_post(), label="市场数据")
    button = find_forward_button(card)
    assert button["text"]["content"] == "转发"
    value = button["behaviors"][0]["value"]
    assert value == {"action": "forward", "mid": "m1", "uid": "42"}

    forwarded = mark_forwarded(card)
    assert not any(el["tag"] == "button" for el in forwarded["body"]["elements"])
    assert any(
        el["tag"] == "markdown" and "已转发" in el["content"]
        for el in forwarded["body"]["elements"]
    )
    # mark_forwarded 不改原卡片（patch 失败还要留原样）
    assert any(el["tag"] == "button" for el in card["body"]["elements"])


def test_no_forward_button_when_disabled():
    card = build_post_card(make_post(), with_forward=False)
    assert not any(el["tag"] == "button" for el in card["body"]["elements"])


def test_source_header_does_not_depend_on_category():
    card = build_post_card(make_post(), label="产品发布")
    assert card["header"]["title"]["content"] == "测试博主"
    assert card["header"]["template"] == "blue"
    assert "产品发布" not in json.dumps(card, ensure_ascii=False)


def test_each_source_has_stable_color():
    from src.card import _SOURCE_TEMPLATES

    assert _SOURCE_TEMPLATES == {
        "yiche-u61014816": "blue",
        "sina-newcar-news": "red",
        "autohome-newbrand": "orange",
    }


def test_long_text_folds():
    card = build_post_card(make_post(text_plain="长" * 600))
    tags = [el["tag"] for el in card["body"]["elements"]]
    assert "collapsible_panel" in tags


def test_ai_summary_visible_and_full_text_always_collapsed():
    post = make_post(
        title="新车发布",
        text_plain="新车发布\n\n站点摘要",
        full_text="完整正文第一段。\n\n完整正文最后一段。",
    )
    card = build_post_card(
        post,
        summary="- 长城H10正式开启预售\n- 新车公布动力与续航信息",
    )
    top_markdown = [
        element["content"]
        for element in card["body"]["elements"]
        if element["tag"] == "markdown"
    ]
    assert "- 长城H10正式开启预售\n- 新车公布动力与续航信息" in top_markdown
    assert not any("**摘要**" in text for text in top_markdown)
    assert not any("完整正文第一段" in text for text in top_markdown)
    panel = next(
        element
        for element in card["body"]["elements"]
        if element["tag"] == "collapsible_panel"
        and "完整文案" in element["header"]["title"]["content"]
    )
    assert panel["expanded"] is False
    assert "完整正文第一段" in panel["elements"][0]["content"]


def test_full_text_card_stays_under_feishu_limit():
    post = make_post(
        title="长文",
        text_plain="长文",
        full_text="长" * 20_000,
    )
    card = build_post_card(post, summary="- 简短要点")
    assert len(json.dumps(card, ensure_ascii=False).encode("utf-8")) < 30_000
    assert "长文正文可能被截断" in json.dumps(card, ensure_ascii=False)


def test_full_text_card_keeps_room_for_json_escaping():
    post = make_post(
        title="长文",
        text_plain="长文",
        full_text='"\\\n' * 5_000,
    )
    card = build_post_card(post, summary="- 简短要点")
    payload = json.dumps(card, ensure_ascii=False).encode("utf-8")
    assert len(payload) < 30_000
    assert "查看原文" in payload.decode()


def test_final_card_size_guard_handles_json_control_character_escaping():
    post = make_post(
        title="长文",
        text_plain="长文",
        full_text="\x01" * 20_000 + "有效正文",
    )
    card = build_post_card(post, summary="- 简短要点")
    assert len(json.dumps(card, ensure_ascii=False).encode("utf-8")) <= 28_000


def test_summary_does_not_hide_legacy_long_text_panel():
    post = make_post(text_plain="长" * 600)
    card = build_post_card(post, summary="- 简短要点")
    panels = [
        element
        for element in card["body"]["elements"]
        if element["tag"] == "collapsible_panel"
    ]
    assert panels and panels[0]["expanded"] is False
    assert "长" * 100 in panels[0]["elements"][0]["content"]


def test_external_text_cannot_inject_feishu_at_tag():
    post = make_post(
        title="新车<at id=all></at>",
        text_plain="新车<at id=all></at>",
        full_text="正文<at id=all></at>",
    )
    card = build_post_card(post, summary="- 要点<AT id=all></AT>")
    dumped = json.dumps(card, ensure_ascii=False).lower()
    assert "<at" not in dumped and "</at" not in dumped
    assert "＜at" in dumped and "＜/at" in dumped


def test_truncated_long_text_has_original_link_note():
    card = build_post_card(make_post(text_truncated=True))
    dumped = json.dumps(card, ensure_ascii=False)
    assert "长文正文可能被截断" in dumped
    assert "https://weibo.com/42/Babc" in dumped


def test_repost_marker_and_quote():
    post = make_post(
        is_repost=True,
        retweeted_screen_name="原作者",
        retweeted_text_plain="原帖内容",
    )
    card = build_post_card(post, label="车圈热点")
    assert card["header"]["title"]["content"] == "测试博主"
    meta = card["body"]["elements"][0]["content"]
    assert "转发" in meta
    dumped = json.dumps(card, ensure_ascii=False)
    assert "@原作者" in dumped
    assert "原帖内容" in dumped


def test_image_folded_as_markdown():
    post = make_post(
        image_urls=["https://wx1.sinaimg.cn/large/x.jpg", "https://wx1.sinaimg.cn/y.jpg"],
        video=VideoInfo(title="试驾视频", duration=95),
    )
    with_key = build_post_card(post, image_key="img_v3_xxx")
    top_tags = [el["tag"] for el in with_key["body"]["elements"]]
    assert "img" not in top_tags  # img 组件在折叠面板里渲染不稳定，必须走 markdown
    panel = next(
        el
        for el in with_key["body"]["elements"]
        if el["tag"] == "collapsible_panel" and "查看图片" in el["header"]["title"]["content"]
    )
    assert panel["expanded"] is False
    assert "共 2 张" in panel["header"]["title"]["content"]
    assert panel["elements"][0] == {"tag": "markdown", "content": "![图片](img_v3_xxx)"}
    assert "1:35" in json.dumps(with_key, ensure_ascii=False)

    without_key = build_post_card(post)
    assert "未能上传" in json.dumps(without_key, ensure_ascii=False)
