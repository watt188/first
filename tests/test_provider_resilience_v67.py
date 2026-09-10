import json
import os
import socket
import urllib.error
from unittest.mock import patch

from real_provider.contracts_v61 import ProviderRequestV61
from real_provider.openai_compatible_v61 import OpenAICompatibleProviderV61


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def req():
    return ProviderRequestV61(system="s", user="u", temperature=0, max_tokens=32)


def configure():
    os.environ["MODEL_API_KEY"] = "secret-for-test"
    os.environ["MODEL_BASE_URL"] = "https://provider.example/v1"
    os.environ["MODEL_NAME"] = "test-model"
    os.environ["MODEL_MAX_ATTEMPTS"] = "3"
    os.environ["MODEL_CIRCUIT_FAILURE_THRESHOLD"] = "2"
    os.environ["MODEL_CIRCUIT_COOLDOWN_SECONDS"] = "30"


def test_retry_429_then_success():
    configure()
    provider = OpenAICompatibleProviderV61()
    http_error = urllib.error.HTTPError("https://provider.example", 429, "rate", {}, None)
    success = FakeResponse({"choices": [{"message": {"content": "ok"}}]})
    with patch("urllib.request.urlopen", side_effect=[http_error, success]) as call, patch("time.sleep") as sleep, patch("random.uniform", return_value=0):
        result = provider.invoke(req())
    assert result.ok is True
    assert result.content == "ok"
    assert call.call_count == 2
    sleep.assert_called_once_with(0.5)
    assert provider.failure_count == 0


def test_retry_empty_content_then_success():
    configure()
    provider = OpenAICompatibleProviderV61()
    empty = FakeResponse({"choices": [{"message": {"content": ""}}]})
    success = FakeResponse({"choices": [{"message": {"content": "final"}}]})
    with patch("urllib.request.urlopen", side_effect=[empty, success]), patch("time.sleep"), patch("random.uniform", return_value=0):
        result = provider.invoke(req())
    assert result.ok is True
    assert result.content == "final"


def test_non_retryable_401_stops_immediately():
    configure()
    provider = OpenAICompatibleProviderV61()
    error = urllib.error.HTTPError("https://provider.example", 401, "no", {}, None)
    with patch("urllib.request.urlopen", side_effect=error) as call, patch("time.sleep") as sleep:
        result = provider.invoke(req())
    assert result.ok is False
    assert result.error == "http_401"
    assert call.call_count == 1
    sleep.assert_not_called()


def test_network_timeout_retries_and_sanitizes_error():
    configure()
    provider = OpenAICompatibleProviderV61()
    with patch("urllib.request.urlopen", side_effect=socket.timeout("sensitive detail")) as call, patch("time.sleep"), patch("random.uniform", return_value=0):
        result = provider.invoke(req())
    assert result.ok is False
    assert result.error == "transport_timeout_or_network"
    assert "sensitive" not in result.error
    assert call.call_count == 3


def test_circuit_opens_after_failed_invocations():
    configure()
    os.environ["MODEL_MAX_ATTEMPTS"] = "1"
    provider = OpenAICompatibleProviderV61()
    error = urllib.error.HTTPError("https://provider.example", 503, "down", {}, None)
    with patch("urllib.request.urlopen", side_effect=error) as call:
        first = provider.invoke(req())
        second = provider.invoke(req())
        third = provider.invoke(req())
    assert first.ok is False and second.ok is False
    assert third.ok is False
    assert third.error == "provider_circuit_open"
    assert call.call_count == 2
    assert provider.health()["circuit_open"] is True


def test_reasoning_trace_not_exposed_wholesale():
    message = {"content": "", "reasoning_content": "private chain text without an explicit final marker"}
    assert OpenAICompatibleProviderV61._final_content(message) == ""
