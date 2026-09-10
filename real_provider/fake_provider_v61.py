import json
from real_provider.contracts_v61 import ProviderResponseV61

class FakeProviderV61:
    def configured(self):
        return True

    def invoke(self,request):
        body=json.loads(request.user)
        role=body["role"]
        allowed=body["allowed_paths"]
        path=allowed[0]
        content=("def runtime_status():\n    return {'status':'Ready'}\n" if role=="backend" else "<html><body><h1>Ready</h1></body></html>")
        payload={"operations":[{"path":path,"content":content}]}
        return ProviderResponseV61(True,content=json.dumps(payload),model="fake-v61",latency_ms=1)
