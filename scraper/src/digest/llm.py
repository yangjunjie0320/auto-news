"""日报的 DeepSeek 调用封装。

和 classifier 用同一个 OpenAI 兼容端点，但日报的输出比单条分类长得多，
max_tokens 不能沿用分类那套。任何失败都返回 None，由调用方降级。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)

# 服务端过载/限流时退避重试；发布高峰期 DeepSeek 会对大请求间歇性 503
_RETRY_STATUS = {429, 500, 502, 503}
_RETRY_DELAYS = (10.0, 30.0)  # 首次失败后等 10s，再失败等 30s，共 3 次尝试


async def chat_json(
    settings: Settings,
    http_client: httpx.AsyncClient,
    system_prompt: str,
    user_content: str,
    *,
    max_tokens: int,
    timeout: float,
) -> dict[str, Any] | None:
    """要一份 JSON 对象回来。失败返回 None，不抛。"""
    if not settings.deepseek_api_key:
        logger.warning("digest llm skipped: no deepseek_api_key")
        return None
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": max_tokens,
        # v4-flash 思考默认 high，对结构化任务过度：思考与正文共享 max_tokens，
        # 放任默认档会把配额吃穿（实测 finish_reason=length）
        "reasoning_effort": "low",
    }
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            resp = await http_client.post(
                f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
            raw = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
            break
        except Exception as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            retryable = status in _RETRY_STATUS or isinstance(exc, httpx.TransportError)
            if retryable and attempt < len(_RETRY_DELAYS):
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "digest llm call failed (attempt %d, retry in %.0fs): %s",
                    attempt + 1,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
                continue
            logger.warning("digest llm call failed: %s", exc)
            return None

    if raw:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # finish_reason=length 通常是思考+输出超过 max_tokens 被截断
        logger.warning(
            "digest llm returned non-JSON output: finish_reason=%s len=%d",
            finish_reason,
            len(raw or ""),
        )
        return None
    if not isinstance(parsed, dict):
        logger.warning("digest llm returned unexpected JSON shape")
        return None
    return parsed
