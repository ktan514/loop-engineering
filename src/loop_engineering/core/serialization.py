"""Deterministic serialization helpers for provider-independent Core values."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Any, cast


def _normalize(value: object) -> object:
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("canonical serialization requires timezone-aware datetime")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if is_dataclass(value) and not isinstance(value, type):
        dataclass_value = cast(Any, value)
        return {
            field.name: _normalize(getattr(dataclass_value, field.name))
            for field in fields(dataclass_value)
        }
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical mappings require string keys")
            normalized[key] = _normalize(item)
        return normalized
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        raise TypeError("unordered collections are not canonically serializable")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"unsupported canonical serialization type: {type(value).__name__}")


def canonical_json(value: object) -> str:
    """Return compact deterministic JSON for a supported Core value."""

    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_digest(value: object) -> str:
    """Return a lowercase SHA-256 digest of canonical JSON."""

    return sha256(canonical_json(value).encode("utf-8")).hexdigest()
