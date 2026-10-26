"""Parse what a user pastes into the "use a refresh token" sign-in step.

Accepted, so users can paste straight from wherever they found the token:

* the bare refresh token;
* a small JSON object: ``{"refresh_token": "...", "device_fingerprint": "..."}``;
* the saved auth state of Skylight's own web or mobile client, as copied from browser
  developer tools: ``{"state": {"refreshToken": "...", "uniqueId": "..."}, "version": 1}``,
  optionally with a storage key in front (``mmkv.default\\auth-storage<tab>{...}``).

Key names may be snake_case or camelCase, and may sit at the top level or under ``state``.
A device id (``device_fingerprint`` / ``unique_id`` / ``uniqueId``) is returned when present,
because a refresh token may be tied to the device it was issued to and must then be sent back
unchanged. Access tokens in the pasted text are ignored on purpose.

Kept free of Home Assistant imports so it can be unit tested offline.
"""

from __future__ import annotations

import json
from typing import Any

_TOKEN_KEYS = ("refresh_token", "refreshToken")
_FINGERPRINT_KEYS = (
    "device_fingerprint",
    "deviceFingerprint",
    "fingerprint",
    "unique_id",
    "uniqueId",
)


def _first(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _json_object(text: str) -> dict[str, Any]:
    """Parse the JSON object in ``text``, ignoring anything before the first ``{``."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("not valid JSON")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as err:
        raise ValueError("not valid JSON") from err
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def parse_token_input(raw: str) -> tuple[str, str | None]:
    """Return ``(refresh_token, device_fingerprint or None)``; raise ValueError if unusable."""
    text = (raw or "").strip().strip("'\"")
    if not text:
        raise ValueError("empty")

    if "{" in text:
        data = _json_object(text)
        # Zustand-style persisted state keeps the values one level down.
        candidates = [data, data["state"]] if isinstance(data.get("state"), dict) else [data]
        token = fingerprint = None
        for level in candidates:
            token = token or _first(level, _TOKEN_KEYS)
            fingerprint = fingerprint or _first(level, _FINGERPRINT_KEYS)
        token = token or ""
    else:
        token, fingerprint = text, None

    # A token never contains whitespace; this catches half-pasted URLs and sentences.
    if not token or len(token) < 20 or any(ch.isspace() for ch in token):
        raise ValueError("does not look like a refresh token")
    return token, fingerprint or None
