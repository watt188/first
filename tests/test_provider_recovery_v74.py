import os
from unittest.mock import patch

from real_provider.contracts_v61 import ProviderRequestV61
from real_provider.openai_compatible_v61 import OpenAICompatibleProviderV61


def _configured():
    return patch.dict(os.environ, {
        "MODEL_API_KEY": "x",
        "MODEL_BASE_URL": "https://example.invalid/v1",
        "MODEL_NAME": "test",
        "MODEL_CIRCUIT_COOLDOWN_SECONDS": "1",
    }, clear=False)


def test_open_circuit_waits_then_allows_recovery_probe():
    provider = OpenAICompatibleProviderV61()
    provider.failure_count = provider.circuit_threshold
    provider.circuit_opened_at = 100.0
    request = ProviderRequestV61("system", "user")
    response = type("R", (), {"read": lambda self: b'{"choices":[{"message":{"content":"ok"}}]}', "__enter__": lambda self: self, "__exit__": lambda *a: None})()
    with _configured(), patch("time.monotonic", side_effect=[100.25, 100.25, 101.0, 101.1]), patch("time.sleep") as sleep, patch("urllib.request.urlopen", return_value=response):
        result = provider.invoke(request)
    assert result.ok is True
    sleep.assert_called_once()
    assert provider.failure_count == 0
    assert provider.circuit_opened_at is None


def test_expired_circuit_needs_no_sleep():
    provider = OpenAICompatibleProviderV61()
    provider.failure_count = provider.circuit_threshold
    provider.circuit_opened_at = 100.0
    with _configured(), patch("time.monotonic", return_value=102.0), patch("time.sleep") as sleep:
        assert provider._circuit_is_open() is False
    sleep.assert_not_called()
    assert provider.failure_count == 0
