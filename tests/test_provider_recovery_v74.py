import os
from unittest.mock import patch

from real_provider.openai_compatible_v61 import OpenAICompatibleProviderV61


def _configured():
    return patch.dict(os.environ, {
        "MODEL_API_KEY": "x",
        "MODEL_BASE_URL": "https://example.invalid/v1",
        "MODEL_NAME": "test",
        "MODEL_CIRCUIT_COOLDOWN_SECONDS": "1",
    }, clear=False)


def test_open_circuit_exposes_bounded_retry_after():
    provider = OpenAICompatibleProviderV61()
    with _configured(), patch("time.monotonic", return_value=100.25):
        provider.failure_count = provider.circuit_threshold
        provider.circuit_opened_at = 100.0
        assert provider._circuit_is_open() is True
        remaining = provider.circuit_retry_after_seconds()
    assert 0 < remaining <= 1.0


def test_expired_circuit_recovers_without_sleep():
    provider = OpenAICompatibleProviderV61()
    with _configured(), patch("time.monotonic", return_value=102.0), patch("time.sleep") as sleep:
        provider.failure_count = provider.circuit_threshold
        provider.circuit_opened_at = 100.0
        assert provider._circuit_is_open() is False
    sleep.assert_not_called()
    assert provider.failure_count == 0
    assert provider.circuit_opened_at is None


def test_health_reports_retry_after_without_secret_material():
    provider = OpenAICompatibleProviderV61()
    with _configured(), patch("time.monotonic", return_value=100.5):
        provider.failure_count = provider.circuit_threshold
        provider.circuit_opened_at = 100.0
        health = provider.health()
    assert health["circuit_open"] is True
    assert 0 < health["circuit_retry_after_seconds"] <= 1.0
    assert "MODEL_API_KEY" not in health
