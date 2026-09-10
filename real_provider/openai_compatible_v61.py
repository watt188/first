import json
import os
import time
import urllib.request
from real_provider.contracts_v61 import ProviderResponseV61

class OpenAICompatibleProviderV61:
    REQUIRED=("MODEL_API_KEY","MODEL_BASE_URL","MODEL_NAME")

    def configured(self):
        return all(os.getenv(k) for k in self.REQUIRED)

    def invoke(self, request):
        if not self.configured():
            return ProviderResponseV61(False,error="provider_not_configured")
        base=os.environ["MODEL_BASE_URL"].rstrip("/")
        url=base if base.endswith("/chat/completions") else base+"/chat/completions"
        payload={
            "model":os.environ["MODEL_NAME"],
            "messages":[
                {"role":"system","content":request.system},
                {"role":"user","content":request.user}
            ],
            "temperature":request.temperature,
            "max_tokens":request.max_tokens
        }
        req=urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization":"Bearer "+os.environ["MODEL_API_KEY"],
                "Content-Type":"application/json"
            },
            method="POST"
        )
        started=time.time()
        try:
            with urllib.request.urlopen(req,timeout=30) as resp:
                body=json.loads(resp.read().decode("utf-8"))
            message=body["choices"][0]["message"]
            content=message.get("content") or message.get("reasoning_content") or ""
            return ProviderResponseV61(True,content=content,model=os.environ["MODEL_NAME"],latency_ms=int((time.time()-started)*1000))
        except Exception as e:
            return ProviderResponseV61(False,error=type(e).__name__,model=os.getenv("MODEL_NAME",""),latency_ms=int((time.time()-started)*1000))
