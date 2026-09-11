import json

import pytest

from protected_delivery.recovery_http_v106 import RecoveryHTTPApplicationV106, create_http_server


class Service:
    def __init__(self):
        self.handled = []

    def health(self, *, now):
        return {"status": "READY", "now": now}

    def handle(self, envelope, *, now):
        self.handled.append((envelope, now))
        if envelope.get("reject"):
            raise RuntimeError("authenticated_command_replay")
        return {"accepted": True, "now": now}


def decode(raw):
    return json.loads(raw.decode("utf-8"))


def test_health_route_is_read_only_and_no_store():
    app = RecoveryHTTPApplicationV106(service=Service())
    status, headers, body = app.dispatch(method="GET", path="/v1/health", now=100)
    assert status == 200
    assert headers["cache-control"] == "no-store"
    assert decode(body)["result"] == {"status": "READY", "now": 100}
    status, _, body = app.dispatch(method="GET", path="/v1/health", body=b"x", now=100)
    assert status == 400
    assert decode(body)["error"] == "health_body_not_allowed"


def test_command_requires_json_and_delegates_authenticated_envelope():
    service = Service()
    app = RecoveryHTTPApplicationV106(service=service)
    raw = json.dumps({"key_id": "k", "payload": {"operation": "health"}}).encode()
    status, _, body = app.dispatch(
        method="POST", path="/v1/command", body=raw, content_type="application/json; charset=utf-8", now=123
    )
    assert status == 200
    assert decode(body)["result"]["accepted"] is True
    assert service.handled == [({"key_id": "k", "payload": {"operation": "health"}}, 123)]


def test_command_rejection_is_fail_closed_and_does_not_return_success():
    app = RecoveryHTTPApplicationV106(service=Service())
    raw = json.dumps({"reject": True}).encode()
    status, headers, body = app.dispatch(
        method="POST", path="/v1/command", body=raw, content_type="application/json", now=123
    )
    assert status == 403
    assert headers["cache-control"] == "no-store"
    assert decode(body) == {"status": "REJECTED", "error": "authenticated_command_replay"}


def test_size_content_type_json_and_route_guards():
    app = RecoveryHTTPApplicationV106(service=Service(), max_body_bytes=256)
    status, _, body = app.dispatch(
        method="POST", path="/v1/command", body=b"x" * 257, content_type="application/json", now=1
    )
    assert status == 413 and decode(body)["error"] == "request_body_too_large"
    status, _, body = app.dispatch(
        method="POST", path="/v1/command", body=b"{}", content_type="text/plain", now=1
    )
    assert status == 415 and decode(body)["error"] == "content_type_must_be_json"
    status, _, body = app.dispatch(
        method="POST", path="/v1/command", body=b"{", content_type="application/json", now=1
    )
    assert status == 400 and decode(body)["error"] == "invalid_request_json"
    status, headers, _ = app.dispatch(method="GET", path="/v1/command", now=1)
    assert status == 405 and headers["allow"] == "POST"
    status, _, _ = app.dispatch(method="GET", path="/unknown", now=1)
    assert status == 404


def test_server_defaults_to_loopback_and_remote_bind_is_explicit():
    app = RecoveryHTTPApplicationV106(service=Service())
    server = create_http_server(application=app, host="127.0.0.1", port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()
    with pytest.raises(RuntimeError, match="remote_http_bind_requires_explicit_opt_in"):
        create_http_server(application=app, host="0.0.0.0", port=0)


def test_invalid_configuration_rejected():
    with pytest.raises(ValueError, match="invalid_http_body_limit"):
        RecoveryHTTPApplicationV106(service=Service(), max_body_bytes=1)
    app = RecoveryHTTPApplicationV106(service=Service())
    with pytest.raises(ValueError, match="invalid_http_port"):
        create_http_server(application=app, port=70000)
