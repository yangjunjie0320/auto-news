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


async def test_promo_post_skips_card_but_lands_in_digest(monkeypatch):
    async def fake_classify(post, settings, client):
        return Classification(
            label="行业观察",
            promo=True,
            headline="传祺GS4 全国最高优惠2.9万",
            summary="- 要点",
        )

    monkeypatch.setattr("src.sender.classify_post", fake_classify)
    store = _StoreStub()
    settings = Settings(promo_to_digest_only=True)
    async with httpx.AsyncClient() as client:
        pusher = PostPusher(settings, None, client, digest_store=store)
        result = await pusher.push(make_post(title="原始软文标题"))
    assert result == PushResult.processed()
    assert len(store.records) == 1
    assert store.records[0].title == "传祺GS4 全国最高优惠2.9万"


async def test_promo_hold_can_be_disabled(monkeypatch):
    async def fake_classify(post, settings, client):
        return Classification(label="行业观察", promo=True)

    sent = []

    async def fake_send(self, card_json, *, chat_id=None):
        sent.append(card_json)
        return "om_message_id"

    monkeypatch.setattr("src.sender.classify_post", fake_classify)
    monkeypatch.setattr("src.sender.CardSender.send", fake_send)
    settings = Settings(promo_to_digest_only=False)
    async with httpx.AsyncClient() as client:
        pusher = PostPusher(settings, None, client)
        result = await pusher.push(make_post())
    assert result == PushResult.sent()
    assert len(sent) == 1
