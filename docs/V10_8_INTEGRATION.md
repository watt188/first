# V10.8 Integration Contract

Use `GitHubAuthoritativeRecoveryCommandV108` when the recovery control plane must bind recover operations to the current live GitHub pull-request head. The GitHub bearer token, when needed, must be injected at runtime from the deployment environment or secret manager and must not be committed to the repository.

The resolver performs one live GitHub PR read immediately before the downstream recovery command is invoked. If GitHub is unavailable, the response is malformed, or the expected head differs from the live head, recovery fails closed before downstream mutation.

This layer strengthens head freshness and caller-trust boundaries only. It does not create distributed consensus and does not upgrade downstream providers to physical exactly-once semantics.
