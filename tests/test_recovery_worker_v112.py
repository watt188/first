import pytest

from protected_delivery.recovery_worker_v112 import _derive_secret


def test_v112_secret_is_stable_and_scoped():
    assert _derive_secret("token") == _derive_secret("token")
    assert len(_derive_secret("token")) == 32
    assert _derive_secret("token") != _derive_secret("other")


def test_v112_secret_rejects_empty_token():
    with pytest.raises(ValueError, match="worker_token_missing"):
        _derive_secret("")
