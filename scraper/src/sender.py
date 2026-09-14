from __future__ import annotations

import asyncio
import json
import logging

import httpx
import lark_oapi as lark
import tenacity
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

from .card import build_post_card
from .card_store import CardStore
from .classifier import classify_post
from .config import Settings
from .digest.store import DigestRecord, DigestStore
from .image_uploader import upload_image
from .models import Post, PushResult
from .translate import translate_article

logger = logging.getLogger(__name__)


class SendError(Exception):
    pass


class CardSender:
    def __init__(self, settings: Settings, client: lark.Client | None) -> None:
        self._settings = settings
        self._client = client

    async def send(self, card_json: str, *, chat_id: str | None = None) -> str | None:
        """发送卡片，成功返回 message_id，失败返回 None。默认发到主群。"""

        @tenacity.retry(
            stop=tenacity.stop_after_attempt(self._settings.send_retry_attempts),
            wait=tenacity.wait_exponential(multiplier=1, min=1, max=9),
            reraise=True,
        )
        async def _attempt() -> str:
            return await self._create(card_json, chat_id or self._settings.chat_id)

        try:
            return await _attempt()
        except Exception as e:
            logger.critical("card send exhausted all retries: error=%s", e)
            return None

    async def _create(self, card_json: str, chat_id: str) -> str:
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(card_json)
                .build()
            )
            .build()
        )
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: self._client.im.v1.message.create(request)
        )
        if not response.success():
            raise SendError(f"create failed: code={response.code} msg={response.msg}")
        return response.data.message_id or ""


class PostPusher:
    """单条新帖的处理流水线：分类 → 归档进 digest →（飞书开着时）组卡片发送。

    关掉飞书时只走到归档为止，digest 就是网站的全部输入。
    """

    def __init__(
        self,
        settings: Settings,
        lark_client: lark.Client | None,
        http_client: httpx.AsyncClient,
        *,
        card_store: CardStore | None = None,
        digest_store: DigestStore | None = None,
        dry_run: bool = False,
    ) -> None:
        self._settings = settings
        self._lark_client = lark_client
        self._http_client = http_client
        # CardSender 构造是惰性的（只在真正发送时才用到 lark 客户端），
        # 关飞书由 push 里的 feishu_enabled 分支拦住，不在这里判空
        self._sender = CardSender(settings, lark_client)
        self._card_store = card_store
        self._digest_store = digest_store
        self._dry_run = dry_run

    async def _archive(self, post: Post, result) -> None:
        """落 digest 供网站消费。

        三个调用点各自保留，不要合并成「分类后统一归档一次」：
        最后那个故意放在飞书发送成功之后，因为 append 不去重、build-data.py 的
        load_rows 也不按 mid 去重，发送失败重试会写出重复行、网站上出现重复条目。
        要合并得先让 append 幂等。
        """
        if self._digest_store is not None and not self._dry_run:
            self._digest_store.append(await self._digest_record(post, result))

    async def _digest_record(self, post: Post, result) -> DigestRecord:
        title = result.headline or post.title or post.text_plain[:60]
        summary = result.summary.strip()
        # 翻译是软依赖：失败只是没有英文，中文字段照常完整
        title_en, summary_en = await translate_article(
            title, summary, self._settings, self._http_client
        )
        return DigestRecord(
            mid=post.mid,
            source=post.screen_name,
            title=title,
            summary=summary,
            label=result.label,
            url=post.url,
            created_at=post.created_at,
            full_text=post.full_text or post.text_plain,
            image_urls=list(post.image_urls),
            title_en=title_en,
            summary_en=summary_en,
        )

    async def push(self, post: Post) -> PushResult:
        result = await classify_post(post, self._settings, self._http_client)
        if result.should_drop(self._settings):
            # 视为已处理（返回 True 落 state），不再重试
            logger.info(
                "post dropped: name=%s mid=%s label=%s china=%s url=%s",
                post.screen_name,
                post.mid,
                result.label,
                result.china,
                post.url,
            )
            return PushResult.discarded()

        # 软文/通稿不推实时卡片，仍进日报池由日报聚合决定去留
        if result.promo and self._settings.promo_to_digest_only:
            logger.info(
                "post held from cards (promo): name=%s mid=%s label=%s url=%s",
                post.screen_name,
                post.mid,
                result.label,
                post.url,
            )
            await self._archive(post, result)
            return PushResult.processed()

        # 关掉飞书时链路到此为止：只落 digest 供网站消费，不组卡片不发送。
        # 注意不要挪到 promo 分支之前——promo 的判定与日志仍然有意义。
        if not self._settings.feishu_enabled:
            await self._archive(post, result)
            logger.info(
                "post archived (feishu off): name=%s mid=%s label=%s url=%s",
                post.screen_name,
                post.mid,
                result.label,
                post.url,
            )
            return PushResult.processed()

        image_key = None
        if post.image_urls and not self._dry_run:
            image_key = await upload_image(
                post.image_urls[0],
                self._lark_client,
                self._http_client,
                max_bytes=self._settings.image_max_bytes,
            )
        card = build_post_card(
            post,
            image_key,
            result.label,
            summary=result.summary,
            headline=result.headline,
            with_forward=self._settings.forward_enabled,
        )

        if self._dry_run:
            logger.info(
                "[dry-run] would push: name=%s mid=%s label=%s url=%s text=%s",
                post.screen_name,
                post.mid,
                result.label,
                post.url,
                post.text_plain[:80].replace("\n", " "),
            )
            return PushResult.processed()

        message_id = await self._sender.send(json.dumps(card, ensure_ascii=False))
        if message_id is None:
            return PushResult.failed()
        logger.info("post pushed: name=%s mid=%s url=%s", post.screen_name, post.mid, post.url)
        if self._card_store is not None and message_id:
            self._card_store.put(
                message_id,
                {
                    "card": card,
                    "mid": post.mid,
                    "uid": post.uid,
                    "screen_name": post.screen_name,
                    "label": result.label,
                    "summary": result.summary.strip(),
                    "url": post.url,
                    "post_created_at": post.created_at.isoformat(timespec="seconds"),
                },
            )
        await self._archive(post, result)
        return PushResult.sent()
