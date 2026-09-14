"""单条文章的处理流水线：分类 → 翻译 → 落进 digest 归档。

归档写出的 state/digest/*.jsonl 是网站的全部输入，这条链路就是本项目的全部产出。
"""

from __future__ import annotations

import logging

import httpx

from .archive import DigestRecord, DigestStore
from .classifier import classify_post
from .config import Settings
from .models import Post, PushResult
from .translate import translate_article

logger = logging.getLogger(__name__)


class ArticlePipeline:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient,
        *,
        archive: DigestStore | None = None,
        dry_run: bool = False,
    ) -> None:
        self._settings = settings
        self._http_client = http_client
        self._archive = archive
        self._dry_run = dry_run

    async def _record(self, post: Post, result) -> DigestRecord:
        title = result.headline or post.title or post.text_plain[:60]
        summary = result.summary.strip()
        # 翻译是软依赖：失败只是没有英文，中文字段照常完整，中文 RSS 不受影响
        title_en, summary_en, figure_en = await translate_article(
            title, summary, self._settings, self._http_client
        )
        return DigestRecord(
            kind=post.kind,
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
            figure_en=figure_en,
        )

    async def process(self, post: Post) -> PushResult:
        result = await classify_post(post, self._settings, self._http_client)
        if result.should_drop(self._settings):
            # 视为已处理（落 state），不再重试
            logger.info(
                "article dropped: name=%s mid=%s label=%s china=%s url=%s",
                post.screen_name,
                post.mid,
                result.label,
                result.china,
                post.url,
            )
            return PushResult.discarded()

        if self._dry_run:
            logger.info(
                "[dry-run] would archive: name=%s mid=%s label=%s url=%s",
                post.screen_name,
                post.mid,
                result.label,
                post.url,
            )
            return PushResult.processed()

        if self._archive is not None:
            self._archive.append(await self._record(post, result))
        logger.info(
            "article archived: name=%s mid=%s label=%s url=%s",
            post.screen_name,
            post.mid,
            result.label,
            post.url,
        )
        return PushResult.sent()
