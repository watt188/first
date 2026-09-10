import json
from real_provider.contracts_v61 import ProviderRequestV61
from real_provider.openai_compatible_v61 import OpenAICompatibleProviderV61
from side_effect_governance.pep_v59 import UnifiedPEPV59

class RealProviderAgentV61:
    def __init__(self,root,provider=None):
        self.root=root
        self.provider=provider or OpenAICompatibleProviderV61()
        self.pep=UnifiedPEPV59(root)

    def execute_writer(self,role,goal,allowed_paths):
        req=ProviderRequestV61(
            system=('You are a specialist engineering agent. Return ONLY valid JSON: '
                    '{"operations":[{"path":"...","content":"..."}]}. '
                    'Only write within allowed_paths. No markdown.'),
            user=json.dumps({"role":role,"goal":goal,"allowed_paths":allowed_paths})
        )
        res=self.provider.invoke(req)
        if not res.ok:
            return {"status":"BLOCKED","provider":res.as_dict()}
        try:
            data=json.loads(res.content)
            ops=data.get("operations",[])
        except Exception:
            return {"status":"FAILED","reason":"invalid_provider_json","provider":res.as_dict()}
        applied=[]
        for op in ops:
            path=op.get("path","")
            content=op.get("content","")
            out=self.pep.write_text(role,path,content,allowed_paths)
            if out["status"]!="WRITTEN":
                return {"status":"FAILED","reason":"pep_denied","detail":out}
            applied.append(path)
        return {"status":"DONE","applied":applied,"provider":res.as_dict()}
