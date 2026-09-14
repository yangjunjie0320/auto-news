import httpx

from src.config import Settings
from src.llm import chat_json


def _settings() -> Settings:
    return Settings(deepseek_api_key="sk-x")


def _ok(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
    )


async def _call(client: httpx.AsyncClient) -> dict | None:
    return await chat_json(
        _settings(), client, "system", "user", max_tokens=100, timeout=5.0
    )


async def test_chat_json_success(respx_mock):
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=_ok('{"points": ["a"]}')
    )
    async with httpx.AsyncClient() as client:
        assert await _call(client) == {"points": ["a"]}


async def test_chat_json_retries_on_503_then_succeeds(respx_mock, monkeypatch):
    monkeypatch.setattr("src.llm._RETRY_DELAYS", (0, 0))
    route = respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        side_effect=[httpx.Response(503), httpx.Response(503), _ok('{"k": 1}')]
    )
    async with httpx.AsyncClient() as client:
        assert await _call(client) == {"k": 1}
    assert route.call_count == 3


async def test_chat_json_gives_up_after_retries(respx_mock, monkeypatch):
    monkeypatch.setattr("src.llm._RETRY_DELAYS", (0, 0))
    route = respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(503)
    )
    async with httpx.AsyncClient() as client:
        assert await _call(client) is None
    assert route.call_count == 3


async def test_chat_json_no_retry_on_client_error(respx_mock):
    route = respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(401)
    )
    async with httpx.AsyncClient() as client:
        assert await _call(client) is None
    assert route.call_count == 1


async def test_chat_json_strips_markdown_fence(respx_mock):
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=_ok('```json\n{"k": 1}\n```')
    )
    async with httpx.AsyncClient() as client:
        assert await _call(client) == {"k": 1}


async def test_chat_json_non_json_returns_none(respx_mock):
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=_ok("截断的输出{")
    )
    async with httpx.AsyncClient() as client:
        assert await _call(client) is None
