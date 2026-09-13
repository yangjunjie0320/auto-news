"""关掉飞书后，抓取链路必须仍然把条目落进 digest——网站的全部输入就是它。

这是最容易在重构中被悄悄改坏的地方：digest 的写入原本挂在飞书发送成功之后
（message_id 为空就直接 return，不写 digest），关飞书必须走另一条路径。
"""

import httpx

from src.classifier import Classification
from src.config import Settings
from src.models import PushResult
from src.sender import PostPusher
from tests.test_card import make_post


class _StoreStub:
    def __init__(self) -> None:
        self.records = []

    def append(self, record) -> None:
        self.records.append(record)


def _no_translate(monkeypatch):
    async def fake_translate(title, summary, settings, client):
        return "", ""

    monkeypatch.setattr("src.sender.translate_article", fake_translate)


async def test_feishu_off_archives_without_sending(monkeypatch):
    async def fake_classify(post, settings, client):
        return Classification(label="产品发布", headline="某车型上市", summary="- 要点")

    def boom(*args, **kwargs):
        raise AssertionError("关飞书时不应该构造卡片")

    monkeypatch.setattr("src.sender.classify_post", fake_classify)
    monkeypatch.setattr("src.sender.build_post_card", boom)
    _no_translate(monkeypatch)

    store = _StoreStub()
    settings = Settings(feishu_enabled=False)
    async with httpx.AsyncClient() as client:
        pusher = PostPusher(settings, None, client, digest_store=store)
        result = await pusher.push(make_post())

    assert result == PushResult.processed()
    assert len(store.records) == 1
    assert store.records[0].title == "某车型上市"


async def test_feishu_off_still_drops_offtopic(monkeypatch):
    async def fake_classify(post, settings, client):
        return Classification(label="汽车无关")

    monkeypatch.setattr("src.sender.classify_post", fake_classify)
    _no_translate(monkeypatch)

    store = _StoreStub()
    settings = Settings(feishu_enabled=False)
    async with httpx.AsyncClient() as client:
        pusher = PostPusher(settings, None, client, digest_store=store)
        result = await pusher.push(make_post())

    assert result == PushResult.discarded()
    assert store.records == []


async def test_feishu_off_writes_translation_into_record(monkeypatch):
    async def fake_classify(post, settings, client):
        return Classification(
            label="市场数据", headline="零跑7月交付101267台", summary="- 中文要点"
        )

    async def fake_translate(title, summary, settings, client):
        return "Leapmotor July deliveries hit 101,267", "- English point"

    monkeypatch.setattr("src.sender.classify_post", fake_classify)
    monkeypatch.setattr("src.sender.translate_article", fake_translate)

    store = _StoreStub()
    settings = Settings(feishu_enabled=False)
    async with httpx.AsyncClient() as client:
        pusher = PostPusher(settings, None, client, digest_store=store)
        await pusher.push(make_post())

    record = store.records[0]
    # 中文必须原样保留：中文 RSS 和网站热度打分都依赖它
    assert record.title == "零跑7月交付101267台"
    assert record.summary == "- 中文要点"
    assert record.title_en == "Leapmotor July deliveries hit 101,267"
    assert record.summary_en == "- English point"


async def test_translation_failure_leaves_english_empty(monkeypatch):
    async def fake_classify(post, settings, client):
        return Classification(label="产品发布", headline="某车型上市", summary="- 要点")

    monkeypatch.setattr("src.sender.classify_post", fake_classify)
    _no_translate(monkeypatch)

    store = _StoreStub()
    settings = Settings(feishu_enabled=False)
    async with httpx.AsyncClient() as client:
        pusher = PostPusher(settings, None, client, digest_store=store)
        await pusher.push(make_post())

    record = store.records[0]
    assert record.title_en == ""
    assert record.summary_en == ""
    # 翻译挂掉不影响中文，中文 RSS 照常
    assert record.title == "某车型上市"
