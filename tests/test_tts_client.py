"""Retry behaviour of GeminiTTSClient.synthesize."""

import base64
import io
import wave
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from custom_components.homepod_tts.tts_client import (
    GeminiTTSClient,
    to_pcm,
)

AUDIO = b"pcm"
OK_BODY = {
    "steps": [
        {"type": "user_input"},
        {
            "type": "model_output",
            "content": [{"type": "audio", "data": base64.b64encode(AUDIO).decode()}],
        },
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
        self.payloads = []

    def post(self, url, json, headers=None):
        self.calls += 1
        self.payloads.append(json)
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


async def test_transient_400_then_success():
    session = FakeSession(
        FakeResponse(400, "Request contains an invalid argument."),
        FakeResponse(200, OK_BODY),
    )
    assert await GeminiTTSClient("k", session).synthesize("hi") == AUDIO
    assert session.calls == 2


async def test_400_is_retried_only_once():
    session = FakeSession(*(FakeResponse(400, "API key not valid") for _ in range(2)))
    with pytest.raises(RuntimeError, match="returned 400"):
        await GeminiTTSClient("k", session).synthesize("hi")
    assert session.calls == 2


async def test_style_goes_in_speech_metadata_not_in_text():
    session = FakeSession(FakeResponse(200, OK_BODY), FakeResponse(200, OK_BODY))
    client = GeminiTTSClient("k", session)
    assert await client.synthesize("Dzień dobry", prompt="Powiedz to z radością") == AUDIO
    await client.synthesize("Hi", prompt="  ")
    styled, plain = (p["input"][0]["content"][0] for p in session.payloads)
    assert styled["text"] == "Dzień dobry"
    assert styled["annotations"] == [
        {"type": "speech_metadata", "style": "Powiedz to z radością"}
    ]
    assert plain == {"type": "text", "text": "Hi"}


async def test_no_audio_part_raises():
    session = FakeSession(FakeResponse(200, {"steps": [{"type": "model_output", "content": []}]}))
    with pytest.raises(RuntimeError, match="No audio"):
        await GeminiTTSClient("k", session).synthesize("hi")


def test_wav_response_is_unwrapped_to_pcm():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x01\x00\x02\x00")
    # trailing metadata chunk, like gemini-3.8's provenance tag
    data = buf.getvalue() + b"LIST\x08\x00\x00\x00metadata"
    assert to_pcm(data) == b"\x01\x00\x02\x00"
    assert to_pcm(b"raw-pcm") == b"raw-pcm"
