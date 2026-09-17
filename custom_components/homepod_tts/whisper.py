"""Per-speaker whisper mode: pure helpers (no Home Assistant imports).

A "whisper speakers" entity names the speakers that should whisper right now
(e.g. the rooms where someone is on a call). An announcement is then split into
two groups: the whisper group gets the quiet prompt/volume, the rest plays
normally. Kept free of HA imports so it can be unit-tested standalone.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from typing import Any

_MAC_RE = re.compile(r"^[0-9a-f]{12}$")


def normalize_mac(value: str) -> str | None:
    """Return a lowercase, separator-free MAC, or None if value isn't a MAC.

    Accepts ``C2:8C:D9:FF:F3:8C``, ``c2-8c-d9-ff-f3-8c`` and ``A27629752E7B``.
    """
    if not isinstance(value, str):
        return None
    norm = value.strip().replace(":", "").replace("-", "").lower()
    return norm if _MAC_RE.match(norm) else None


def parse_speaker_list(attribute: Any, state: str | None) -> list[str]:
    """Extract the speaker list from the whisper entity.

    Prefers a ``speakers`` attribute (list, or a string holding a JSON list or
    comma-separated values). Falls back to the entity state as a
    comma-separated list, so a UI template helper without attributes works too.
    Unknown/unavailable/empty states yield an empty list.
    """
    for raw in (attribute, state):
        items = _as_list(raw)
        if items:
            return items
    return []


def _as_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(x).strip() for x in raw if str(x).strip()]
    if not isinstance(raw, str):
        return []
    text = raw.strip()
    if text.lower() in ("", "unknown", "unavailable", "none", "[]"):
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


def partition_speakers(
    targets: Iterable[str],
    whisper_macs: set[str],
    mac_of: Callable[[str], str | None],
) -> tuple[list[str], list[str]]:
    """Split targets into ``(normal, whisper)`` preserving order.

    ``mac_of`` maps a target (MAC or entity_id) to its normalized MAC; targets
    it can't resolve play normally.
    """
    normal: list[str] = []
    whisper: list[str] = []
    for target in targets:
        mac = mac_of(target)
        if mac is not None and mac in whisper_macs:
            whisper.append(target)
        else:
            normal.append(target)
    return normal, whisper
