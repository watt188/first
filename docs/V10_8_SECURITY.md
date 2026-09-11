# V10.8 Security Boundary

V10.8 treats the GitHub pull-request API response as the authoritative source for the current PR head. The local caller cannot choose `current_head_sha`; V10.7/V10.8 replace it with the live value before downstream recovery execution.

Operational requirements:
- use HTTPS only;
- inject GitHub credentials at runtime when private-repository access is required;
- do not log or persist bearer tokens;
- fail closed if GitHub cannot be reached or returns malformed data;
- retain V10.3/V10.4 authentication and replay protection for command submission.

Residual limits remain: trust in GitHub and network endpoint integrity, single-host replay state, and downstream idempotency guarantees are outside this resolver's authority.
