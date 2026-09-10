from pathlib import Path
import time
class HealthCheckV60:
    def __init__(self,root): self.root=Path(root)
    def run(self):
        audit=self.root/"runtime"/"v59"/"audit"/"side_effects.jsonl"
        return {"status":"HEALTHY","ts":time.time(),
                "filesystem":self.root.exists(),
                "pep_audit_available":audit.parent.exists(),
                "runtime_state_dir":(self.root/"runtime"/"v58"/"jobs").exists()}
