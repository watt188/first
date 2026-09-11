from pathlib import Path
from real_provider.runtime_v61 import RealModelProductionRuntimeV61
from real_provider.fake_provider_v61 import FakeProviderV61

def test_provider_path(tmp_path:Path):
    rt=RealModelProductionRuntimeV61(tmp_path,provider=FakeProviderV61(),github_repository_count=1)
    out=rt.writer_task("backend","create status function",["workspace_sample/api.py"])
    assert out["status"]=="DONE"
    assert (tmp_path/"workspace_sample"/"api.py").exists()

def test_non_writer_blocked(tmp_path:Path):
    rt=RealModelProductionRuntimeV61(tmp_path,provider=FakeProviderV61(),github_repository_count=1)
    out=rt.writer_task("security","write code",["workspace_sample/x.py"])
    assert out["status"]=="BLOCKED"

def test_v112_live_auto_recovery_canary():
    # Deliberate deterministic failure used only by the canary PR.
    # The resident recovery worker should dispatch exactly one rerun, persist
    # its recovery record, and ignore the second failed attempt.
    assert False, "V11.2_CANARY_TRANSIENT_SIMULATION"
