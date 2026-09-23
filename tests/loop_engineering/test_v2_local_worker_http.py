import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from loop_engineering.v2_local_worker_http import (
    LocalWorkerHttpFailure,
    post_worker_request,
)


class Handler(BaseHTTPRequestHandler):
    response_status = 200
    response_payload: object = {"schema_version": 1}
    response_content_type = "application/json"
    received: dict[str, Any] | None = None

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        type(self).received = json.loads(self.rfile.read(length))
        body = json.dumps(type(self).response_payload).encode("utf-8")
        self.send_response(type(self).response_status)
        self.send_header(
            "Content-Type",
            type(self).response_content_type,
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.fixture
def worker_endpoint() -> Iterator[str]:
    Handler.response_status = 200
    Handler.response_payload = {"schema_version": 1}
    Handler.response_content_type = "application/json"
    Handler.received = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_post_worker_request_uses_versioned_http_envelope(
    worker_endpoint: str,
) -> None:
    req = {
        "schema_version": 1,
        "request_identity": "req-1",
    }

    res = post_worker_request(
        worker_endpoint,
        "product",
        req,
        2,
    )

    assert res == {"schema_version": 1}
    assert Handler.received == {
        "api_version": 1,
        "production_name": "product",
        "request": req,
    }


def test_post_worker_request_rejects_http_error(
    worker_endpoint: str,
) -> None:
    Handler.response_status = 409
    Handler.response_payload = {
        "api_version": 1,
        "error": {"code": "WORKER_BUSY"},
    }

    with pytest.raises(LocalWorkerHttpFailure) as captured:
        post_worker_request(
            worker_endpoint,
            "product",
            {"schema_version": 1},
            2,
        )

    assert captured.value.http_status == 409
    assert captured.value.code == "LOCAL_WORKER_HTTP_ERROR"


def test_post_worker_request_rejects_non_json_content_type(
    worker_endpoint: str,
) -> None:
    Handler.response_content_type = "text/plain"

    with pytest.raises(LocalWorkerHttpFailure) as captured:
        post_worker_request(
            worker_endpoint,
            "product",
            {"schema_version": 1},
            2,
        )

    assert captured.value.code == (
        "LOCAL_WORKER_HTTP_CONTENT_TYPE_INVALID"
    )


def test_post_worker_request_rejects_malformed_json(
    worker_endpoint: str,
) -> None:
    Handler.response_payload = "not-an-object"

    with pytest.raises(LocalWorkerHttpFailure) as captured:
        post_worker_request(
            worker_endpoint,
            "product",
            {"schema_version": 1},
            2,
        )

    assert captured.value.code == "LOCAL_WORKER_HTTP_JSON_INVALID"
