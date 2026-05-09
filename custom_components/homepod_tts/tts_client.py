import base64
import logging

import aiohttp

from .const import GEMINI_TTS_BASE_URL

_LOGGER = logging.getLogger(__name__)


class GeminiTTSClient:

    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession,
        voice: str = "Aoede",
        model: str = "gemini-2.5-flash-preview-tts",
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
        url = f"{GEMINI_TTS_BASE_URL}{self._model}:generateContent?key={self._api_key}"

        if prompt:
            full_text = f"{prompt}: {text}"
        else:
            full_text = text

        payload = {
            "contents": [{"parts": [{"text": full_text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": self._voice}
                    }
                },
            },
        }

        async with self._session.post(url, json=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"Gemini TTS API returned {resp.status}: {body}"
                )
            data = await resp.json()

        try:
            audio_b64 = data["candidates"][0]["content"]["parts"][0][
                "inlineData"
            ]["data"]
        except (KeyError, IndexError) as err:
            raise RuntimeError(
                f"Unexpected Gemini TTS response structure: {err}"
            ) from err

        return base64.b64decode(audio_b64)

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
