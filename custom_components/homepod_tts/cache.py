"""Simple file-based TTS PCM cache with LRU eviction."""
import hashlib
import logging
import os
from pathlib import Path

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

CACHE_DIR_NAME = "homepod_tts_cache"


def _cache_dir(hass: HomeAssistant) -> Path:
    path = Path(hass.config.path(CACHE_DIR_NAME))
    path.mkdir(exist_ok=True)
    return path


def cache_key(
    message: str,
    voice: str,
    model: str,
    prompt: str,
) -> str:
    raw = f"{model}|{voice}|{prompt}|{message}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _sync_get_cached(hass: HomeAssistant, key: str) -> bytes | None:
    path = _cache_dir(hass) / f"{key}.pcm"
    if path.is_file():
        _LOGGER.debug("Cache hit: %s", key[:12])
        os.utime(path)  # touch for LRU
        return path.read_bytes()
    return None


async def get_cached(hass: HomeAssistant, key: str) -> bytes | None:
    return await hass.async_add_executor_job(_sync_get_cached, hass, key)


def _sync_put_cache(hass: HomeAssistant, key: str, pcm_data: bytes) -> None:
    path = _cache_dir(hass) / f"{key}.pcm"
    path.write_bytes(pcm_data)
    _LOGGER.debug("Cached TTS PCM: %s (%d bytes)", key[:12], len(pcm_data))


async def put_cache(hass: HomeAssistant, key: str, pcm_data: bytes) -> None:
    await hass.async_add_executor_job(_sync_put_cache, hass, key, pcm_data)


def _sync_clear_cache(hass: HomeAssistant) -> int:
    cache_dir = _cache_dir(hass)
    count = 0
    for f in cache_dir.glob("*.pcm"):
        f.unlink(missing_ok=True)
        count += 1
    _LOGGER.info("Cleared %d cached TTS entries", count)
    return count


async def async_clear_cache(hass: HomeAssistant) -> int:
    return await hass.async_add_executor_job(_sync_clear_cache, hass)


def clear_cache(hass: HomeAssistant) -> int:
    """Synchronous clear_cache - kept for backward compat."""
    return _sync_clear_cache(hass)


def _sync_enforce_max_size(hass: HomeAssistant, max_mb: int) -> None:
    cache_dir = _cache_dir(hass)
    files = sorted(
        cache_dir.glob("*.pcm"),
        key=lambda f: f.stat().st_mtime,
    )
    total = sum(f.stat().st_size for f in files)
    max_bytes = max_mb * 1024 * 1024

    while total > max_bytes and files:
        oldest = files.pop(0)
        size = oldest.stat().st_size
        oldest.unlink(missing_ok=True)
        total -= size
        _LOGGER.debug("Evicted cache file %s (%d bytes)", oldest.name, size)


async def enforce_max_size(hass: HomeAssistant, max_mb: int) -> None:
    await hass.async_add_executor_job(_sync_enforce_max_size, hass, max_mb)
