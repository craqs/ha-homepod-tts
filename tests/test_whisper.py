"""Pure helpers for per-speaker whisper."""

from custom_components.homepod_tts.whisper import (
    normalize_mac,
    parse_speaker_list,
    partition_speakers,
)


def test_normalize_mac_formats():
    assert normalize_mac("C2:8C:D9:FF:F3:8C") == "c28cd9fff38c"
    assert normalize_mac("c2-8c-d9-ff-f3-8c") == "c28cd9fff38c"
    assert normalize_mac("A27629752E7B") == "a27629752e7b"


def test_normalize_mac_rejects_non_mac():
    assert normalize_mac("media_player.homepod_l") is None
    assert normalize_mac("532768AE-A295-446A-B538-3337FD631DA3") is None
    assert normalize_mac("") is None
    assert normalize_mac(None) is None


def test_parse_prefers_attribute_list():
    assert parse_speaker_list(["a", " b "], "2") == ["a", "b"]


def test_parse_attribute_json_and_csv_strings():
    assert parse_speaker_list('["a", "b"]', None) == ["a", "b"]
    assert parse_speaker_list("a, b", None) == ["a", "b"]


def test_parse_falls_back_to_state():
    assert parse_speaker_list(None, "media_player.x, media_player.y") == [
        "media_player.x",
        "media_player.y",
    ]


def test_parse_empty_and_unavailable():
    assert parse_speaker_list([], "unavailable") == []
    assert parse_speaker_list(None, "unknown") == []
    assert parse_speaker_list(None, "") == []
    assert parse_speaker_list(None, "none") == []


def test_partition_preserves_order_and_unresolved_play_normally():
    macs = {"a": "m1", "b": "m2", "c": "m3"}
    normal, whisper = partition_speakers(
        ["a", "b", "c", "unknown"], {"m2", "m3"}, macs.get
    )
    assert normal == ["a", "unknown"]
    assert whisper == ["b", "c"]
