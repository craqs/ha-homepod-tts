"""Music Assistant health sensor for HomePod TTS."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEFAULT_SPEAKERS, DOMAIN
from .notify import classify_ma_speakers

_LOGGER = logging.getLogger(__name__)

MA_ANNOUNCE_SERVICE = ("music_assistant", "play_announcement")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    default_speakers = list(entry.options.get(CONF_DEFAULT_SPEAKERS, []))
    if not default_speakers:
        return
    async_add_entities([MusicAssistantHealthSensor(hass, entry, default_speakers)])


class MusicAssistantHealthSensor(SensorEntity):
    """Reports whether Music Assistant can serve all configured speakers.

    States:
      ok        — all default speakers are resolved and available in MA
      degraded  — MA is available but not all speakers are (partial playback)
      failed    — MA service absent or no speakers available (pyatv fallback only)
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_icon = "mdi:speaker-multiple"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        default_speakers: list[str],
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._default_speakers = default_speakers
        self._attr_unique_id = f"{entry.entry_id}_ma_health"
        self._attr_name = "MA Health"
        self._available: list[str] = []
        self._unavailable: list[str] = []
        self._unresolved: list[str] = []
        self._recompute()

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": self._entry.title,
        }

    def _recompute(self) -> None:
        self._available, self._unavailable, self._unresolved = classify_ma_speakers(
            self.hass, self._default_speakers
        )

    @property
    def native_value(self) -> str:
        has_ma = self.hass.services.has_service(*MA_ANNOUNCE_SERVICE)
        if not has_ma or not self._available:
            return "failed"
        if len(self._available) < len(self._default_speakers):
            return "degraded"
        return "ok"

    @property
    def extra_state_attributes(self) -> dict:
        has_ma = self.hass.services.has_service(*MA_ANNOUNCE_SERVICE)
        return {
            "transport": (
                "music_assistant"
                if has_ma and self._available
                else "pyatv_fallback"
            ),
            "available_count": len(self._available),
            "configured_count": len(self._default_speakers),
            "available": self._available,
            "unavailable": self._unavailable,
            "unresolved_macs": self._unresolved,
        }

    async def async_added_to_hass(self) -> None:
        @callback
        def _on_state_change(event) -> None:
            entity_id: str = event.data.get("entity_id", "")
            if entity_id.startswith("media_player."):
                self._recompute()
                self.async_write_ha_state()

        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, _on_state_change)
        )
