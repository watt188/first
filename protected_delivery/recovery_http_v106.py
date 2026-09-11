import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "10.6"
_JSON = "application/json"


class RecoveryHTTPApplicationV106:
    """Minimal HTTP application boundary over V10.5.

    The transport exposes only a read-only health route and an authenticated
    command route. Recovery commands remain authenticated/replay-protected by
    V10.3/V10.4; this layer only validates HTTP shape, size and JSON framing.
    """

    def __init__(self, *, service, max_body_bytes: int = 65536):
        if not callable(getattr(service, "health", None)):
            raise TypeError("secure_service_health_missing")
        if not callable(getattr(service, "handle", None)):
            raise TypeError("secure_service_handle_missing")
        if not isinstance(max_body_bytes, int) or isinstance(max_body_bytes, bool) or max_body_bytes < 256:
            raise ValueError("invalid_http_body_limit")
        self.service = service
        self.max_body_bytes = max_body_bytes

    @staticmethod
    def _json_bytes(value: dict) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def dispatch(self, *, method: str, path: str, body: bytes = b"", content_type: str = "", now: int) -> tuple[int, dict, bytes]:
        if not isinstance(method, str) or not isinstance(path, str):
            raise TypeError("invalid_http_request_identity")
        if not isinstance(body, (bytes, bytearray)):
            raise TypeError("http_body_must_be_bytes")
        if not isinstance(now, int) or isinstance(now, bool) or now < 0:
            raise ValueError("invalid_http_now")
        method = method.upper()
        headers = {"content-type": _JSON, "cache-control": "no-store", "x-recovery-version": VERSION}

        if method == "GET" and path == "/v1/health":
            if body:
                return 400, headers, self._json_bytes({"status": "ERROR", "error": "health_body_not_allowed"})
            result = self.service.health(now=now)
            return 200, headers, self._json_bytes({"http_version": VERSION, "result": result})

        if method == "POST" and path == "/v1/command":
            if len(body) > self.max_body_bytes:
                return 413, headers, self._json_bytes({"status": "ERROR", "error": "request_body_too_large"})
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type != _JSON:
                return 415, headers, self._json_bytes({"status": "ERROR", "error": "content_type_must_be_json"})
            try:
                envelope = json.loads(bytes(body).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return 400, headers, self._json_bytes({"status": "ERROR", "error": "invalid_request_json"})
            try:
                result = self.service.handle(envelope, now=now)
            except (TypeError, ValueError, RuntimeError) as exc:
                code = str(exc) or "authenticated_command_rejected"
                return 403, headers, self._json_bytes({"status": "REJECTED", "error": code})
            return 200, headers, self._json_bytes({"http_version": VERSION, "result": result})

        if path in {"/v1/health", "/v1/command"}:
            return 405, {**headers, "allow": "GET" if path == "/v1/health" else "POST"}, self._json_bytes({"status": "ERROR", "error": "method_not_allowed"})
        return 404, headers, self._json_bytes({"status": "ERROR", "error": "route_not_found"})


def make_handler(application: RecoveryHTTPApplicationV106):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RecoveryHTTP/10.6"

        def log_message(self, format, *args):
            return

        def _serve(self):
            length_raw = self.headers.get("Content-Length", "0")
            try:
                length = int(length_raw)
            except ValueError:
                self.send_error(400)
                return
            if length < 0 or length > application.max_body_bytes:
                body = application._json_bytes({"status": "ERROR", "error": "request_body_too_large"})
                self.send_response(413)
                self.send_header("Content-Type", _JSON)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            raw = self.rfile.read(length) if length else b""
            status, headers, body = application.dispatch(
                method=self.command,
                path=self.path,
                body=raw,
                content_type=self.headers.get("Content-Type", ""),
                now=int(time.time()),
            )
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _serve
        do_POST = _serve
        do_PUT = _serve
        do_PATCH = _serve
        do_DELETE = _serve

    return Handler


def create_http_server(*, application: RecoveryHTTPApplicationV106, host: str = "127.0.0.1", port: int = 0, allow_remote: bool = False):
    if not isinstance(host, str) or not host.strip():
        raise ValueError("invalid_http_host")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("invalid_http_port")
    host = host.strip()
    if not allow_remote and host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("remote_http_bind_requires_explicit_opt_in")
    return ThreadingHTTPServer((host, port), make_handler(application))
