"""归档是本项目唯一的产出：文章落不进 state/digest/*.jsonl，网站就没有输入。"""

import httpx

from src.classifier import Classification
from src.config import Settings
from src.models import PushResult
from src.pipeline import ArticlePipeline
from tests.conftest import make_post


class _ArchiveStub:
    def __init__(self) -> None:
        self.records = []

    def append(self, record) -> None:
        self.records.append(record)


def _no_translate(monkeypatch):
    async def fake_translate(title, summary, settings, client):
        return "", ""

    monkeypatch.setattr("src.pipeline.translate_article", fake_translate)


async def test_article_is_archived(monkeypatch):
    async def fake_classify(post, settings, client):
        return Classification(label="产品发布", headline="某车型上市", summary="- 要点")

    monkeypatch.setattr("src.pipeline.classify_post", fake_classify)
    _no_translate(monkeypatch)

    archive = _ArchiveStub()
    async with httpx.AsyncClient() as client:
        pipeline = ArticlePipeline(Settings(), client, archive=archive)
        result = await pipeline.process(make_post())

    assert result == PushResult.sent()
    assert len(archive.records) == 1
    assert archive.records[0].title == "某车型上市"


async def test_offtopic_is_dropped_before_archiving(monkeypatch):
    async def fake_classify(post, settings, client):
        return Classification(label="汽车无关")

    monkeypatch.setattr("src.pipeline.classify_post", fake_classify)
    _no_translate(monkeypatch)

    archive = _ArchiveStub()
    async with httpx.AsyncClient() as client:
        pipeline = ArticlePipeline(Settings(), client, archive=archive)
        result = await pipeline.process(make_post())

    assert result == PushResult.discarded()
    assert archive.records == []


async def test_dry_run_archives_nothing(monkeypatch):
    async def fake_classify(post, settings, client):
        return Classification(label="产品发布", headline="某车型上市", summary="- 要点")

    def boom(*args, **kwargs):
        raise AssertionError("dry-run 不应该翻译，那是要花钱的调用")

    monkeypatch.setattr("src.pipeline.classify_post", fake_classify)
    monkeypatch.setattr("src.pipeline.translate_article", boom)

    archive = _ArchiveStub()
    async with httpx.AsyncClient() as client:
        pipeline = ArticlePipeline(Settings(), client, archive=archive, dry_run=True)
        result = await pipeline.process(make_post())

    assert result == PushResult.processed()
    assert archive.records == []


async def test_translation_lands_beside_chinese(monkeypatch):
    async def fake_classify(post, settings, client):
        return Classification(
            label="市场数据", headline="零跑7月交付101267台", summary="- 中文要点"
        )

    async def fake_translate(title, summary, settings, client):
        return "Leapmotor July deliveries hit 101,267", "- English point"

    monkeypatch.setattr("src.pipeline.classify_post", fake_classify)
    monkeypatch.setattr("src.pipeline.translate_article", fake_translate)

    archive = _ArchiveStub()
    async with httpx.AsyncClient() as client:
        pipeline = ArticlePipeline(Settings(), client, archive=archive)
        await pipeline.process(make_post())

    record = archive.records[0]
    # 中文必须原样保留：中文 RSS 和网站的热度打分都依赖它
    assert record.title == "零跑7月交付101267台"
    assert record.summary == "- 中文要点"
    assert record.title_en == "Leapmotor July deliveries hit 101,267"
    assert record.summary_en == "- English point"


async def test_translation_failure_leaves_chinese_intact(monkeypatch):
    async def fake_classify(post, settings, client):
        return Classification(label="产品发布", headline="某车型上市", summary="- 要点")

    monkeypatch.setattr("src.pipeline.classify_post", fake_classify)
    _no_translate(monkeypatch)

    archive = _ArchiveStub()
    async with httpx.AsyncClient() as client:
        pipeline = ArticlePipeline(Settings(), client, archive=archive)
        await pipeline.process(make_post())

    record = archive.records[0]
    assert record.title_en == ""
    assert record.summary_en == ""
    # 翻译挂掉不影响中文，中文 RSS 照常
    assert record.title == "某车型上市"
