import json
import os
import time
import urllib.request
from real_provider.contracts_v61 import ProviderResponseV61

class OpenAICompatibleProviderV61:
    REQUIRED=("MODEL_API_KEY","MODEL_BASE_URL","MODEL_NAME")

    def configured(self):
        return all(os.getenv(k) for k in self.REQUIRED)

    @staticmethod
    def _final_content(message):
        content=message.get("content") or ""
        if content.strip():
            return content
        # Some OpenAI-compatible reasoning providers spend the whole token budget
        # in reasoning_content and leave content empty. Recover only an explicitly
        # delimited final answer/code block; never expose or forward the full trace.
        reasoning=message.get("reasoning_content") or ""
        if not reasoning:
            return ""
        fenced=reasoning.rfind("```")
        if fenced >= 0:
            before=reasoning.rfind("```",0,fenced)
            if before >= 0:
                block=reasoning[before+3:fenced].strip()
                if block.lower().startswith("python"):
                    block=block[6:].lstrip("\r\n ")
                elif block.lower().startswith("py"):
                    block=block[2:].lstrip("\r\n ")
                if block:
                    return block
        for marker in ("FINAL ANSWER:","FINAL:","ANSWER:"):
            idx=reasoning.upper().rfind(marker)
            if idx >= 0:
                candidate=reasoning[idx+len(marker):].strip()
                if candidate:
                    return candidate
        return ""

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
            content=self._final_content(message)
            return ProviderResponseV61(True,content=content,model=os.environ["MODEL_NAME"],latency_ms=int((time.time()-started)*1000))
        except Exception as e:
            return ProviderResponseV61(False,error=type(e).__name__,model=os.getenv("MODEL_NAME",""),latency_ms=int((time.time()-started)*1000))
