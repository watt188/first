import json
import os
from pathlib import Path

from protected_delivery.recovery_live_validation_v110 import run_live_validation


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    pr_number = int(os.environ["PR_NUMBER"])
    head_sha = os.environ["PR_HEAD_SHA"]
    token = os.environ.get("GITHUB_TOKEN") or None
    root = Path(os.environ.get("V110_ROOT", ".v110-validation"))
    report = run_live_validation(
        root=root,
        repository_full_name=repository,
        pr_number=pr_number,
        expected_head_sha=head_sha,
        github_token=token,
    )
    output = json.dumps(report, indent=2, sort_keys=True)
    print(output)
    Path("v110-live-validation-report.json").write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
