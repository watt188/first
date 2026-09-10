import json
import os
import sys
from real_provider.contracts_v61 import ProviderRequestV61
from real_provider.openai_compatible_v61 import OpenAICompatibleProviderV61


def main() -> int:
    provider = OpenAICompatibleProviderV61()
    if not provider.configured():
        print(json.dumps({"status": "SKIPPED", "reason": "provider_not_configured"}))
        return 0

    req = ProviderRequestV61(
        system=(
            "Return ONLY valid JSON with this exact shape: "
            '{"operations": []}. Do not include markdown.'
        ),
        user="Production provider connectivity smoke test. Return no file operations.",
        temperature=0.0,
        max_tokens=80,
    )
    res = provider.invoke(req)
    if not res.ok:
        print(json.dumps({
            "status": "FAILED",
            "model": res.model,
            "latency_ms": res.latency_ms,
            "error": res.error,
        }))
        return 2

    try:
        body = json.loads(res.content)
    except json.JSONDecodeError:
        print(json.dumps({
            "status": "FAILED",
            "reason": "invalid_json",
            "model": res.model,
            "latency_ms": res.latency_ms,
        }))
        return 3

    if body.get("operations") != []:
        print(json.dumps({
            "status": "FAILED",
            "reason": "unexpected_operations",
            "model": res.model,
        }))
        return 4

    print(json.dumps({
        "status": "PASSED",
        "model": res.model,
        "latency_ms": res.latency_ms,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
