from __future__ import annotations

import datetime as dt

from src.digest.doc import DocLine, _to_block, doc_outline
from src.digest.events import Brief, Event
from src.digest.result import DigestResult
from src.digest.store import DigestRecord


def _record(mid: str, *, kind: str = "web", **overrides) -> DigestRecord:
    base = {
        "kind": kind,
        "mid": mid,
        "source": "易车·易车原创" if kind == "web" else "@42号车库",
        "title": f"这是一条足够长的{kind}内容标题{mid}",
        "summary": "- 要点一\n- 要点二",
        "label": "市场数据",
        "url": f"https://example.com/{mid}",
        "created_at": dt.datetime(2026, 8, 1, 2, 0, tzinfo=dt.UTC),
    }
    base.update(overrides)
    return DigestRecord(**base)


def _result() -> DigestResult:
    event = Event(
        title="零跑7月交付破10万",
        label="市场数据",
        records=[
            _record("w1", full_text="第一段正文。\n第二段正文。", image_urls=["https://p.sinaimg.cn/a.jpg"]),
            _record("m1", kind="weibo", full_text="博主的完整观点内容，比标题长一些。"),
        ],
        brief=Brief(
            title="零跑7月交付101,267辆",
            pairs=[("中文要点一", "English point one"), ("中文要点二", "")],
        ),
    )
    return DigestResult(
        day=dt.date(2026, 8, 1),
        events=[event],
        intro="1. 第一句\n2. 第二句",
        strays=[_record("m2", kind="weibo"), _record("m3", kind="weibo", title="短")],
    )


def _section(lines: list[DocLine], title: str) -> list[DocLine]:
    """取某个 h4 小节到下一个标题之间的行。"""
    collected: list[DocLine] = []
    inside = False
    for line in lines:
        if line.kind in ("h3", "h4"):
            inside = line.kind == "h4" and line.runs and line.runs[0][0] == title
            continue
        if inside:
            collected.append(line)
    return collected


def test_feature_layout_title_and_zh_expanded_rest_folded() -> None:
    lines = doc_outline(_result())
    h3 = lines[0]
    assert h3.kind == "h3"
    assert h3.runs[0] == ("零跑7月交付101,267辆", "https://example.com/w1")
    assert not h3.folded  # 标题+中文缩写默认展开

    zh = [line.runs[0][0] for line in lines[1:3]]
    assert zh == ["中文要点一", "中文要点二"]

    h4 = {line.runs[0][0]: line for line in lines if line.kind == "h4"}
    assert set(h4) == {"English", "原文"}  # 未提供 available_images 时无图片节
    assert all(line.folded for line in h4.values())

    english = _section(lines, "English")
    assert [line.runs[0][0] for line in english] == ["English point one"]  # en 空串跳过

    original = _section(lines, "原文")
    texts = [line for line in original if line.kind == "text"]
    assert [line.runs[0][0] for line in texts] == ["第一段正文。", "第二段正文。"]
    quotes = [line for line in original if line.kind == "quote"]
    assert quotes and "博主的完整观点内容" in quotes[0].runs[1][0]


def test_original_section_renders_detail_material_verbatim() -> None:
    """精修跑过时，「原文」栏 = 喂给 LLM 的素材，逐行一致。"""
    result = _result()
    result.events[0].detail_material = (
        "事件标题：零跑7月交付破10万\n主源（易车）《标题》：\n第一段。\n第二段。\n"
        "相关微博：\n- 博主长文" + "长" * 3000
    )
    lines = doc_outline(result)
    original = _section(lines, "原文")
    texts = [line.runs[0][0] for line in original if line.kind == "text"]
    assert texts[0] == "事件标题：零跑7月交付破10万"
    assert "第一段。" in texts and "第二段。" in texts
    assert not [line for line in original if line.kind == "quote"]  # 素材分支不再拼引用块
    assert all(len(t) <= 2000 for t in texts)  # 超长行被切块
    assert sum(len(t) for t in texts) >= 3000  # 长文没有被截掉


def test_image_section_only_for_available_images() -> None:
    result = _result()
    lines = doc_outline(
        result,
        available_images={"https://p.sinaimg.cn/a.jpg"},
        max_images_per_event=4,
    )
    images = [line for line in lines if line.kind == "image"]
    assert len(images) == 1
    assert images[0].image_url == "https://p.sinaimg.cn/a.jpg"
    h4_titles = [line.runs[0][0] for line in lines if line.kind == "h4"]
    assert "图片" in h4_titles

    # 下载全部失败时整个图片节不输出
    lines = doc_outline(result, available_images=set(), max_images_per_event=4)
    assert not [line for line in lines if line.kind == "image"]
    assert "图片" not in [line.runs[0][0] for line in lines if line.kind == "h4"]


def test_shorts_section_collects_single_weibo_events_and_strays() -> None:
    result = _result()
    result.events.append(
        Event(title="单微博事件", label="行业观察", records=[_record("m4", kind="weibo")])
    )
    lines = doc_outline(result)
    headers = [line.runs[0][0] for line in lines if line.kind == "h3"]
    assert headers[-1] == "短讯"
    shorts_h3 = next(line for line in lines if line.kind == "h3" and line.runs[0][0] == "短讯")
    assert shorts_h3.folded
    tail = lines[lines.index(shorts_h3) + 1 :]
    bullet_sources = [line.runs[0][0] for line in tail if line.kind == "bullet"]
    assert len(bullet_sources) == 2  # 单微博事件 + 1 条有内容的散装（过短碎片被过滤）


def test_doc_outline_orders_features_by_heat() -> None:
    result = _result()
    hot = Event(
        title="某新车上市",
        label="产品发布",
        records=[
            _record("w2", label="产品发布"),
            _record("w3", label="产品发布"),
            _record("w4", label="产品发布"),
        ],
    )
    result.events.append(hot)
    lines = doc_outline(result)
    headers = [line.runs[0][0] for line in lines if line.kind == "h3"]
    assert headers == ["某新车上市", "零跑7月交付101,267辆", "短讯"]


def test_to_block_builds_linked_text_run() -> None:
    block = _to_block(DocLine(kind="h3", runs=[("标题", "https://example.com")]))
    assert block.block_type == 5
    run = block.heading3.elements[0].text_run
    assert run.content == "标题"
    assert run.text_element_style.link.url == "https://example.com"

    plain = _to_block(DocLine(kind="text", runs=[("正文", None)]))
    assert plain.block_type == 2
    assert plain.text.elements[0].text_run.content == "正文"

    folded = _to_block(DocLine(kind="h4", runs=[("English", None)], folded=True))
    assert folded.block_type == 6
    assert folded.heading4.style.folded is True

    grey = _to_block(DocLine(kind="text", runs=[("元信息", None)], grey=True))
    assert grey.text.elements[0].text_run.text_element_style.text_color == 7

    image = _to_block(DocLine(kind="image", image_url="https://p.sinaimg.cn/a.jpg"))
    assert image.block_type == 27
