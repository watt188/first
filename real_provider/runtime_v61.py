from pathlib import Path
from real_provider.agent_v61 import RealProviderAgentV61
from real_provider.openai_compatible_v61 import OpenAICompatibleProviderV61
from production_baseline.preflight_v60 import StartupPreflightV60

class RealModelProductionRuntimeV61:
    def __init__(self,root,provider=None,github_repository_count=0):
        self.root=Path(root)
        self.provider=provider or OpenAICompatibleProviderV61()
        self.preflight=StartupPreflightV60(root).run(github_repository_count)
        self.agent=RealProviderAgentV61(root,self.provider)

    def readiness(self):
        provider_ok=self.provider.configured()
        github_ok=self.preflight["checks"]["github_repository"]["passed"]
        return {
            "local_ready":self.preflight["local_ready"],
            "provider_configured":provider_ok,
            "github_repository":github_ok,
            "production_ready":self.preflight["local_ready"] and provider_ok and github_ok
        }

    def writer_task(self,role,goal,allowed_paths):
        if role not in {"frontend","backend"}:
            return {"status":"BLOCKED","reason":"role_not_writer"}
        if not self.provider.configured():
            return {"status":"BLOCKED","reason":"provider_not_configured"}
        return self.agent.execute_writer(role,goal,allowed_paths)
