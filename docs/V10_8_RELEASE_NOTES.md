# V10.8 Release Notes

V10.8 adds a concrete GitHub-backed authoritative pull-request head resolver and composes it with V10.7's fail-closed exact-head recovery boundary.

Key changes:
- live HTTPS read of `pulls/{pr_number}` from the configured GitHub API;
- strict validation of repository identity, API base, timeout, JSON response and lowercase 40-character head SHA;
- optional runtime-only bearer token support;
- fail-closed handling for transport, HTTP, timeout and malformed-response failures;
- composed recovery command overwrites untrusted caller `current_head_sha` with the live GitHub head before delegation;
- deterministic CI coverage for both resolver and composition behavior.

This release does not claim distributed trust, GitHub service compromise resistance, or provider-independent exactly-once physical side effects.
