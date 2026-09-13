"""日报文档配图链路：预下载 → 占位块 → drive 上传 → patch 绑定。

图片是软依赖：下载失败的 URL 不建占位块，上传或绑定失败只留告警，
文档正文照发。上传需要应用具备 drive 文件上传权限，缺权限时整体降级为
无图文档。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging

import httpx
import lark_oapi as lark
from lark_oapi.api.docx.v1 import (
    PatchDocumentBlockRequest,
    ReplaceImageRequest,
    UpdateBlockRequest,
)
from lark_oapi.api.drive.v1 import UploadAllMediaRequest, UploadAllMediaRequestBody

from ..config import Settings
from ..image_uploader import _safe_url_for_log, download_image

logger = logging.getLogger(__name__)

_DOWNLOAD_CONCURRENCY = 4


async def download_images(
    urls: list[str],
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> dict[str, bytes]:
    """并发下载去重后的图片。失败的 URL 不出现在返回值里。"""
    semaphore = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)

    async def fetch(url: str) -> tuple[str, bytes | None]:
        async with semaphore:
            try:
                data = await download_image(
                    url, http_client, max_bytes=settings.image_max_bytes
                )
            except Exception as exc:
                logger.warning(
                    "doc image download failed: url=%s error=%s",
                    _safe_url_for_log(url),
                    type(exc).__name__,
                )
                return url, None
            return url, data

    unique = list(dict.fromkeys(urls))
    results = await asyncio.gather(*(fetch(url) for url in unique))
    return {url: data for url, data in results if data is not None}


def bind_image(client: lark.Client, document_id: str, block_id: str, data: bytes) -> bool:
    """把图片素材上传到 drive 并绑定到占位图片块。失败返回 False，不抛。"""
    try:
        upload = client.drive.v1.media.upload_all(
            UploadAllMediaRequest.builder()
            .request_body(
                UploadAllMediaRequestBody.builder()
                .file_name(f"{block_id}.jpg")
                .parent_type("docx_image")
                .parent_node(block_id)
                .size(len(data))
                .extra(json.dumps({"drive_route_token": document_id}))
                .file(io.BytesIO(data))
                .build()
            )
            .build()
        )
        if not upload.success() or not upload.data or not upload.data.file_token:
            logger.warning(
                "doc image upload failed (需要 drive 上传权限): code=%s msg=%s",
                upload.code,
                upload.msg,
            )
            return False
        patch = client.docx.v1.document_block.patch(
            PatchDocumentBlockRequest.builder()
            .document_id(document_id)
            .block_id(block_id)
            .request_body(
                UpdateBlockRequest.builder()
                .replace_image(
                    ReplaceImageRequest.builder().token(upload.data.file_token).build()
                )
                .build()
            )
            .build()
        )
        if not patch.success():
            logger.warning(
                "doc image bind failed: block=%s code=%s msg=%s",
                block_id,
                patch.code,
                patch.msg,
            )
            return False
        return True
    except Exception:
        logger.exception("doc image bind crashed: block=%s", block_id)
        return False
