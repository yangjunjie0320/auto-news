from __future__ import annotations

import asyncio
import io
import logging
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

logger = logging.getLogger(__name__)

# 文章图床（bitautoimg / autoimg / 新浪封面）实测：不带 Referer 均可下载；
# 反而带 weibo Referer 会被易车图床拒连、被汽车之家图床 403 防盗链。故默认只发 UA。
# 例外：微博配图图床 wx*.sinaimg.cn 防盗链要求 weibo Referer（2026-08-07 实测
# 不带是 403，带 https://weibo.com/ 是 200），按域名附加。
IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
}


def _headers_for(url: str) -> dict[str, str]:
    try:
        hostname = (urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return IMAGE_HEADERS
    if hostname == "sinaimg.cn" or hostname.endswith(".sinaimg.cn"):
        return {**IMAGE_HEADERS, "Referer": "https://weibo.com/"}
    return IMAGE_HEADERS

DEFAULT_IMAGE_MAX_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 3
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_ALLOWED_IMAGE_DOMAINS = (
    "sinaimg.cn",
    "bitautoimg.com",
    "bitauto.com",
    "autoimg.cn",
)


class ImageFetchError(RuntimeError):
    """The remote image failed validation or could not be downloaded safely."""


async def upload_image(
    url: str,
    lark_client: lark.Client,
    http_client: httpx.AsyncClient,
    *,
    max_bytes: int = DEFAULT_IMAGE_MAX_BYTES,
) -> str | None:
    """下载文章封面并上传飞书，返回 image_key；任何失败返回 None（不阻塞发卡）。"""
    try:
        image_bytes = await _download_image(url, http_client, max_bytes=max_bytes)
    except Exception as exc:
        logger.warning(
            "image fetch failed url=%s error=%s",
            _safe_url_for_log(url),
            _safe_error_for_log(exc),
        )
        return None

    def _upload() -> str | None:
        try:
            request = (
                CreateImageRequest.builder()
                .request_body(
                    CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(io.BytesIO(image_bytes))
                    .build()
                )
                .build()
            )
            response = lark_client.im.v1.image.create(request)
            if response.success():
                return response.data.image_key
            logger.warning("image upload failed: %s %s", response.code, response.msg)
            return None
        except Exception as exc:
            logger.warning("image upload failed url=%s: %s", _safe_url_for_log(url), exc)
            return None

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _upload)


async def download_image(
    url: str,
    http_client: httpx.AsyncClient,
    *,
    max_bytes: int = DEFAULT_IMAGE_MAX_BYTES,
) -> bytes:
    """下载并校验图片（域名白名单、HTTPS、大小上限）。供卡片与日报文档共用。"""
    return await _download_image(url, http_client, max_bytes=max_bytes)


async def _download_image(
    url: str,
    http_client: httpx.AsyncClient,
    *,
    max_bytes: int = DEFAULT_IMAGE_MAX_BYTES,
) -> bytes:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    current_url = _validate_image_url(url)
    redirects = 0
    while True:
        async with http_client.stream(
            "GET",
            current_url,
            headers=_headers_for(current_url),
            follow_redirects=False,
            timeout=15.0,
        ) as response:
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise ImageFetchError("image redirect has no Location header")
                if redirects >= MAX_REDIRECTS:
                    raise ImageFetchError(f"image exceeded {MAX_REDIRECTS} redirects")
                current_url = _validate_image_url(urljoin(str(response.url), location))
                redirects += 1
                continue

            if not 200 <= response.status_code < 300:
                raise ImageFetchError(f"image server returned HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "")
            media_type = content_type.partition(";")[0].strip().lower()
            if not media_type.startswith("image/"):
                raise ImageFetchError(f"unexpected image Content-Type: {media_type or 'missing'}")

            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = 0
                if declared_size > max_bytes:
                    raise ImageFetchError(f"image Content-Length exceeds {max_bytes} bytes")

            image = bytearray()
            async for chunk in response.aiter_bytes():
                if len(image) + len(chunk) > max_bytes:
                    raise ImageFetchError(f"image exceeds {max_bytes} bytes")
                image.extend(chunk)
            return bytes(image)


def _validate_image_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ImageFetchError("invalid image URL") from exc
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme.lower() != "https":
        raise ImageFetchError("image URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ImageFetchError("image URL must not contain credentials")
    if not any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in _ALLOWED_IMAGE_DOMAINS
    ):
        raise ImageFetchError("image URL host is not an approved source image domain")
    if port not in (None, 443):
        raise ImageFetchError("image URL must use the standard HTTPS port")
    return url


def _safe_url_for_log(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    hostname = parsed.hostname or "<unknown-host>"
    return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))


def _safe_error_for_log(exc: Exception) -> str:
    if isinstance(exc, ImageFetchError):
        return str(exc)
    if isinstance(exc, httpx.TimeoutException):
        return "request timed out"
    if isinstance(exc, httpx.HTTPError):
        return type(exc).__name__
    return type(exc).__name__
