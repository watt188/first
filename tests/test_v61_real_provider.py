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


def test_v113_nontransient_canary():
    raise AssertionError("deterministic test defect: do not classify as provider transient")
