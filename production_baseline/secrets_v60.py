import os
class SecretBoundaryV60:
    NAMES=("MODEL_API_KEY","MODEL_BASE_URL","MODEL_NAME")
    def status(self):
        present={k:bool(os.getenv(k)) for k in self.NAMES}
        return {"configured":all(present.values()),"present":present}
    def export_safe(self):
        s=self.status()
        return {"configured":s["configured"],"keys_present":[k for k,v in s["present"].items() if v]}
