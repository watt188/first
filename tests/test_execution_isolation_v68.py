from multi_agent.execution_v68 import IsolatedExecutionV68


FEATURE = '''def normalize_title(text):
    return ' '.join(text.split())


def feature_info():
    return {'name': 'normalize_title', 'version': '6.5'}
'''

TESTS = r'''def run_tests(module):
    assert module.normalize_title("  hello  world  ") == "hello world"
    assert module.normalize_title("hello\t\nworld") == "hello world"
    assert module.normalize_title("   ") == ""
    assert module.normalize_title("") == ""
    assert module.feature_info() == {"name": "normalize_title", "version": "6.5"}
'''


def test_isolated_execution_passes_contract():
    result = IsolatedExecutionV68(timeout_seconds=3, memory_mb=128, cpu_seconds=2).run_feature_tests(FEATURE, TESTS)
    assert result["status"] == "PASSED"
    isolation = result["isolation"]
    assert isolation["python_isolated_mode"] is True
    assert isolation["site_disabled"] is True
    assert isolation["clean_environment"] is True
    assert isolation["working_directory_isolated"] is True


def test_isolated_execution_timeout_is_sanitized():
    feature = '''def normalize_title(text):
    while True:
        pass


def feature_info():
    return {'name': 'normalize_title', 'version': '6.5'}
'''
    result = IsolatedExecutionV68(timeout_seconds=1, memory_mb=128, cpu_seconds=10).run_feature_tests(feature, TESTS)
    assert result == {"status": "FAILED", "error": "execution_timeout"}


def test_child_environment_does_not_inherit_model_secret(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "secret-value")
    env = IsolatedExecutionV68._clean_env()
    assert "MODEL_API_KEY" not in env
    assert "MODEL_BASE_URL" not in env
    assert "MODEL_NAME" not in env


def test_failure_does_not_surface_child_stderr():
    feature = '''def normalize_title(text):
    raise RuntimeError("sensitive-child-detail")


def feature_info():
    return {'name': 'normalize_title', 'version': '6.5'}
'''
    result = IsolatedExecutionV68(timeout_seconds=3, memory_mb=128, cpu_seconds=2).run_feature_tests(feature, TESTS)
    assert result["status"] == "FAILED"
    assert result["error"] == "execution_failed"
    assert "sensitive-child-detail" not in str(result)
