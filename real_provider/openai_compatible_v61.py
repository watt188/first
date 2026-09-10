import json
import os
import random
import socket
import time
import urllib.error
import urllib.request

from real_provider.contracts_v61 import ProviderResponseV61


class OpenAICompatibleProviderV61:
    """OpenAI-compatible provider with bounded production resilience.

    V6.7 preserves the V6.1 public interface while adding classified retries,
    exponential backoff with jitter, timeout handling, and a simple circuit
    breaker. Raw provider reasoning and response bodies are never surfaced in
    error strings.
    """

    REQUIRED = ("MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_NAME")
    TRANSIENT_HTTP = {408, 409, 425, 429, 500, 502, 503, 504}

    def __init__(self):
        self.failure_count = 0
        self.circuit_opened_at = None

    def configured(self):
        return all(os.getenv(k) for k in self.REQUIRED)

    @staticmethod
    def _env_int(name, default, minimum=1, maximum=300):
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError:
            value = default
        return max(minimum, min(maximum, value))

    @property
    def max_attempts(self):
        return self._env_int("MODEL_MAX_ATTEMPTS", 3, 1, 6)

    @property
    def timeout_seconds(self):
        return self._env_int("MODEL_TIMEOUT_SECONDS", 30, 1, 120)

    @property
    def circuit_threshold(self):
        return self._env_int("MODEL_CIRCUIT_FAILURE_THRESHOLD", 3, 1, 20)

    @property
    def circuit_cooldown_seconds(self):
        return self._env_int("MODEL_CIRCUIT_COOLDOWN_SECONDS", 30, 1, 300)

    def health(self):
        return {
            "configured": self.configured(),
            "circuit_open": self._circuit_is_open(),
            "failure_count": self.failure_count,
            "max_attempts": self.max_attempts,
            "timeout_seconds": self.timeout_seconds,
        }

    def _circuit_is_open(self):
        if self.circuit_opened_at is None:
            return False
        elapsed = time.monotonic() - self.circuit_opened_at
        if elapsed >= self.circuit_cooldown_seconds:
            self.circuit_opened_at = None
            self.failure_count = 0
            return False
        return True

    def _record_success(self):
        self.failure_count = 0
        self.circuit_opened_at = None

    def _record_failure(self):
        self.failure_count += 1
        if self.failure_count >= self.circuit_threshold:
            self.circuit_opened_at = time.monotonic()

    @staticmethod
    def _final_content(message):
        content = message.get("content") or ""
        if content.strip():
            return content
        # Reasoning traces are never returned wholesale. Recover only an
        # explicitly delimited final answer/code block.
        reasoning = message.get("reasoning_content") or ""
        if not reasoning:
            return ""
        fenced = reasoning.rfind("```")
        if fenced >= 0:
            before = reasoning.rfind("```", 0, fenced)
            if before >= 0:
                block = reasoning[before + 3 : fenced].strip()
                if block.lower().startswith("python"):
                    block = block[6:].lstrip("\r\n ")
                elif block.lower().startswith("py"):
                    block = block[2:].lstrip("\r\n ")
                if block:
                    return block
        for marker in ("FINAL ANSWER:", "FINAL:", "ANSWER:"):
            idx = reasoning.upper().rfind(marker)
            if idx >= 0:
                candidate = reasoning[idx + len(marker) :].strip()
                if candidate:
                    return candidate
        return ""

    @classmethod
    def _classify_exception(cls, exc):
        if isinstance(exc, urllib.error.HTTPError):
            code = int(getattr(exc, "code", 0) or 0)
            return (f"http_{code}", code in cls.TRANSIENT_HTTP)
        if isinstance(exc, (urllib.error.URLError, TimeoutError, socket.timeout)):
            return ("transport_timeout_or_network", True)
        if isinstance(exc, (json.JSONDecodeError, KeyError, IndexError, TypeError)):
            return ("provider_protocol_error", True)
        return (type(exc).__name__, False)

    @staticmethod
    def _backoff_seconds(attempt):
        base = min(4.0, 0.5 * (2 ** (attempt - 1)))
        return base + random.uniform(0.0, min(0.25, base * 0.25))

    def _build_request(self, request):
        base = os.environ["MODEL_BASE_URL"].rstrip("/")
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        payload = {
            "model": os.environ["MODEL_NAME"],
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        return urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + os.environ["MODEL_API_KEY"],
                "Content-Type": "application/json",
            },
            method="POST",
        )

    def invoke(self, request):
        model = os.getenv("MODEL_NAME", "")
        if not self.configured():
            return ProviderResponseV61(False, error="provider_not_configured", model=model)
        if self._circuit_is_open():
            return ProviderResponseV61(False, error="provider_circuit_open", model=model)

        req = self._build_request(request)
        started = time.monotonic()
        last_error = "provider_failed"

        for attempt in range(1, self.max_attempts + 1):
            retryable = False
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                message = body["choices"][0]["message"]
                content = self._final_content(message)
                if not content.strip():
                    last_error = "empty_model_response"
                    retryable = True
                else:
                    self._record_success()
                    return ProviderResponseV61(
                        True,
                        content=content,
                        model=model,
                        latency_ms=int((time.monotonic() - started) * 1000),
                    )
            except Exception as exc:
                last_error, retryable = self._classify_exception(exc)

            if not retryable or attempt >= self.max_attempts:
                break
            time.sleep(self._backoff_seconds(attempt))

        self._record_failure()
        return ProviderResponseV61(
            False,
            error=last_error,
            model=model,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
