# Setup Contract: Hermes Worker Routing

`sb hermes setup --remote <name>` must converge the following non-secret state:

- Root model remains Spark with the configured provider.
- Direct delegation targets Terra, permits one child, and prevents nested orchestration.
- Task-board configuration enables gateway dispatch when the operator later installs and starts the gateway; it does not activate the service itself.
- Profiles `luna`, `terra`, and `sol` exist with their declared model, reasoning level, description, and role policy.
- Root policy has exactly one Sandbox-owned routed-worker block.
- Luna has `safe` and `file` toolsets and a no-write behavioral policy.

Setup must not authenticate a provider, handle credentials, install/start a gateway, or contact a messaging platform.
