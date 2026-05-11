import asyncio
import contextlib
import logging
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pyatv
from pyatv.interface import AppleTV

from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .audio import async_generate_wav
from .cache import (
    cache_key,
    enforce_max_size,
    get_cached,
    put_cache,
)
from .const import (
    CONF_CACHE_ENABLED,
    CONF_CACHE_MAX_MB,
    CONF_CHIME_ENABLED,
    CONF_DEFAULT_SPEAKERS,
    CONF_CHIME_OFFSET,
    CONF_CHIME_PATH,
    CONF_CHIME_VOLUME,
    CONF_COMPRESS_TTS,
    CONF_DEFAULT_VOLUME,
    CONF_GEMINI_API_KEY,
    CONF_HOMEPOD_IDENTIFIER,
    CONF_MINI_VOLUME_SCALE,
    CONF_MUTE_ENTITY,
    CONF_QUIET_CHIME_VOLUME,
    CONF_QUIET_ENTITY,
    CONF_QUIET_PROMPT,
    CONF_QUIET_SPEAKERS,
    CONF_QUIET_VOLUME,
    CONF_RESTORE_VOLUME,
    CONF_TTS_MODEL,
    CONF_TTS_PROMPT,
    CONF_TTS_VOICE,
    DEFAULT_CACHE_ENABLED,
    DEFAULT_CACHE_MAX_MB,
    DEFAULT_CHIME_ENABLED,
    DEFAULT_CHIME_OFFSET,
    DEFAULT_CHIME_VOLUME,
    DEFAULT_COMPRESS_TTS,
    DEFAULT_MINI_VOLUME_SCALE,
    DEFAULT_QUIET_CHIME_VOLUME,
    DEFAULT_QUIET_PROMPT,
    DEFAULT_QUIET_VOLUME,
    DEFAULT_RESTORE_VOLUME,
    DEFAULT_TTS_MODEL,
    DEFAULT_TTS_PROMPT,
    DEFAULT_TTS_VOICE,
    DEFAULT_VOLUME,
    DOMAIN,
    MINI_SPEAKER_LABEL,
)
from .tts_client import GeminiTTSClient

_LOGGER = logging.getLogger(__name__)

DEFAULT_CHIME_FILE = str(Path(__file__).parent / "sounds" / "chime.mp3")

# ── Music injection parsing ───────────────────────────────────────────────────
# Syntax: [music: <prompt>]
# Example: "Hello! [music: upbeat jazz piano, 30 seconds]"
_MUSIC_RE = re.compile(r"\[music:\s*(.+?)\]", re.DOTALL | re.IGNORECASE)


def _parse_music_injection(
    message: str,
) -> tuple[str, str | None, str]:
    """Parse [music: prompt] marker from message.

    Returns:
        (clean_message, music_prompt_or_None, music_position)
        music_position is "before" or "after" (relative to TTS content).
    """
    match = _MUSIC_RE.search(message)
    if not match:
        return message, None, "after"

    music_prompt = match.group(1).strip()
    start, end = match.span()
    text_before = message[:start].strip()
    text_after = message[end:].strip()

    if not text_before:
        # Marker at the very start → music plays before TTS
        clean_message = text_after
        position = "before"
    else:
        # Marker at the end (or middle — append any trailing text)
        clean_message = text_before
        if text_after:
            clean_message += " " + text_after
        position = "after"

    return clean_message, music_prompt, position

# Directory under /config/www/ for serving WAVs to Music Assistant
MA_SERVE_DIR = "homepod_tts"


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entity = HomePodTTSNotifyEntity(hass, entry)
    hass.data[DOMAIN][entry.entry_id]["entity"] = entity
    async_add_entities([entity])


class HomePodTTSNotifyEntity(NotifyEntity):

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._lock = asyncio.Lock()
        self._connections: dict[str, AppleTV] = {}
        self._tts_client: GeminiTTSClient | None = None

        self._attr_unique_id = f"{entry.entry_id}_notify"
        self._attr_name = entry.data.get(CONF_NAME, "HomePod TTS")

    # -- config properties --

    @property
    def _identifier(self) -> str:
        return self._entry.data[CONF_HOMEPOD_IDENTIFIER]

    @property
    def _api_key(self) -> str:
        return self._entry.data[CONF_GEMINI_API_KEY]

    @property
    def _chime_enabled(self) -> bool:
        return self._entry.options.get(CONF_CHIME_ENABLED, DEFAULT_CHIME_ENABLED)

    @property
    def _chime_path(self) -> str:
        return self._entry.options.get(CONF_CHIME_PATH, "") or DEFAULT_CHIME_FILE

    @property
    def _chime_volume(self) -> float:
        return self._entry.options.get(CONF_CHIME_VOLUME, DEFAULT_CHIME_VOLUME)

    @property
    def _volume(self) -> float:
        return self._entry.options.get(CONF_DEFAULT_VOLUME, DEFAULT_VOLUME)

    @property
    def _restore_volume(self) -> bool:
        return self._entry.options.get(CONF_RESTORE_VOLUME, DEFAULT_RESTORE_VOLUME)

    @property
    def _voice(self) -> str:
        return self._entry.options.get(CONF_TTS_VOICE, DEFAULT_TTS_VOICE)

    @property
    def _model(self) -> str:
        return self._entry.options.get(CONF_TTS_MODEL, DEFAULT_TTS_MODEL)

    @property
    def _default_prompt(self) -> str:
        return self._entry.options.get(CONF_TTS_PROMPT, DEFAULT_TTS_PROMPT)

    @property
    def _compress_tts(self) -> str:
        return self._entry.options.get(CONF_COMPRESS_TTS, DEFAULT_COMPRESS_TTS)

    @property
    def _chime_offset(self) -> int:
        return self._entry.options.get(CONF_CHIME_OFFSET, DEFAULT_CHIME_OFFSET)

    @property
    def _cache_enabled(self) -> bool:
        return self._entry.options.get(CONF_CACHE_ENABLED, DEFAULT_CACHE_ENABLED)

    @property
    def _cache_max_mb(self) -> int:
        return self._entry.options.get(CONF_CACHE_MAX_MB, DEFAULT_CACHE_MAX_MB)

    @property
    def _default_speakers(self) -> list[str]:
        return self._entry.options.get(CONF_DEFAULT_SPEAKERS, [])

    @property
    def _mute_entity(self) -> str:
        return self._entry.options.get(CONF_MUTE_ENTITY, "")

    @property
    def _quiet_entity(self) -> str:
        return self._entry.options.get(CONF_QUIET_ENTITY, "")

    @property
    def _quiet_prompt(self) -> str:
        return self._entry.options.get(CONF_QUIET_PROMPT, DEFAULT_QUIET_PROMPT)

    @property
    def _quiet_chime_volume(self) -> float:
        return self._entry.options.get(
            CONF_QUIET_CHIME_VOLUME, DEFAULT_QUIET_CHIME_VOLUME
        )

    @property
    def _quiet_volume(self) -> float:
        return self._entry.options.get(CONF_QUIET_VOLUME, DEFAULT_QUIET_VOLUME)

    @property
    def _quiet_speakers(self) -> list[str]:
        return self._entry.options.get(CONF_QUIET_SPEAKERS, [])

    @property
    def _mini_volume_scale(self) -> float:
        return self._entry.options.get(
            CONF_MINI_VOLUME_SCALE, DEFAULT_MINI_VOLUME_SCALE
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose all operational values as entity attributes for easy inspection."""
        is_muted = self._is_muted()
        is_quiet = self._is_quiet()

        # Resolve effective volume/speakers (quiet overrides applied)
        effective_volume = self._quiet_volume if is_quiet else self._volume
        effective_chime_volume = (
            self._quiet_chime_volume if is_quiet else self._chime_volume
        )
        effective_prompt = (
            self._quiet_prompt if is_quiet else self._default_prompt
        )
        effective_speakers = (
            self._quiet_speakers if (is_quiet and self._quiet_speakers)
            else self._default_speakers
        )

        return {
            # -- TTS --
            "tts_model": self._model,
            "tts_voice": self._voice,
            "tts_prompt": self._default_prompt or None,
            # -- Volume --
            "volume": self._volume,
            "mini_volume_scale": self._mini_volume_scale,
            "effective_volume": round(effective_volume, 3),
            # -- Chime --
            "chime_enabled": self._chime_enabled,
            "chime_volume": self._chime_volume,
            "chime_offset_ms": self._chime_offset,
            "effective_chime_volume": round(effective_chime_volume, 3),
            # -- Compression --
            "compress_tts": self._compress_tts,
            # -- Speakers --
            "default_speakers": self._default_speakers,
            "effective_speakers": effective_speakers,
            "effective_prompt": effective_prompt or None,
            # -- Mute --
            "mute_entity": self._mute_entity or None,
            "is_muted": is_muted,
            # -- Quiet mode --
            "quiet_entity": self._quiet_entity or None,
            "is_quiet": is_quiet,
            "quiet_volume": self._quiet_volume,
            "quiet_chime_volume": self._quiet_chime_volume,
            "quiet_prompt": self._quiet_prompt,
            "quiet_speakers": self._quiet_speakers,
            # -- Cache --
            "cache_enabled": self._cache_enabled,
            "cache_max_mb": self._cache_max_mb,
            # -- Restore --
            "restore_volume": self._restore_volume,
        }

    def _is_muted(self) -> bool:
        """Check if the mute entity is on."""
        if not self._mute_entity:
            return False
        state = self._hass.states.get(self._mute_entity)
        return state is not None and state.state == "on"

    def _is_quiet(self) -> bool:
        """Check if the quiet mode entity is on."""
        if not self._quiet_entity:
            return False
        state = self._hass.states.get(self._quiet_entity)
        return state is not None and state.state == "on"

    # -- TTS client --

    def _get_tts_client(self) -> GeminiTTSClient:
        if (
            self._tts_client is None
            or self._tts_client.voice != self._voice
            or self._tts_client.model != self._model
        ):
            session = async_get_clientsession(self._hass)
            self._tts_client = GeminiTTSClient(
                self._api_key, session, self._voice, self._model
            )
        return self._tts_client

    # -- Music Assistant speaker resolution --

    def _has_music_assistant(self) -> bool:
        """Check if Music Assistant is available."""
        return self._hass.services.has_service(
            "music_assistant", "play_announcement"
        )

    def _build_mac_to_ma_map(self) -> dict[str, str]:
        """Build a MAC-address -> MA entity_id mapping.

        Music Assistant media_player entities have a unique_id formatted as
        ``ap<mac_lowercase_no_colons>`` (e.g. ``ap067b90602db2``), readable
        from the entity registry.  Apple TV config entries store identifiers
        as MAC addresses (``06:7B:90:60:2D:B2``).  Normalising both to
        lowercase-no-colon gives a deterministic match.

        Note: older MA versions exposed ``active_queue`` and ``mass_player_type``
        state attributes for this purpose, but these were removed in MA 2.8+.
        Reading from the entity registry is version-agnostic.
        """
        mac_map: dict[str, str] = {}
        registry = er.async_get(self._hass)
        for entry in registry.entities.values():
            if (
                entry.platform == "music_assistant"
                and entry.domain == "media_player"
            ):
                uid = entry.unique_id or ""
                if uid.startswith("ap"):
                    mac_norm = uid[2:]  # strip "ap" prefix
                    mac_map[mac_norm] = entry.entity_id
        return mac_map

    def _resolve_ma_speakers(
        self, speaker: str | list[str] | None
    ) -> list[str]:
        """Resolve speaker field to MA media_player entity_ids."""
        mac_map = self._build_mac_to_ma_map()
        ma_entity_ids = set(mac_map.values())

        if not speaker:
            if self._default_speakers:
                # Default speakers are stored as apple_tv identifiers (MACs)
                result: list[str] = []
                for identifier in self._default_speakers:
                    resolved = self._find_ma_entity_by_mac(
                        identifier, mac_map
                    )
                    result.extend(resolved)
                if result:
                    _LOGGER.debug(
                        "Using configured default speakers: %s", result
                    )
                    return result
            # Fallback to the single configured HomePod
            return self._find_ma_entity_by_mac(
                self._identifier, mac_map
            )

        if isinstance(speaker, str):
            speaker = [speaker]

        result: list[str] = []
        for s in speaker:
            if s in ma_entity_ids:
                # Already an MA entity
                result.append(s)
            elif ":" in s or (len(s) == 12 and s.isalnum()):
                # Looks like a MAC address (apple_tv identifier)
                resolved = self._find_ma_entity_by_mac(s, mac_map)
                result.extend(resolved)
            else:
                # apple_tv media_player -> look up its config entry MAC
                resolved = self._find_ma_entity_for_atv(s, mac_map)
                result.extend(resolved)

        # Deduplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for r in result:
            if r not in seen:
                seen.add(r)
                unique.append(r)
        return unique

    def _find_ma_entity_by_mac(
        self, identifier: str, mac_map: dict[str, str]
    ) -> list[str]:
        """Find MA entity for an apple_tv identifier (MAC address)."""
        mac_norm = identifier.replace(":", "").lower()
        entity_id = mac_map.get(mac_norm)
        if entity_id:
            _LOGGER.debug(
                "Matched MA entity %s for MAC %s", entity_id, identifier
            )
            return [entity_id]
        _LOGGER.warning("No MA entity found for MAC %s", identifier)
        return []

    def _find_ma_entity_for_atv(
        self, atv_entity_id: str, mac_map: dict[str, str]
    ) -> list[str]:
        """Find MA entity matching an apple_tv media_player entity."""
        entity_registry = er.async_get(self._hass)
        entity_entry = entity_registry.async_get(atv_entity_id)
        if entity_entry and entity_entry.config_entry_id:
            apple_tv_entries = self._hass.config_entries.async_entries(
                "apple_tv"
            )
            for atv_entry in apple_tv_entries:
                if atv_entry.entry_id == entity_entry.config_entry_id:
                    uid = atv_entry.unique_id or ""
                    return self._find_ma_entity_by_mac(uid, mac_map)

        _LOGGER.warning(
            "Could not resolve apple_tv entry for %s", atv_entity_id
        )
        return []

    # -- HomePod mini volume scaling --

    def _is_mini(self, entity_id: str | None) -> bool:
        """Return True if the entity (or its paired apple_tv entity) has the 'homepod_mini' label.

        The label can be placed on either the apple_tv OR the Music Assistant
        media_player entity — both are checked via MAC cross-reference so the
        user only needs to label one of the two.
        """
        if not entity_id:
            return False
        registry = er.async_get(self._hass)
        entry = registry.async_get(entity_id)
        if entry is None:
            return False
        if MINI_SPEAKER_LABEL in entry.labels:
            return True
        # If this is a Music Assistant entity, also check the paired apple_tv entity
        if entry.platform == "music_assistant":
            uid = entry.unique_id or ""
            if uid.startswith("ap"):
                mac_norm = uid[2:]
                for atv_entry in self._hass.config_entries.async_entries(
                    "apple_tv"
                ):
                    if (
                        atv_entry.unique_id or ""
                    ).replace(":", "").lower() == mac_norm:
                        for atv_entity in registry.entities.get_entries_for_config_entry_id(
                            atv_entry.entry_id
                        ):
                            if MINI_SPEAKER_LABEL in atv_entity.labels:
                                return True
        return False

    def _scaled_volume(self, base: float, is_mini: bool) -> float:
        """Apply mini_volume_scale multiplier for mini speakers."""
        if is_mini:
            return min(1.0, max(0.0, base * self._mini_volume_scale))
        return base

    def _entity_id_for_mac(self, mac: str) -> str | None:
        """Find the apple_tv media_player entity_id for a given MAC identifier."""
        mac_norm = mac.replace(":", "").lower()
        registry = er.async_get(self._hass)
        for atv_entry in self._hass.config_entries.async_entries("apple_tv"):
            if (atv_entry.unique_id or "").replace(":", "").lower() == mac_norm:
                for entity in registry.entities.get_entries_for_config_entry_id(
                    atv_entry.entry_id
                ):
                    if entity.domain == "media_player":
                        return entity.entity_id
        return None

    # -- Music Assistant transport --

    async def _async_play_via_ma(
        self,
        wav_path: str,
        speakers: list[str],
        volume: float,
    ) -> str:
        """Play WAV via Music Assistant. Returns the served file path."""
        # Ensure the www/homepod_tts directory exists
        www_dir = Path(self._hass.config.path("www")) / MA_SERVE_DIR
        www_dir.mkdir(parents=True, exist_ok=True)

        # Copy WAV to www directory with unique name
        filename = f"tts_{uuid.uuid4().hex[:12]}.wav"
        served_path = www_dir / filename
        await self._hass.async_add_executor_job(
            shutil.copy2, wav_path, str(served_path)
        )

        # Build the local URL
        base_url = self._hass.config.internal_url or self._hass.config.external_url
        if not base_url:
            # Fallback: construct from HA config
            base_url = "http://homeassistant.local:8123"
        url = f"{base_url}/local/{MA_SERVE_DIR}/{filename}"

        _LOGGER.debug(
            "Playing via Music Assistant: %s -> %s", url, speakers
        )

        # Read current volumes so we can restore them afterwards
        orig_volumes: dict[str, float | None] = {}
        for spk in speakers:
            state = self._hass.states.get(spk)
            orig_volumes[spk] = (
                state.attributes.get("volume_level") if state else None
            )

        # Set per-speaker volumes in parallel (mini gets scaled independently)
        async def _set_vol(entity_id: str, vol: float) -> None:
            await self._hass.services.async_call(
                "media_player",
                "volume_set",
                {"volume_level": vol},
                target={"entity_id": entity_id},
            )

        await asyncio.gather(*(
            _set_vol(spk, self._scaled_volume(volume, self._is_mini(spk)))
            for spk in speakers
        ))

        # Single play_announcement call — no announce_volume so MA plays at
        # each speaker's current (pre-set) level, preserving AirPlay 2 sync
        service_data: dict[str, Any] = {
            "url": url,
            "use_pre_announce": False,
        }
        await self._hass.services.async_call(
            "music_assistant",
            "play_announcement",
            service_data,
            target={"entity_id": speakers},
            blocking=True,
        )

        # Restore original per-speaker volumes in parallel
        await asyncio.gather(*(
            _set_vol(spk, orig_vol)
            for spk, orig_vol in orig_volumes.items()
            if orig_vol is not None
        ))

        # Schedule cleanup of the served file after a delay
        async def _cleanup() -> None:
            await asyncio.sleep(60)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(str(served_path))

        self._hass.async_create_task(_cleanup())

        return str(served_path)

    # -- pyatv connections (fallback) --

    def _resolve_speaker_identifier(
        self, speaker_entity_id: str | None
    ) -> str:
        """Resolve a media_player entity_id to an apple_tv identifier.

        Handles both apple_tv and music_assistant entity IDs so that pyatv
        fallback works correctly even when speakers were configured as MA
        entity IDs.
        """
        if not speaker_entity_id:
            return self._identifier

        entity_registry = er.async_get(self._hass)
        entity_entry = entity_registry.async_get(speaker_entity_id)
        if entity_entry:
            apple_tv_entries = self._hass.config_entries.async_entries(
                "apple_tv"
            )
            if entity_entry.platform == "music_assistant":
                # MA unique_id is ap<mac_no_colons> — strip prefix and match
                uid = entity_entry.unique_id or ""
                if uid.startswith("ap"):
                    mac_norm = uid[2:]
                    for atv_entry in apple_tv_entries:
                        atv_uid = (
                            atv_entry.unique_id or ""
                        ).replace(":", "").lower()
                        if atv_uid == mac_norm:
                            return atv_entry.unique_id or atv_entry.entry_id
            elif entity_entry.config_entry_id:
                # apple_tv entity — match by config entry
                for atv_entry in apple_tv_entries:
                    if atv_entry.entry_id == entity_entry.config_entry_id:
                        return atv_entry.unique_id or atv_entry.entry_id

        _LOGGER.warning(
            "Could not resolve speaker %s, using default", speaker_entity_id
        )
        return self._identifier

    def _resolve_speakers_pyatv(
        self, speaker: str | list[str] | None
    ) -> list[str]:
        """Resolve speaker field to a list of apple_tv identifiers."""
        if not speaker:
            return [self._identifier]
        if isinstance(speaker, str):
            speaker = [speaker]
        seen: set[str] = set()
        result: list[str] = []
        for s in speaker:
            identifier = self._resolve_speaker_identifier(s)
            if identifier not in seen:
                seen.add(identifier)
                result.append(identifier)
        return result

    async def _async_get_connection(self, identifier: str) -> AppleTV:
        """Get or create a cached connection to a specific device."""
        if identifier in self._connections:
            return self._connections[identifier]

        apple_tv_entries = self._hass.config_entries.async_entries("apple_tv")

        credentials: dict[int, str] = {}
        for atv_entry in apple_tv_entries:
            uid = atv_entry.unique_id or atv_entry.entry_id
            if uid == identifier:
                credentials = atv_entry.data.get("credentials", {})
                break

        atvs = await pyatv.scan(
            self._hass.loop,
            identifier=identifier,
            timeout=5,
        )
        if not atvs:
            raise ConnectionError(
                f"Could not find HomePod with identifier {identifier}"
            )

        conf = atvs[0]
        for protocol_int, cred in credentials.items():
            protocol = pyatv.const.Protocol(int(protocol_int))
            conf.set_credentials(protocol, cred)

        atv = await pyatv.connect(conf, self._hass.loop)
        self._connections[identifier] = atv
        return atv

    async def _async_disconnect(self, identifier: str | None = None) -> None:
        """Disconnect one or all cached connections."""
        if identifier:
            atv = self._connections.pop(identifier, None)
            if atv is not None:
                atv.close()
        else:
            for atv in self._connections.values():
                atv.close()
            self._connections.clear()

    async def _async_stream_to_device(
        self,
        identifier: str,
        tmp_path: str,
        volume: float,
    ) -> None:
        """Stream WAV to a single HomePod via pyatv."""
        previous_volume: float | None = None
        try:
            atv = await self._async_get_connection(identifier)

            if self._restore_volume:
                with contextlib.suppress(Exception):
                    previous_volume = atv.audio.volume

            atv_entity = self._entity_id_for_mac(identifier)
            effective_vol = self._scaled_volume(
                volume, self._is_mini(atv_entity)
            )
            with contextlib.suppress(Exception):
                await atv.audio.set_volume(effective_vol * 100)

            _LOGGER.debug(
                "Streaming %s to HomePod %s", tmp_path, identifier[:12]
            )
            await atv.stream.stream_file(tmp_path)

        except ConnectionError:
            _LOGGER.warning(
                "Lost connection to HomePod %s, will reconnect next call",
                identifier[:12],
            )
            await self._async_disconnect(identifier)
        except Exception:
            _LOGGER.exception(
                "Failed to stream to HomePod %s", identifier[:12]
            )
            await self._async_disconnect(identifier)
        finally:
            if previous_volume is not None:
                with contextlib.suppress(Exception):
                    atv = self._connections.get(identifier)
                    if atv is not None:
                        await atv.audio.set_volume(previous_volume)

    # -- main TTS pipeline --

    async def async_play_tts(
        self,
        message: str,
        *,
        chime: bool | None = None,
        volume: float | None = None,
        compress: str | None = None,
        offset: int | None = None,
        prompt: str | None = None,
        speaker: str | list[str] | None = None,
        chime_volume: float | None = None,
        quiet: bool | None = None,
    ) -> None:
        # -- Mute check --
        if self._is_muted():
            _LOGGER.debug("Muted by %s, ignoring TTS call", self._mute_entity)
            return

        if chime is None:
            chime = self._chime_enabled
        if volume is None:
            volume = self._volume
        if compress is None:
            compress = self._compress_tts
        if offset is None:
            offset = self._chime_offset
        if chime_volume is None:
            chime_volume = self._chime_volume

        effective_prompt = prompt if prompt is not None else self._default_prompt

        # -- Quiet mode --
        is_quiet = quiet if quiet is not None else self._is_quiet()
        if is_quiet:
            effective_prompt = self._quiet_prompt
            chime_volume = self._quiet_chime_volume
            volume = self._quiet_volume
            # Override speakers if quiet speakers configured and no explicit speaker
            if not speaker and self._quiet_speakers:
                speaker = self._quiet_speakers
                _LOGGER.debug(
                    "Quiet mode: using quiet speakers %s", speaker
                )
            _LOGGER.debug("Quiet mode active")
        use_ma = self._has_music_assistant()

        # Parse music injection marker [music: prompt] from message
        clean_message, music_prompt, music_position = _parse_music_injection(message)
        if music_prompt:
            _LOGGER.debug(
                "Music injection detected: prompt=%r position=%s",
                music_prompt, music_position,
            )

        async with self._lock:
            tmp_path = None
            tmp_music_path = None
            try:
                tts_client = self._get_tts_client()

                # Check cache (keyed on the clean message without music marker)
                key = cache_key(
                    clean_message, tts_client.voice, tts_client.model,
                    effective_prompt,
                )
                tts_pcm = None
                if self._cache_enabled:
                    tts_pcm = await get_cached(self._hass, key)

                # Fire TTS synthesis and music generation concurrently
                if tts_pcm is None and music_prompt:
                    _LOGGER.debug(
                        "Synthesizing TTS + generating music concurrently"
                    )
                    tts_pcm, music_bytes = await asyncio.gather(
                        tts_client.synthesize(
                            clean_message, prompt=effective_prompt or None
                        ),
                        tts_client.generate_music(music_prompt),
                    )
                    if self._cache_enabled:
                        await put_cache(self._hass, key, tts_pcm)
                        await enforce_max_size(self._hass, self._cache_max_mb)
                elif tts_pcm is None:
                    _LOGGER.debug("Synthesizing TTS for: %s", clean_message)
                    tts_pcm = await tts_client.synthesize(
                        clean_message, prompt=effective_prompt or None
                    )
                    music_bytes = None
                    if self._cache_enabled:
                        await put_cache(self._hass, key, tts_pcm)
                        await enforce_max_size(self._hass, self._cache_max_mb)
                else:
                    # Cache hit for TTS — still need to generate music if requested
                    if music_prompt:
                        _LOGGER.debug("TTS cache hit; generating music separately")
                        music_bytes = await tts_client.generate_music(music_prompt)
                    else:
                        music_bytes = None

                # Save music to temp file if we have it
                if music_bytes:
                    fd_m, tmp_music_path = tempfile.mkstemp(
                        suffix=".mp3", prefix="homepod_music_"
                    )
                    os.close(fd_m)
                    await self._hass.async_add_executor_job(
                        _write_bytes, tmp_music_path, music_bytes
                    )

                fd, tmp_path = tempfile.mkstemp(
                    suffix=".wav", prefix="homepod_tts_"
                )
                os.close(fd)

                chime_path = self._chime_path if chime else None
                await async_generate_wav(
                    self._hass,
                    tts_pcm,
                    chime_path,
                    tmp_path,
                    compress_preset=compress,
                    offset_ms=offset,
                    chime_volume=chime_volume,
                    music_path=tmp_music_path,
                    music_position=music_position,
                )

                if use_ma:
                    # Music Assistant transport (synchronized AirPlay 2)
                    ma_speakers = self._resolve_ma_speakers(speaker)
                    if not ma_speakers:
                        _LOGGER.error(
                            "No Music Assistant speakers found, "
                            "falling back to pyatv"
                        )
                        use_ma = False

                if use_ma:
                    await self._async_play_via_ma(
                        tmp_path, ma_speakers, volume
                    )
                else:
                    # pyatv transport (fallback)
                    target_ids = self._resolve_speakers_pyatv(speaker)
                    if len(target_ids) == 1:
                        await self._async_stream_to_device(
                            target_ids[0], tmp_path, volume
                        )
                    else:
                        _LOGGER.debug(
                            "Streaming to %d speakers in parallel (pyatv)",
                            len(target_ids),
                        )
                        await asyncio.gather(
                            *(
                                self._async_stream_to_device(
                                    tid, tmp_path, volume
                                )
                                for tid in target_ids
                            )
                        )

            except Exception:
                _LOGGER.exception("Failed to send TTS to HomePod")
            finally:
                if tmp_path is not None:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(tmp_path)
                if tmp_music_path is not None:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(tmp_music_path)

    async def async_play_music(
        self,
        prompt: str,
        *,
        volume: float | None = None,
        speaker: str | list[str] | None = None,
    ) -> None:
        """Generate music via Lyria 3 and play on speakers."""
        if self._is_muted():
            _LOGGER.debug("Muted, ignoring play_music call")
            return

        if volume is None:
            volume = self._volume

        use_ma = self._has_music_assistant()

        async with self._lock:
            tmp_path = None
            try:
                tts_client = self._get_tts_client()
                _LOGGER.debug("Generating music for: %s", prompt)
                mp3_bytes = await tts_client.generate_music(prompt)

                fd, tmp_path = tempfile.mkstemp(
                    suffix=".mp3", prefix="homepod_music_"
                )
                os.close(fd)
                await self._hass.async_add_executor_job(
                    _write_bytes, tmp_path, mp3_bytes
                )

                if use_ma:
                    ma_speakers = self._resolve_ma_speakers(speaker)
                    if ma_speakers:
                        await self._async_play_mp3_via_ma(
                            tmp_path, ma_speakers, volume
                        )
                    else:
                        _LOGGER.error("No MA speakers found for music playback")
                else:
                    _LOGGER.error(
                        "Music playback requires Music Assistant (MP3 format)"
                    )

            except Exception:
                _LOGGER.exception("Failed to generate/play music")
            finally:
                if tmp_path is not None:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(tmp_path)

    async def _async_play_mp3_via_ma(
        self,
        mp3_path: str,
        speakers: list[str],
        volume: float,
    ) -> str:
        """Play MP3 via Music Assistant."""
        www_dir = Path(self._hass.config.path("www")) / MA_SERVE_DIR
        www_dir.mkdir(parents=True, exist_ok=True)

        filename = f"music_{uuid.uuid4().hex[:12]}.mp3"
        served_path = www_dir / filename
        await self._hass.async_add_executor_job(
            shutil.copy2, mp3_path, str(served_path)
        )

        base_url = (
            self._hass.config.internal_url
            or self._hass.config.external_url
            or "http://homeassistant.local:8123"
        )
        url = f"{base_url}/local/{MA_SERVE_DIR}/{filename}"

        _LOGGER.debug("Playing music via MA: %s -> %s", url, speakers)

        # Per-speaker volume (mini scaling), then single MA call
        orig_volumes: dict[str, float | None] = {}
        for spk in speakers:
            state = self._hass.states.get(spk)
            orig_volumes[spk] = (
                state.attributes.get("volume_level") if state else None
            )

        async def _set_vol(entity_id: str, vol: float) -> None:
            await self._hass.services.async_call(
                "media_player",
                "volume_set",
                {"volume_level": vol},
                target={"entity_id": entity_id},
            )

        await asyncio.gather(*(
            _set_vol(spk, self._scaled_volume(volume, self._is_mini(spk)))
            for spk in speakers
        ))

        service_data: dict[str, Any] = {
            "url": url,
            "use_pre_announce": False,
        }
        await self._hass.services.async_call(
            "music_assistant",
            "play_announcement",
            service_data,
            target={"entity_id": speakers},
            blocking=True,
        )

        await asyncio.gather(*(
            _set_vol(spk, orig_vol)
            for spk, orig_vol in orig_volumes.items()
            if orig_vol is not None
        ))

        async def _cleanup() -> None:
            await asyncio.sleep(120)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(str(served_path))

        self._hass.async_create_task(_cleanup())
        return str(served_path)

    async def async_send_message(self, message: str, **kwargs: Any) -> None:
        await self.async_play_tts(message)

    async def async_will_remove_from_hass(self) -> None:
        await self._async_disconnect()
