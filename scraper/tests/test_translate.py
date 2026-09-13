import httpx
import pytest

from src.config import Settings
from src.translate import (
    fix_money_units,
    parse_translation,
    points_from_summary,
    summary_from_points,
    translate_article,
)


def test_points_roundtrip():
    summary = "- 第一条\n- 第二条"
    assert points_from_summary(summary) == ["第一条", "第二条"]
    assert summary_from_points(["a", "b"]) == "- a\n- b"


def test_summary_from_points_drops_blanks():
    # 漏翻的条目被置空，拼回去时不能留下空的「- 」行
    assert summary_from_points(["a", "", "b"]) == "- a\n- b"


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("¥12,000 units sold", "12,000 units sold"),
        ("delivered ¥89,453 vehicles", "delivered 89,453 vehicles"),
        ("¥1,200 UNITS", "1,200 UNITS"),
        # 真正的金额不能被动
        ("priced at ¥380,000", "priced at ¥380,000"),
        ("from ¥89,800 for the base trim", "from ¥89,800 for the base trim"),
    ],
)
def test_fix_money_units(raw, want):
    assert fix_money_units(raw) == want


def test_parse_translation_rejects_count_mismatch():
    assert parse_translation({"title": "T", "points": ["a"]}, 2) is None
    assert parse_translation({"title": "T", "points": "a"}, 1) is None


def test_parse_translation_blanks_cjk_residue():
    title, points = parse_translation(
        {"title": "Leapmotor delivers", "points": ["fine", "享界 G9 launched"]}, 2
    )
    assert title == "Leapmotor delivers"
    # 漏翻的整条作废，宁缺毋滥
    assert points == ["fine", ""]


def test_parse_translation_fixes_money_units():
    _, points = parse_translation({"title": "T", "points": ["¥12,000 units"]}, 1)
    assert points == ["12,000 units"]


async def test_translate_article_disabled_returns_empty():
    settings = Settings(translate_enabled=False, deepseek_api_key="k")
    async with httpx.AsyncClient() as client:
        assert await translate_article("标题", "- 要点", settings, client) == ("", "")


async def test_translate_article_without_key_returns_empty():
    settings = Settings(translate_enabled=True, deepseek_api_key="")
    async with httpx.AsyncClient() as client:
        assert await translate_article("标题", "- 要点", settings, client) == ("", "")


async def test_translate_article_happy_path(monkeypatch):
    captured = {}

    async def fake_chat_json(settings, client, system, user, *, max_tokens, timeout):
        captured["system"] = system
        captured["user"] = user
        return {"title": "Leapmotor July deliveries hit 101,267", "points": ["A", "B"]}

    monkeypatch.setattr("src.translate.chat_json", fake_chat_json)
    settings = Settings(translate_enabled=True, deepseek_api_key="k")
    async with httpx.AsyncClient() as client:
        title_en, summary_en = await translate_article(
            "零跑7月交付101267台", "- 第一条\n- 第二条", settings, client
        )
    assert title_en == "Leapmotor July deliveries hit 101,267"
    assert summary_en == "- A\n- B"
    # 标题和要点一起送进同一次翻译调用
    assert "标题：零跑7月交付101267台" in captured["user"]
    assert "1. 第一条" in captured["user"]
    # ¥ 规则必须在 prompt 里，违反是静默的
    assert "¥380,000" in captured["system"]
    assert "12,000 units" in captured["system"]


async def test_translate_article_degrades_on_llm_failure(monkeypatch):
    async def fake_chat_json(*args, **kwargs):
        return None

    monkeypatch.setattr("src.translate.chat_json", fake_chat_json)
    settings = Settings(translate_enabled=True, deepseek_api_key="k")
    async with httpx.AsyncClient() as client:
        assert await translate_article("标题", "- 要点", settings, client) == ("", "")


async def test_translate_article_degrades_on_misalignment(monkeypatch):
    async def fake_chat_json(*args, **kwargs):
        return {"title": "T", "points": ["only one"]}

    monkeypatch.setattr("src.translate.chat_json", fake_chat_json)
    settings = Settings(translate_enabled=True, deepseek_api_key="k")
    async with httpx.AsyncClient() as client:
        # 两条中文要点对一条英文，整批作废
        assert await translate_article("标题", "- 一\n- 二", settings, client) == ("", "")
