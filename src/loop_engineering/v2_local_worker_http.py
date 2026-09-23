"""local-llm-coder localhost Worker HTTP transport。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


MAX_WORKER_HTTP_RESPONSE_BYTES = 2_000_000


@dataclass(frozen=True)
class LocalWorkerHttpFailure(Exception):
    """Worker HTTP transport / protocol failure。"""

    code: str
    http_status: int | None = None


class LocalWorkerHttpTimeout(Exception):
    """Worker HTTP request timeout。"""


def post_worker_request(
    endpoint: str,
    production_name: str,
    request: dict[str, object],
    timeout_seconds: int,
) -> dict[str, Any]:
    body = json.dumps(
        {
            "api_version": 1,
            "production_name": production_name,
            "request": request,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    http_request = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/worker",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        response = urllib.request.urlopen(
            http_request,
            timeout=timeout_seconds,
        )
    except urllib.error.HTTPError as exc:
        try:
            exc.read(MAX_WORKER_HTTP_RESPONSE_BYTES + 1)
        except OSError:
            pass
        raise LocalWorkerHttpFailure(
            "LOCAL_WORKER_HTTP_ERROR",
            exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise LocalWorkerHttpTimeout from exc
        raise LocalWorkerHttpFailure(
            "LOCAL_WORKER_CONNECTION_FAILED",
        ) from exc
    except TimeoutError as exc:
        raise LocalWorkerHttpTimeout from exc
    except OSError as exc:
        raise LocalWorkerHttpFailure(
            "LOCAL_WORKER_CONNECTION_FAILED",
        ) from exc

    try:
        with response:
            if response.status != 200:
                raise LocalWorkerHttpFailure(
                    "LOCAL_WORKER_HTTP_STATUS_INVALID",
                    response.status,
                )
            content_type = response.headers.get("Content-Type", "")
            if (
                content_type.split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                raise LocalWorkerHttpFailure(
                    "LOCAL_WORKER_HTTP_CONTENT_TYPE_INVALID",
                    response.status,
                )
            raw_length = response.headers.get("Content-Length")
            if raw_length:
                try:
                    length = int(raw_length)
                except ValueError as exc:
                    raise LocalWorkerHttpFailure(
                        "LOCAL_WORKER_HTTP_LENGTH_INVALID",
                        response.status,
                    ) from exc
                if length > MAX_WORKER_HTTP_RESPONSE_BYTES:
                    raise LocalWorkerHttpFailure(
                        "LOCAL_WORKER_HTTP_RESPONSE_TOO_LARGE",
                        response.status,
                    )
            raw = response.read(MAX_WORKER_HTTP_RESPONSE_BYTES + 1)
    except LocalWorkerHttpFailure:
        raise
    except OSError as exc:
        raise LocalWorkerHttpFailure(
            "LOCAL_WORKER_HTTP_READ_FAILED",
        ) from exc

    if len(raw) > MAX_WORKER_HTTP_RESPONSE_BYTES:
        raise LocalWorkerHttpFailure(
            "LOCAL_WORKER_HTTP_RESPONSE_TOO_LARGE",
            200,
        )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise LocalWorkerHttpFailure(
            "LOCAL_WORKER_HTTP_JSON_INVALID",
            200,
        ) from exc
    if not isinstance(value, dict):
        raise LocalWorkerHttpFailure(
            "LOCAL_WORKER_HTTP_JSON_INVALID",
            200,
        )
    return value
