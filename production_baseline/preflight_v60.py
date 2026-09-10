from pathlib import Path
from production_baseline.secrets_v60 import SecretBoundaryV60
class StartupPreflightV60:
    def __init__(self,root): self.root=Path(root)
    def run(self,github_repository_count=0):
        checks={
          "runtime_root":{"passed":self.root.exists()},
          "pep_module":{"passed":(self.root/"side_effect_governance"/"pep_v59.py").exists()},
          "production_runtime":{"passed":(self.root/"production_runtime"/"runtime_v58.py").exists()},
          "audit_dir":{"passed":(self.root/"runtime"/"v59"/"audit").exists()},
          "external_model":{"passed":SecretBoundaryV60().status()["configured"]},
          "github_repository":{"passed":github_repository_count>0}
        }
        blockers=[k for k,v in checks.items() if not v["passed"]]
        local_blockers=[x for x in blockers if x not in {"external_model","github_repository"}]
        return {"checks":checks,"local_ready":not local_blockers,
                "production_ready":not blockers,"blockers":blockers}
