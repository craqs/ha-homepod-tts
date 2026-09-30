"""Retry behaviour of GeminiTTSClient.synthesize."""

import base64
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from custom_components.homepod_tts.tts_client import GeminiTTSClient

AUDIO = b"pcm"
OK_BODY = {
    "candidates": [
        {"content": {"parts": [{"inlineData": {"data": base64.b64encode(AUDIO).decode()}}]}}
    ]
}


class FakeResponse:
    def __init__(self, status, body=None):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._body

    async def text(self):
        return str(self._body)


class FakeSession:
    """Plays back a list of responses (or exceptions), one per post()."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = 0

    def post(self, url, json):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def no_sleep():
    with patch(
        "custom_components.homepod_tts.tts_client.asyncio.sleep", new=AsyncMock()
    ) as sleep:
        yield sleep


async def test_transient_403_then_success():
    session = FakeSession(
        FakeResponse(403, "A valid API key or GCP project is required."),
        FakeResponse(200, OK_BODY),
    )
    assert await GeminiTTSClient("k", session).synthesize("hi") == AUDIO
    assert session.calls == 2


async def test_network_error_is_retried():
    session = FakeSession(aiohttp.ClientConnectionError("reset"), FakeResponse(200, OK_BODY))
    assert await GeminiTTSClient("k", session).synthesize("hi") == AUDIO
    assert session.calls == 2


async def test_gives_up_after_retries(no_sleep):
    session = FakeSession(*(FakeResponse(503, "busy") for _ in range(3)))
    with pytest.raises(RuntimeError, match="returned 503"):
        await GeminiTTSClient("k", session).synthesize("hi")
    assert session.calls == 3
    assert [c.args[0] for c in no_sleep.await_args_list] == [2, 5]


async def test_400_fails_without_retry():
    session = FakeSession(FakeResponse(400, "API key not valid"))
    with pytest.raises(RuntimeError, match="returned 400"):
        await GeminiTTSClient("k", session).synthesize("hi")
    assert session.calls == 1
