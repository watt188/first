from protected_delivery.recovery_live_validation_v110 import SQLiteCanaryRecoveryGatewayV110


def test_canary_gateway_is_durable_and_idempotent(tmp_path):
    path = tmp_path / "canary.sqlite3"
    first = SQLiteCanaryRecoveryGatewayV110(path)
    receipt1 = first.execute_recovery(1, "a" * 40, "retry", 7, "key-1")
    receipt2 = first.execute_recovery(1, "a" * 40, "retry", 7, "key-1")
    assert receipt1 == receipt2

    reopened = SQLiteCanaryRecoveryGatewayV110(path)
    assert reopened.lookup_recovery("key-1") == receipt1


def test_canary_gateway_rejects_identity_conflict(tmp_path):
    gateway = SQLiteCanaryRecoveryGatewayV110(tmp_path / "canary.sqlite3")
    gateway.execute_recovery(1, "a" * 40, "retry", 7, "same-key")
    try:
        gateway.execute_recovery(2, "a" * 40, "retry", 7, "same-key")
    except RuntimeError as exc:
        assert "canary_idempotency_identity_conflict" in str(exc)
    else:
        raise AssertionError("identity conflict was not rejected")
