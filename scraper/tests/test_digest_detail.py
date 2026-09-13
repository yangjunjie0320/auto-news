from __future__ import annotations

import datetime as dt
import json

import httpx

from src.config import Settings
from src.digest.detail import (
    _parse_detail,
    _parse_translation,
    _render_material,
    refine_features,
)
from src.digest.events import Event
from src.digest.store import DigestRecord


def _settings(**overrides) -> Settings:
    return Settings(deepseek_api_key="sk-x", **overrides)


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


def _ok(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"content": json.dumps(payload, ensure_ascii=False)},
                    "finish_reason": "stop",
                }
            ]
        },
    )


def test_parse_detail_title_and_points() -> None:
    title, points = _parse_detail(
        {
            "title": "零跑7月交付101,267辆" + "长" * 40,
            "points": ["中文要点一", "", 42, "中文要点二"],
        }
    )
    assert len(title) == 30
    assert points == ["中文要点一", "中文要点二"]


def test_parse_translation_validates_count_and_cjk() -> None:
    # 条数不符 → 整体作废
    assert _parse_translation({"points": ["one"]}, expected=2) is None
    assert _parse_translation({"points": "oops"}, expected=1) is None
    # 混入汉字的条目单独作废（漏翻的专有名词宁缺毋滥），其余保留
    result = _parse_translation(
        {"points": ["The G9预售 exceeded 10,300 orders", "Clean English point"]},
        expected=2,
    )
    assert result == ["", "Clean English point"]


def test_render_material_uses_full_text_and_weibo() -> None:
    event = Event(
        title="零跑交付破10万",
        label="市场数据",
        records=[
            _record("w1", full_text="第一段正文。\n\n第二段正文。"),
            _record("m1", kind="weibo", full_text="博主观点全文"),
        ],
    )
    material = _render_material(event, [], 4000)
    assert "第一段正文。\n第二段正文。" in material  # 段落结构保留（空行压掉）
    assert "博主观点全文" in material


def test_render_material_roundup_uses_points_not_full_text() -> None:
    event = Event(
        title="7月交付盘点",
        label="市场数据",
        records=[_record("w1", full_text="不该出现的原文")],
        kind="roundup",
        points=["零跑 101,267（首破10万）"],
    )
    material = _render_material(event, [], 4000)
    assert "零跑 101,267" in material
    assert "不该出现的原文" not in material


async def test_refine_features_two_step_writes_brief(respx_mock) -> None:
    """第一次调用出标题+中文要点，第二次调用出英文对照，逐条配对。"""
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        side_effect=[
            _ok({"title": "精修标题", "points": ["中文一", "中文二"]}),
            _ok({"points": ["English one", "English two"]}),
        ]
    )
    feature = Event(title="详讯事件", label="市场数据", records=[_record("w1")])
    short = Event(title="短讯事件", label="行业观察", records=[_record("m1", kind="weibo")])
    async with httpx.AsyncClient() as client:
        await refine_features([feature, short], {}, _settings(), client)
    assert feature.brief is not None
    assert feature.brief.title == "精修标题"
    assert feature.brief.pairs == [("中文一", "English one"), ("中文二", "English two")]
    assert short.brief is None  # 短讯不精修


async def test_refine_features_keeps_zh_when_translation_fails(respx_mock) -> None:
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        side_effect=[
            _ok({"title": "精修标题", "points": ["中文一"]}),
            httpx.Response(400),
        ]
    )
    event = Event(title="详讯事件", label="市场数据", records=[_record("w1")])
    async with httpx.AsyncClient() as client:
        await refine_features([event], {}, _settings(), client)
    assert event.brief is not None
    assert event.brief.pairs == [("中文一", "")]


async def test_refine_features_degrades_on_llm_failure(respx_mock) -> None:
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(400)
    )
    event = Event(title="详讯事件", label="市场数据", records=[_record("w1")])
    async with httpx.AsyncClient() as client:
        await refine_features([event], {}, _settings(), client)
    assert event.brief is None
    assert "素材标题w1" in event.detail_material  # 失败也记素材，原文栏照常可用


async def test_refine_features_respects_disable_switch() -> None:
    event = Event(title="详讯事件", label="市场数据", records=[_record("w1")])
    async with httpx.AsyncClient() as client:
        await refine_features([event], {}, _settings(digest_detail_enabled=False), client)
    assert event.brief is None
