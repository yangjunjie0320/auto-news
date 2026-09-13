from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from src.config import Settings
from src.digest.runner import DigestScheduler, build_digest
from src.digest.store import DigestRecord, DigestStore


@pytest.fixture
def scheduler(tmp_path):
    settings = Settings(
        chat_id="oc_test",
        digest_state_file=str(tmp_path / "digest.json"),
        digest_outbox_file=str(tmp_path / "outbox.json"),
    )
    return DigestScheduler(settings, None, None)


class FlakySender:
    """前 fail_first 次发送失败，之后成功。"""

    def __init__(self, fail_first: int) -> None:
        self.fail_first = fail_first
        self.sent: list[str] = []

    async def send(self, card_json: str) -> str | None:
        if self.fail_first > 0:
            self.fail_first -= 1
            return None
        self.sent.append(card_json)
        return f"om_{len(self.sent)}"


def _cards(n: int) -> list[dict]:
    return [{"header": {"title": {"content": f"卡{i}"}}} for i in range(1, n + 1)]


def test_next_run_rolls_to_tomorrow_after_send_time(scheduler) -> None:
    cn = dt.timezone(dt.timedelta(hours=8))
    before = dt.datetime(2026, 8, 1, 6, 0, tzinfo=cn)
    after = dt.datetime(2026, 8, 1, 9, 0, tzinfo=cn)
    assert scheduler._next_run(before) == dt.datetime(2026, 8, 1, 8, 0, tzinfo=cn)
    assert scheduler._next_run(after) == dt.datetime(2026, 8, 2, 8, 0, tzinfo=cn)


async def test_partial_failure_resumes_without_duplicates(scheduler) -> None:
    day = dt.date(2026, 8, 1)
    cards = _cards(3)
    scheduler._save_outbox(day, cards, 0)

    # 第一轮：第 1 张成功后第 2 张失败（重试在 CardSender 内部耗尽后返回 None）
    class OneThenFail(FlakySender):
        async def send(self, card_json: str) -> str | None:
            if len(self.sent) >= 1:
                return None
            return await super().send(card_json)

    first = OneThenFail(fail_first=0)
    scheduler._sender = first
    await scheduler.run_once(day)
    assert len(first.sent) == 1
    assert scheduler._last_sent() == ""  # 未发完，不标记

    # 第二轮：从第 2 张续发，且不重建卡片
    second = FlakySender(fail_first=0)
    scheduler._sender = second
    await scheduler.run_once(day)
    assert [json.loads(c)["header"]["title"]["content"] for c in second.sent] == ["卡2", "卡3"]
    assert scheduler._last_sent() == day.isoformat()

    # 第三轮：已完成，直接跳过
    third = FlakySender(fail_first=0)
    scheduler._sender = third
    await scheduler.run_once(day)
    assert third.sent == []


async def test_outbox_for_other_day_is_ignored(scheduler) -> None:
    scheduler._save_outbox(dt.date(2026, 7, 31), _cards(2), 1)
    assert scheduler._load_outbox(dt.date(2026, 8, 1)) is None


async def test_build_digest_produces_cards_when_llm_down(respx_mock, tmp_path) -> None:
    """LLM 全挂：聚类退化一文一事件、精修与导语跳过，详讯卡照出。"""
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(400)
    )
    day = dt.date(2026, 8, 1)
    settings = Settings(
        deepseek_api_key="sk-x",
        digest_dir=str(tmp_path),
        digest_pool_enabled=False,
        digest_weibo_enabled=False,
    )
    DigestStore(tmp_path).append(
        DigestRecord(
            mid="w1",
            source="易车",
            title="这是一条足够长的网站文章标题",
            summary="- 要点一",
            label="市场数据",
            url="https://example.com/w1",
            created_at=dt.datetime(2026, 8, 1, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))),
        )
    )
    async with httpx.AsyncClient() as client:
        result = await build_digest(day, settings, client)
    assert result.events and all(e.brief is None for e in result.events)
    assert result.cards
    assert any(c["header"]["title"]["content"].endswith("详讯") for c in result.cards)
