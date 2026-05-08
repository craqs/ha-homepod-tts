import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

SERVICE_SAY = "say"
SERVICE_CLEAR_CACHE = "clear_cache"

ATTR_MESSAGE = "message"
ATTR_ENTITY_ID = "entity_id"
ATTR_CHIME = "chime"
ATTR_VOLUME_LEVEL = "volume_level"
ATTR_COMPRESS = "compress"
ATTR_OFFSET = "offset"
ATTR_PROMPT = "prompt"
ATTR_SPEAKER = "speaker"
ATTR_CHIME_VOLUME = "chime_volume"

SERVICE_SAY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_CHIME): cv.boolean,
        vol.Optional(ATTR_VOLUME_LEVEL): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
        vol.Optional(ATTR_COMPRESS): vol.In(["off", "light", "moderate", "heavy"]),
        vol.Optional(ATTR_OFFSET): vol.All(
            vol.Coerce(int), vol.Range(min=-5000, max=5000)
        ),
        vol.Optional(ATTR_PROMPT): cv.string,
        vol.Optional(ATTR_SPEAKER): vol.Any(cv.entity_id, [cv.entity_id]),
        vol.Optional(ATTR_CHIME_VOLUME): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=2.0)
        ),
    }
)

SERVICE_CLEAR_CACHE_SCHEMA = vol.Schema({})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async def async_handle_say(call: ServiceCall) -> None:
        entity_id = call.data[ATTR_ENTITY_ID]
        message = call.data[ATTR_MESSAGE]
        chime = call.data.get(ATTR_CHIME)
        volume = call.data.get(ATTR_VOLUME_LEVEL)
        compress = call.data.get(ATTR_COMPRESS)
        offset = call.data.get(ATTR_OFFSET)
        prompt = call.data.get(ATTR_PROMPT)
        speaker = call.data.get(ATTR_SPEAKER)
        chime_volume = call.data.get(ATTR_CHIME_VOLUME)

        for eid, data in hass.data[DOMAIN].items():
            entity = data.get("entity")
            if entity is not None and entity.entity_id == entity_id:
                await entity.async_play_tts(
                    message,
                    chime=chime,
                    volume=volume,
                    compress=compress,
                    offset=offset,
                    prompt=prompt,
                    speaker=speaker,
                    chime_volume=chime_volume,
                )
                return

        _LOGGER.error("Entity %s not found in homepod_tts", entity_id)

    async def async_handle_clear_cache(call: ServiceCall) -> None:
        from .cache import clear_cache
        count = clear_cache(hass)
        _LOGGER.info("Cache cleared: %d entries removed", count)

    if not hass.services.has_service(DOMAIN, SERVICE_SAY):
        hass.services.async_register(
            DOMAIN, SERVICE_SAY, async_handle_say, schema=SERVICE_SAY_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, SERVICE_CLEAR_CACHE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEAR_CACHE,
            async_handle_clear_cache,
            schema=SERVICE_CLEAR_CACHE_SCHEMA,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SAY)
            hass.services.async_remove(DOMAIN, SERVICE_CLEAR_CACHE)
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry from v1 to v2."""
    if entry.version == 1:
        _LOGGER.info("Migrating homepod_tts config entry from v1 to v2")
        new_options = dict(entry.options)
        # v1 had compress_tts as bool, v2 uses string presets
        if isinstance(new_options.get("compress_tts"), bool):
            new_options["compress_tts"] = (
                "moderate" if new_options["compress_tts"] else "off"
            )
        hass.config_entries.async_update_entry(
            entry, options=new_options, version=2
        )
        _LOGGER.info("Migration to v2 complete")
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
