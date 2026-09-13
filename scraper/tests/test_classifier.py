import json

import httpx

from src.classifier import (
    DEFAULT_LABEL,
    SUMMARY_MAX_CHARS,
    Classification,
    classify_post,
    format_summary_points,
    parse_result,
)
from src.config import Settings
from tests.test_card import make_post


def test_parse_result_valid():
    r = parse_result(
        '{"label": "资本市场", "china": true, '
        '"summary_points": ["车企公布最新融资安排", "新资金将用于新车研发"]}'
    )
    assert r.label == "资本市场" and r.china is True
    assert r.summary == "- 车企公布最新融资安排\n- 新资金将用于新车研发"
    assert r.promo is False and r.headline == ""


def test_parse_result_promo_and_headline():
    r = parse_result(
        '{"label": "行业观察", "china": true, "promo": true, '
        '"headline": "  传祺GS4 全国最高优惠2.9万  ", "summary_points": ["要点"]}'
    )
    assert r.promo is True
    assert r.headline == "传祺GS4 全国最高优惠2.9万"
    # 非布尔 promo、超长 headline 回落到安全值
    r = parse_result('{"label": "行业观察", "china": true, "promo": "yes", "headline": 3}')
    assert r.promo is False and r.headline == ""
    r = parse_result('{"label": "行业观察", "china": true, "headline": "' + "长" * 50 + '"}')
    assert len(r.headline) == 30
    legacy = parse_result(
        '{"label": "行业观察", "china": true, "summary": "  这是  旧摘要。  "}'
    )
    assert legacy.summary == "- 这是 旧摘要。"
    r = parse_result('{"label": "汽车无关", "china": false}')
    assert r.label == "汽车无关" and r.china is False


def test_parse_result_falls_back():
    # 宁可放过：任何异常输出都回落到可见默认值
    assert parse_result("not json") == Classification()
    assert parse_result('{"label": "别的", "china": "maybe"}') == Classification()
    assert parse_result("[]") == Classification()
    assert parse_result('{"label": "广告"}').china is True
    assert parse_result('{"label": "产品发布", "china": true, "summary": 3}').summary == ""
    long_summary = parse_result(
        '{"label": "产品发布", "china": true, "summary": "'
        + "长" * 300
        + '"}'
    ).summary
    assert long_summary.startswith("- ")
    assert len(long_summary) <= SUMMARY_MAX_CHARS


def test_summary_points_are_cleaned_deduplicated_and_limited():
    summary = format_summary_points(
        [
            "1. 长城H10正式开启预售",
            "- 长城H10正式开启预售",
            "• 新车公布了动力和续航信息",
            "3、新车计划于今年三季度交付",
            "4. 这条不应被保留",
        ]
    )
    assert summary == (
        "- 长城H10正式开启预售\n"
        "- 新车公布了动力和续航信息\n"
        "- 新车计划于今年三季度交付"
    )


def test_should_drop_rules():
    settings = Settings(drop_offtopic=True, drop_ads=True, drop_non_china=True)
    assert Classification(label="汽车无关").should_drop(settings)
    assert Classification(label="广告").should_drop(settings)
    assert Classification(label="产品发布", china=False).should_drop(settings)
    assert not Classification(label="产品发布", china=True).should_drop(settings)

    lenient = Settings(drop_offtopic=False, drop_ads=False, drop_non_china=False)
    assert not Classification(label="汽车无关", china=False).should_drop(lenient)
    assert not Classification(label="广告").should_drop(lenient)


async def test_classify_disabled_returns_default():
    settings = Settings(classification_enabled=False, deepseek_api_key="sk-x")
    result = await classify_post(make_post(), settings, httpx.AsyncClient())
    assert result.label == DEFAULT_LABEL and result.china is True
    assert result.summary == "- 今天试驾了一台新车"


def test_fallback_summary_strips_lede_and_keeps_whole_sentences():
    from src.classifier import fallback_summary

    post = make_post(
        title="零跑A05将于8月10日正式上市",
        text_plain="零跑A05将于8月10日正式上市",
        full_text=(
            "易车讯日前，我们从官方获悉，零跑A05将于8月10日正式上市。"
            "新车现已开启盲订，99元享5000专属能量积分权益。"
            "外观方面，新车采用悬浮车顶，搭配宽体尾部，车顶分色设计拉长视觉比例，"
            "三根横向尾线拓宽尾部视觉宽度，搭配家族化三段式微笑前后灯组。"
        ),
    )
    summary = fallback_summary(post)
    # 套话被剥掉、只保留完整短句、放不下的长句整句跳过，而不是句中截断
    assert summary == (
        "- 零跑A05将于8月10日正式上市。\n- 新车现已开启盲订，99元享5000专属能量积分权益。"
    )
    assert "…" not in summary and "易车讯" not in summary


async def test_classify_api_error_returns_default(respx_mock, monkeypatch):
    monkeypatch.setattr("src.classifier.RETRY_DELAYS", (0, 0))
    route = respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(500)
    )
    settings = Settings(classification_enabled=True, deepseek_api_key="sk-x")
    async with httpx.AsyncClient() as client:
        result = await classify_post(make_post(), settings, client)
    assert result == Classification(summary="- 今天试驾了一台新车")
    assert route.call_count == 3  # 过载类错误退避重试，共 3 次尝试


async def test_classify_retries_once_then_succeeds(respx_mock, monkeypatch):
    monkeypatch.setattr("src.classifier.RETRY_DELAYS", (0, 0))
    ok = httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": '{"label": "市场数据", "china": true, '
                        '"summary_points": ["销量同比增长"]}'
                    }
                }
            ]
        },
    )
    route = respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        side_effect=[httpx.Response(503), ok]
    )
    settings = Settings(classification_enabled=True, deepseek_api_key="sk-x")
    async with httpx.AsyncClient() as client:
        result = await classify_post(make_post(), settings, client)
    assert result.label == "市场数据"
    assert route.call_count == 2


async def test_classify_success(respx_mock):
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"label": "市场数据", "china": true, '
                            '"summary_points": ["销量同比增长，刷新纪录。", '
                            '"新车交付量创历史新高。"]}'
                        }
                    }
                ]
            },
        )
    )
    settings = Settings(classification_enabled=True, deepseek_api_key="sk-x")
    async with httpx.AsyncClient() as client:
        result = await classify_post(make_post(), settings, client)
    assert result.label == "市场数据"
    assert result.summary == "- 销量同比增长，刷新纪录。\n- 新车交付量创历史新高。"
    request = respx_mock.calls.last.request
    payload = json.loads(request.content)
    assert payload["model"] == settings.deepseek_model
    assert payload["max_tokens"] == 4000


async def test_classify_reads_full_text_instead_of_list_summary(respx_mock):
    route = respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"label": "产品发布", "china": true, '
                            '"summary_points": ["全文提到新车续航为700公里。"]}'
                        }
                    }
                ]
            },
        )
    )
    post = make_post(
        title="长城H10正式开启预售",
        text_plain="长城H10正式开启预售\n\n列表页短摘要",
        full_text="详情页正文独有首段。新车续航为700公里。详情页正文独有尾段。",
    )
    settings = Settings(classification_enabled=True, deepseek_api_key="sk-x")
    async with httpx.AsyncClient() as client:
        await classify_post(post, settings, client)

    payload = json.loads(route.calls.last.request.content)
    user_text = payload["messages"][1]["content"]
    assert "详情页正文独有首段" in user_text
    assert "详情页正文独有尾段" in user_text
    assert "列表页短摘要" not in user_text
