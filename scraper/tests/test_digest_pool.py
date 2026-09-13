from __future__ import annotations

import datetime as dt

import httpx

from src.config import Settings
from src.digest.pool import PoolAccount, _classify_batch, _classify_collected, _to_record
from src.weibo_client import WeiboHit


def _hit(mid: str, text: str) -> WeiboHit:
    return WeiboHit(
        mid=mid,
        bid=f"B{mid}",
        uid="42",
        screen_name="测试博主",
        followers=1000,
        verified=True,
        engagement=10,
        is_repost=False,
        text=text,
        created_at=dt.datetime(2026, 8, 3, 10, 0, tzinfo=dt.UTC),
    )


def _batch(n: int) -> list[tuple[WeiboHit, PoolAccount]]:
    account = PoolAccount(name="测试博主", uid="42")
    return [
        (_hit(str(i), f"第{i}条微博，关于某新车的续航测试结果"), account) for i in range(1, n + 1)
    ]


def _settings(**overrides) -> Settings:
    base = {"deepseek_api_key": "sk-x", "drop_offtopic": True, "drop_ads": True}
    base.update(overrides)
    return Settings(**base)


def _llm_response(items: list[dict]) -> httpx.Response:
    import json

    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"content": json.dumps({"items": items}, ensure_ascii=False)},
                    "finish_reason": "stop",
                }
            ]
        },
    )


def test_to_record_carries_full_text_and_images() -> None:
    from dataclasses import replace

    hit = replace(_hit("1", "很长的微博全文" * 20), image_urls=("https://p.sinaimg.cn/a.jpg",))
    record = _to_record(hit, PoolAccount(name="测试博主", uid="42"), "行业观察", "- 要点")
    assert record.full_text == hit.text
    assert record.image_urls == ["https://p.sinaimg.cn/a.jpg"]
    assert len(record.title) <= 60


async def test_classify_batch_maps_labels_and_drops(respx_mock):
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=_llm_response(
            [
                {"index": 1, "label": "市场数据", "china": True, "summary_points": ["交付创新高"]},
                {"index": 2, "label": "汽车无关", "china": True, "summary_points": []},
                {"index": 3, "label": "不存在的标签", "china": "x", "summary_points": []},
            ]
        )
    )
    async with httpx.AsyncClient() as client:
        results = await _classify_batch(_batch(3), _settings(), client)
    assert results is not None
    kept = [r for r in results if r is not None]
    # 第 2 条被 drop；第 3 条非法字段回落默认（行业观察 / china=true），保留
    assert len(kept) == 2
    assert kept[0].label == "市场数据"
    assert kept[0].summary == "- 交付创新高"
    assert kept[1].label == "行业观察"
    assert kept[1].summary  # 空摘要回落到原文兜底摘要


async def test_classify_batch_returns_none_on_llm_failure(respx_mock, monkeypatch):
    monkeypatch.setattr("src.digest.llm._RETRY_DELAYS", (0, 0))
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(503)
    )
    async with httpx.AsyncClient() as client:
        assert await _classify_batch(_batch(2), _settings(), client) is None


async def test_classify_collected_falls_back_to_singles(respx_mock, monkeypatch):
    monkeypatch.setattr("src.digest.llm._RETRY_DELAYS", (0, 0))
    monkeypatch.setattr("src.classifier.RETRY_DELAYS", (0, 0))
    # 批量调用失败（503），逐条调用也失败 -> 每条落兜底默认标签但仍保留
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(503)
    )
    async with httpx.AsyncClient() as client:
        records = await _classify_collected(_batch(3), _settings(), client)
    assert len(records) == 3
    assert all(r.label == "行业观察" for r in records)


async def test_classify_collected_respects_batch_size_one(respx_mock):
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"label": "市场数据", "china": true, '
                            '"summary_points": ["要点"]}'
                        }
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        records = await _classify_collected(
            _batch(2), _settings(pool_classify_batch_size=1), client
        )
    assert len(records) == 2
    assert all(r.label == "市场数据" for r in records)
