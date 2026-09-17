"""Per-speaker whisper: announcement splitting in the notify entity.

Speakers, TTS and audio rendering are faked; each test asserts which clip
(prompt) went to which speakers at which volume.
"""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.homepod_tts.const import DOMAIN
from custom_components.homepod_tts.notify import HomePodTTSNotifyEntity

LIVING_ROOM = "C2:8C:D9:FF:F3:8C"
JACOB = "5A:EC:26:9D:3C:D1"
HALL = "06:7B:90:60:2D:B2"
BEDROOM = "A27629752E7B"
ROOMS = {
    LIVING_ROOM: "living_room",
    JACOB: "jacob",
    HALL: "hall",
    BEDROOM: "bedroom",
}

QUIET_PROMPT = "Speak in a soft, gentle whisper"
WHISPER_ENTITY = "sensor.chime_whisper_speakers"


def ma(room: str) -> str:
    return f"media_player.ma_{room}"


def atv(room: str) -> str:
    return f"media_player.atv_{room}"


class FakeTTSClient:
    def __init__(self, api_key, session, voice="Aoede", model="m") -> None:
        self.voice = voice
        self.model = model

    async def synthesize(self, text, prompt=None):
        return f"{prompt or ''}|{text}".encode()

    async def generate_music(self, prompt):
        return b"music"


async def fake_generate_wav(hass, tts_pcm, chime_path, output_path, **kwargs):
    with open(output_path, "wb") as f:
        f.write(tts_pcm)


@pytest.fixture
async def entity(hass: HomeAssistant):
    """Set up the integration with 4 fake HomePods and capture playback."""
    registry = er.async_get(hass)
    for mac, room in ROOMS.items():
        atv_entry = MockConfigEntry(domain="apple_tv", unique_id=mac, title=room)
        atv_entry.add_to_hass(hass)
        registry.async_get_or_create(
            "media_player", "apple_tv", mac,
            config_entry=atv_entry, suggested_object_id=f"atv_{room}",
        )
        registry.async_get_or_create(
            "media_player", "music_assistant",
            "ap" + mac.replace(":", "").lower(),
            suggested_object_id=f"ma_{room}",
        )
        hass.states.async_set(ma(room), "idle")

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=HALL,
        data={
            "name": "HomePod TTS",
            "homepod_identifier": HALL,
            "gemini_api_key": "test",
        },
        options={
            "default_speakers": [LIVING_ROOM, JACOB, HALL, BEDROOM],
            "default_volume": 0.3,
            "chime_volume": 1.4,
            "quiet_entity": "binary_sensor.quiet_mode",
            "quiet_speakers": [LIVING_ROOM, BEDROOM],
            "quiet_prompt": QUIET_PROMPT,
            "quiet_volume": 0.25,
            "quiet_chime_volume": 0.6,
            "mute_entity": "input_boolean.night_mode",
            "whisper_speakers_entity": WHISPER_ENTITY,
            "cache_enabled": False,
        },
    )
    entry.add_to_hass(hass)
    async_mock_service(hass, "music_assistant", "play_announcement")

    plays: list[dict] = []
    streams: list[tuple[str, float]] = []

    async def fake_play_via_ma(self, wav_path, speakers, volume):
        with open(wav_path, "rb") as f:
            prompt = f.read().decode().split("|")[0]
        plays.append(
            {"speakers": sorted(speakers), "volume": volume, "prompt": prompt}
        )
        return wav_path

    async def fake_stream(self, identifier, tmp_path, volume):
        streams.append((identifier, volume))

    with (
        patch("custom_components.homepod_tts.notify.GeminiTTSClient", FakeTTSClient),
        patch(
            "custom_components.homepod_tts.notify.async_generate_wav",
            fake_generate_wav,
        ),
        patch.object(HomePodTTSNotifyEntity, "_async_play_via_ma", fake_play_via_ma),
        patch.object(HomePodTTSNotifyEntity, "_async_stream_to_device", fake_stream),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        ent = hass.data[DOMAIN][entry.entry_id]["entity"]
        ent.test_plays = plays
        ent.test_streams = streams
        ent.test_entry = entry
        yield ent


def set_whisper(hass: HomeAssistant, speakers: list[str]) -> None:
    hass.states.async_set(
        WHISPER_ENTITY, str(len(speakers)), {"speakers": speakers}
    )


async def test_no_whisper_entity_state_plays_normally(hass, entity):
    await entity.async_play_tts("hello")
    assert entity.test_plays == [
        {
            "speakers": sorted(ma(r) for r in ROOMS.values()),
            "volume": 0.3,
            "prompt": "",
        }
    ]


async def test_tomek_in_call_splits_living_room_and_hall(hass, entity):
    set_whisper(hass, [atv("living_room"), atv("hall")])
    await entity.async_play_tts("hello")
    plays = sorted(entity.test_plays, key=lambda p: p["volume"])
    assert plays == [
        {
            "speakers": sorted([ma("living_room"), ma("hall")]),
            "volume": 0.25,
            "prompt": QUIET_PROMPT,
        },
        {
            "speakers": sorted([ma("jacob"), ma("bedroom")]),
            "volume": 0.3,
            "prompt": "",
        },
    ]


async def test_whisper_list_accepts_macs_and_ma_entities(hass, entity):
    set_whisper(hass, [BEDROOM, ma("hall")])
    await entity.async_play_tts("hello")
    whisper = [p for p in entity.test_plays if p["prompt"] == QUIET_PROMPT]
    assert whisper[0]["speakers"] == sorted([ma("bedroom"), ma("hall")])


async def test_all_targets_whisper_is_single_play(hass, entity):
    set_whisper(hass, [atv(r) for r in ROOMS.values()])
    await entity.async_play_tts("hello")
    assert entity.test_plays == [
        {
            "speakers": sorted(ma(r) for r in ROOMS.values()),
            "volume": 0.25,
            "prompt": QUIET_PROMPT,
        }
    ]


async def test_whisper_speakers_not_targeted_is_single_normal_play(hass, entity):
    set_whisper(hass, [atv("hall")])
    await entity.async_play_tts("hello", speaker=[atv("jacob")])
    assert entity.test_plays == [
        {"speakers": [ma("jacob")], "volume": 0.3, "prompt": ""}
    ]


async def test_explicit_speakers_are_split_and_keep_caller_prompt(hass, entity):
    set_whisper(hass, [atv("bedroom")])
    await entity.async_play_tts(
        "hello", speaker=[atv("bedroom"), atv("jacob")], prompt="Cheerful",
        volume=0.5,
    )
    plays = sorted(entity.test_plays, key=lambda p: p["volume"])
    assert plays == [
        {"speakers": [ma("bedroom")], "volume": 0.25, "prompt": QUIET_PROMPT},
        {"speakers": [ma("jacob")], "volume": 0.5, "prompt": "Cheerful"},
    ]


async def test_global_quiet_mode_wins_over_whisper(hass, entity):
    hass.states.async_set("binary_sensor.quiet_mode", "on")
    set_whisper(hass, [atv("hall")])
    await entity.async_play_tts("hello")
    assert entity.test_plays == [
        {
            "speakers": sorted([ma("living_room"), ma("bedroom")]),
            "volume": 0.25,
            "prompt": QUIET_PROMPT,
        }
    ]


async def test_quiet_false_disables_whisper(hass, entity):
    set_whisper(hass, [atv("hall")])
    await entity.async_play_tts("hello", quiet=False)
    assert len(entity.test_plays) == 1
    assert entity.test_plays[0]["prompt"] == ""


async def test_mute_wins_over_everything(hass, entity):
    hass.states.async_set("input_boolean.night_mode", "on")
    set_whisper(hass, [atv("hall")])
    await entity.async_play_tts("hello")
    assert entity.test_plays == []


async def test_unavailable_whisper_entity_plays_normally(hass, entity):
    hass.states.async_set(WHISPER_ENTITY, "unavailable")
    await entity.async_play_tts("hello")
    assert len(entity.test_plays) == 1
    assert entity.test_plays[0]["volume"] == 0.3


async def test_pyatv_fallback_streams_split_groups_to_mac_identifiers(
    hass, entity
):
    hass.services.async_remove("music_assistant", "play_announcement")
    set_whisper(hass, [atv("living_room"), atv("hall")])
    await entity.async_play_tts("hello")
    assert sorted(entity.test_streams) == sorted(
        [(LIVING_ROOM, 0.25), (HALL, 0.25), (JACOB, 0.3), (BEDROOM, 0.3)]
    )


async def test_attributes_follow_whisper_entity(hass, entity):
    set_whisper(hass, [atv("bedroom"), atv("hall")])
    await hass.async_block_till_done()
    attrs = hass.states.get(entity.entity_id).attributes
    assert attrs["whisper_speakers"] == [atv("bedroom"), atv("hall")]
    assert attrs["effective_whisper_speakers"] == [HALL, BEDROOM]
    assert attrs["effective_normal_speakers"] == [LIVING_ROOM, JACOB]

    hass.states.async_set("binary_sensor.quiet_mode", "on")
    await hass.async_block_till_done()
    attrs = hass.states.get(entity.entity_id).attributes
    assert attrs["is_quiet"] is True
    assert attrs["effective_whisper_speakers"] == []


async def test_options_flow_saves_whisper_entity(hass, entity):
    entry = entity.test_entry
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "whisper_speakers_entity" in {
        str(k) for k in result["data_schema"].schema
    }
    options = dict(entry.options)
    options["whisper_speakers_entity"] = "sensor.other"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=options
    )
    await hass.async_block_till_done()
    assert entry.options["whisper_speakers_entity"] == "sensor.other"
