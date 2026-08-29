"""提供元に依存しないCore値の決定論的な直列化補助。"""

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
            raise ValueError("正規直列化にはタイムゾーン付きdatetimeが必要です")
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
                raise TypeError("正規mappingのkeyには文字列が必要です")
            normalized[key] = _normalize(item)
        return normalized
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        raise TypeError("順序を持たないcollectionは正規直列化できません")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"未対応の正規直列化型です: {type(value).__name__}")


def canonical_json(value: object) -> str:
    """対応するCore値を、簡潔で決定論的なJSONとして返す。"""

    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_digest(value: object) -> str:
    """正規JSONのSHA-256 digestを小文字16進数で返す。"""

    return sha256(canonical_json(value).encode("utf-8")).hexdigest()
