import pytest

from protected_delivery.recovery_worker_v111 import _validate_event, _derive_secret

SHA = "a" * 40


def event(**run_overrides):
    run = {
        "id": 123,
        "name": "v61-real-provider",
        "conclusion": "failure",
        "run_attempt": 1,
        "head_sha": SHA,
        "pull_requests": [{"number": 56}],
    }
    run.update(run_overrides)
    return {"workflow_run": run}


def test_provider_failure_is_selected():
    target = _validate_event(event())
    assert target == {
        "workflow": "v61-real-provider",
        "conclusion": "failure",
        "pr_number": 56,
        "run_id": 123,
        "head_sha": SHA,
    }


def test_second_attempt_is_not_retried_again():
    assert _validate_event(event(run_attempt=2)) is None


def test_non_provider_workflow_is_not_auto_retried():
    assert _validate_event(event(name="ci")) is None


def test_success_is_no_action():
    assert _validate_event(event(conclusion="success")) is None


def test_ambiguous_pr_identity_is_no_action():
    assert _validate_event(event(pull_requests=[])) is None
    assert _validate_event(event(pull_requests=[{"number": 1}, {"number": 2}])) is None


def test_invalid_identity_fails_closed():
    with pytest.raises(ValueError, match="worker_invalid_run_id"):
        _validate_event(event(id=0))
    with pytest.raises(ValueError, match="worker_invalid_head_sha"):
        _validate_event(event(head_sha="bad"))


def test_runtime_secret_is_deterministic_and_32_bytes():
    first = _derive_secret("token")
    second = _derive_secret("token")
    assert first == second
    assert len(first) == 32
    assert first != _derive_secret("other")
