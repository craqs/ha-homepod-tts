import asyncio
import logging

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant

from .const import (
    AIRPLAY_CHANNELS,
    AIRPLAY_SAMPLE_RATE,
    COMPRESS_PRESETS,
    GEMINI_TTS_CHANNELS,
    GEMINI_TTS_SAMPLE_RATE,
)

_LOGGER = logging.getLogger(__name__)

# 1 second of silence as raw PCM (s16le, 24kHz, mono) to prepend to TTS
# This avoids AirPlay buffering artifacts at the start of playback
_SILENCE_PCM = b"\x00" * (GEMINI_TTS_SAMPLE_RATE * GEMINI_TTS_CHANNELS * 2)

# adelay filter string for 1s silence before an audio segment
_ADELAY_1S = "adelay=1000|1000"


async def _get_audio_duration(ffmpeg_bin: str, path: str) -> float:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_bin, "-i", path, "-f", "null", "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    for line in stderr.decode(errors="replace").splitlines():
        if "Duration:" in line:
            parts = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = parts.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


async def async_generate_wav(
    hass: HomeAssistant,
    tts_pcm: bytes,
    chime_path: str | None,
    output_path: str,
    compress_preset: str = "moderate",
    offset_ms: int = 0,
    chime_volume: float = 1.0,
    music_path: str | None = None,
    music_position: str = "after",  # "before" or "after" TTS content
) -> None:
    """Build a WAV file from TTS PCM, optional chime, and optional music segment.

    Segment order:
      - With chime, music after:  [1s silence] chime  tts  music
      - With chime, music before: [1s silence] chime  music  tts
      - No chime, music after:    [1s silence] tts  music
      - No chime, music before:   [1s silence] music  tts
      - No chime, no music:       [1s silence] tts

    The 1s silence is always prepended to the very first segment to avoid
    AirPlay buffering artifacts.
    """
    ffmpeg_bin = get_ffmpeg_manager(hass).binary
    compressor = COMPRESS_PRESETS.get(compress_preset)

    tts_filters = (
        f"aresample={AIRPLAY_SAMPLE_RATE},"
        f"aformat=sample_fmts=s16:channel_layouts=stereo"
    )
    if compressor:
        tts_filters += f",{compressor}"

    # Music segment: normalize to same format
    music_filters = (
        f"aresample={AIRPLAY_SAMPLE_RATE},"
        f"aformat=sample_fmts=s16:channel_layouts=stereo"
    )

    # ------------------------------------------------------------------ #
    # Build ffmpeg command based on which segments are present             #
    # ------------------------------------------------------------------ #

    if chime_path:
        # Chime is always first → silence goes on chime via adelay
        chime_filters = (
            f"{_ADELAY_1S},"
            f"aresample={AIRPLAY_SAMPLE_RATE},"
            f"aformat=sample_fmts=s16:channel_layouts=stereo"
        )
        if chime_volume != 1.0:
            chime_filters += f",volume={chime_volume:.2f}"
        if offset_ms < 0:
            duration = await _get_audio_duration(ffmpeg_bin, chime_path)
            trim_end = max(0.1, duration + offset_ms / 1000)
            chime_filters += f",atrim=end={trim_end:.3f}"
        elif offset_ms > 0:
            chime_filters += f",apad=pad_dur={offset_ms / 1000:.3f}"

        # Input layout: [0]=chime  [1]=tts(pipe:0)  [2]=music(optional)
        cmd = [
            ffmpeg_bin, "-y",
            "-i", chime_path,
            "-f", "s16le", "-ar", str(GEMINI_TTS_SAMPLE_RATE),
            "-ac", str(GEMINI_TTS_CHANNELS),
            "-i", "pipe:0",
        ]
        pcm_input = tts_pcm  # silence handled by adelay on chime

        if music_path:
            cmd += ["-i", music_path]
            if music_position == "before":
                # order: chime → music → tts
                filter_complex = (
                    f"[0:a]{chime_filters}[a0];"
                    f"[2:a]{music_filters}[a2];"
                    f"[1:a]{tts_filters}[a1];"
                    "[a0][a2][a1]concat=n=3:v=0:a=1[out]"
                )
            else:
                # order: chime → tts → music
                filter_complex = (
                    f"[0:a]{chime_filters}[a0];"
                    f"[1:a]{tts_filters}[a1];"
                    f"[2:a]{music_filters}[a2];"
                    "[a0][a1][a2]concat=n=3:v=0:a=1[out]"
                )
        else:
            filter_complex = (
                f"[0:a]{chime_filters}[a0];"
                f"[1:a]{tts_filters}[a1];"
                "[a0][a1]concat=n=2:v=0:a=1[out]"
            )

        cmd += ["-filter_complex", filter_complex, "-map", "[out]", output_path]

    else:
        # No chime — silence goes on whichever segment plays first
        # Input layout: [0]=tts(pipe:0)  [1]=music(optional)
        cmd = [
            ffmpeg_bin, "-y",
            "-f", "s16le", "-ar", str(GEMINI_TTS_SAMPLE_RATE),
            "-ac", str(GEMINI_TTS_CHANNELS),
            "-i", "pipe:0",
        ]

        if music_path:
            cmd += ["-i", music_path]
            if music_position == "before":
                # order: music → tts
                # Silence goes on music (first segment) via adelay filter
                music_filters_with_silence = f"{_ADELAY_1S},{music_filters}"
                filter_complex = (
                    f"[1:a]{music_filters_with_silence}[a1];"
                    f"[0:a]{tts_filters}[a0];"
                    "[a1][a0]concat=n=2:v=0:a=1[out]"
                )
                pcm_input = tts_pcm  # no silence prepended to PCM
            else:
                # order: tts → music
                # Silence prepended to TTS PCM (first segment)
                filter_complex = (
                    f"[0:a]{tts_filters}[a0];"
                    f"[1:a]{music_filters}[a1];"
                    "[a0][a1]concat=n=2:v=0:a=1[out]"
                )
                pcm_input = _SILENCE_PCM + tts_pcm
            cmd += ["-filter_complex", filter_complex, "-map", "[out]", output_path]
        else:
            # Plain TTS only — silence prepended to PCM
            cmd += ["-af", tts_filters, output_path]
            pcm_input = _SILENCE_PCM + tts_pcm

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate(input=pcm_input)

    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace")
        raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}): {err_msg}")

    _LOGGER.debug("Generated WAV at %s", output_path)
