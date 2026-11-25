import asyncio
import base64
import io
import logging
import wave

import aiohttp

from .const import GEMINI_INTERACTIONS_URL

_LOGGER = logging.getLogger(__name__)

# Statuses worth another try. 400 gets a single retry: Gemini sometimes
# rejects a valid request with "INVALID_ARGUMENT" and accepts the same one
# seconds later, while a genuinely bad key/request just fails again.
RETRY_STATUSES = frozenset({403, 429, 500, 502, 503, 504})
RETRY_400_ATTEMPTS = 1
RETRY_DELAYS = (2, 5)


def to_pcm(audio: bytes) -> bytes:
    """Return raw s16le PCM, unwrapping a WAV container if Gemini sent one.

    The preview models returned bare PCM (audio/l16); gemini-3.8 returns a
    WAV file whose trailing metadata chunk played as a burst of noise when
    the whole file was treated as PCM.
    """
    if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return audio
    with wave.open(io.BytesIO(audio)) as wav:
        return wav.readframes(wav.getnframes())


class GeminiTTSClient:

    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession,
        voice: str = "Aoede",
        model: str = "gemini-3.8-flash-tts",
    ) -> None:
        self._api_key = api_key
        self._session = session
        self._voice = voice
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    @property
    def voice(self) -> str:
        return self._voice

    async def synthesize(
        self,
        text: str,
        *,
        prompt: str | None = None,
    ) -> bytes:
        # gemini-3.8 TTS reads the text field verbatim: a style written into
        # it ("prompt: text" or a "[prompt]" tag) is often spoken aloud. The
        # Interactions API carries the style separately as speech_metadata.
        item = {"type": "text", "text": text}
        style = (prompt or "").strip()
        if style:
            item["annotations"] = [{"type": "speech_metadata", "style": style}]
        payload = {
            "model": self._model,
            "input": [{"type": "user_input", "content": [item]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": self._voice}]},
        }
        url = GEMINI_INTERACTIONS_URL
        headers = {"x-goog-api-key": self._api_key}

        attempt = 0
        while True:
            try:
                async with self._session.post(
                    url, json=payload, headers=headers
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        break
                    body = await resp.text()
                    error = RuntimeError(
                        f"Gemini TTS API returned {resp.status}: {body}"
                    )
                    retryable = resp.status in RETRY_STATUSES or (
                        resp.status == 400 and attempt < RETRY_400_ATTEMPTS
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                error = RuntimeError(f"Gemini TTS request failed: {err!r}")
                retryable = True
            if not retryable or attempt >= len(RETRY_DELAYS):
                raise error
            # Gemini intermittently answers a valid key with 403 "A valid API
            # key or GCP project is required" (and 429/5xx), then succeeds.
            _LOGGER.warning("%s; retrying in %s s", error, RETRY_DELAYS[attempt])
            await asyncio.sleep(RETRY_DELAYS[attempt])
            attempt += 1

        audio = [
            part
            for step in data.get("steps") or []
            if step.get("type") == "model_output"
            for part in step.get("content") or []
            if part.get("type") == "audio" and part.get("data")
        ]
        if not audio:
            # e.g. a safety refusal: no audio part in the output
            raise RuntimeError(
                f"No audio in Gemini TTS response: {str(data)[:300]}"
            )
        audio_b64 = audio[-1]["data"]

        return to_pcm(base64.b64decode(audio_b64))

    async def generate_music(self, prompt: str) -> bytes:
        """Generate music via Gemini Lyria 3 API. Returns MP3 bytes."""
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"lyria-3-clip-preview:generateContent?key={self._api_key}"
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
        }

        async with self._session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"Lyria API returned {resp.status}: {body}"
                )
            data = await resp.json()

        try:
            for part in data["candidates"][0]["content"]["parts"]:
                if "inlineData" in part:
                    return base64.b64decode(part["inlineData"]["data"])
        except (KeyError, IndexError):
            pass

        raise RuntimeError("No audio data in Lyria response")

    async def validate_api_key(self) -> bool:
        try:
            await self.synthesize("test")
            return True
        except RuntimeError:
            return False
