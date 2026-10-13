# Validation Quickstart

This document is an acceptance guide, not authorization to contact a live provider,
credential store, remote machine, Docker host, or Tailscale peer. Use only synthetic values.
Never paste credentials into shell arguments, chat, logs, fixtures, or repository files.

## 1. Local static and synthetic verification

After all implementation and documentation edits are complete, exercise the supported CLI
with temporary synthetic configuration and injected provider/store fakes. Confirm:

- existing file sources produce their pre-feature results;
- provider source info and keys perform zero authentication and value reads;
- an exact resolve performs one login and one exact GET with expansion/import disabled;
- redirect, proxy environment, alternate endpoint, listing, search, and selector override
  attempts are refused;
- locked, missing, unavailable, or prompting credential stores fail closed;
- every failure maps to a stable secret-free code and audit intent precedes callbacks;
- the fixed child receives only its declared minimal environment.

Only after observing those workflows, add focused regressions and run the relevant modules:

```text
PYTHONPATH=. python3 -m unittest tests.test_secret_config
PYTHONPATH=. python3 -m unittest tests.test_secret_credentials
PYTHONPATH=. python3 -m unittest tests.test_secret_infisical
PYTHONPATH=. python3 -m unittest tests.test_secret_service
PYTHONPATH=. python3 -m unittest tests.test_secret_commands
PYTHONPATH=. python3 -m unittest tests.test_secret_mcp
```

Record individual durations. Any test at or above 5 seconds is a failure to fix, not an
accepted slow test. Captured subprocesses must use
`tests.subprocess_support.synthetic_environment` or `run_test_process`.

### Local synthetic observation (2026-10-13)

With an isolated temporary project and `SANDBOX_HOME`, placeholder provider metadata, an
injected credential store/provider, and no live endpoint or OS-store access:

- singleton `keys` inspection returned only `API_TOKEN` and performed no provider resolve;
- a selector override and provider exact-length request returned stable JSON refusals;
- an arbitrary child request was refused before credential or provider access;
- one fixed profile recorded audit intent before its single synthetic resolution, delivered
  the value only to the bounded child, and recorded a secret-free outcome;
- ordinary repository-wrapper commands continued to use the available system dependencies;
  the provider credential path could not create `.cli-venv` because this Linux host lacks
  the `python3-venv` OS package. The supported module fallback exercised the synthetic
  secret CLI surface; provider dependency-bootstrap acceptance remains unavailable until
  that prerequisite is installed.

Focused gates passed 291 tests across `tests.test_cli` and the six secret modules. Their
slowest individual cases were 3.826 seconds (`tests.test_cli`) and 1.542 seconds
(`tests.test_secret_commands`); no focused test reached 5 seconds.

The one permitted repository-wide discovery run was attempted on 2026-10-13 and exited
zero after about 13 minutes, but it did not emit the required final `Ran`/`OK` or 20-slowest
summary. The run is therefore `acceptance_unknown`, was not repeated, and does not satisfy
the final-suite gate.

These observations are local synthetic evidence only. They do not establish Infisical,
Tailscale, Linux Secret Service, or macOS Keychain behavior.

### Hardening observation (2026-10-14)

After authority binding and process isolation were added, an isolated supported CLI
`secrets inspect --mode keys --json` run returned only the configured synthetic singleton
in 0.915 seconds. It did not contact an OS credential store or provider endpoint.

Focused gates passed 239 tests across `tests.test_secret_config`,
`tests.test_secret_credentials`, `tests.test_secret_infisical`,
`tests.test_secret_provider_broker`, `tests.test_secret_service`,
`tests.test_secret_session`, `tests.test_secret_commands`, and
`tests.test_secret_mcp`. The slowest case was the synthetic unreachable-group cleanup at
4.024 seconds; no focused test reached five seconds. The first observation of that case was
5.537 seconds under the old session cleanup bound, so the bound was reduced and the module
was rerun successfully. The repository-wide suite was not repeated because the one allowed
attempt described above had already been consumed.

## 2. Separately authorized provider acceptance

Prerequisites supplied by the operator:

- a reviewed community self-hosted Infisical release at a verified HTTPS origin reachable
  over Tailscale;
- two synthetic provider projects with distinct Viewer identities;
- distinct Linux and macOS Universal Auth credentials for Project A;
- existing unlocked approved OS credential stores on both controllers;
- one synthetic exact item and fixed child that proves receipt without printing the value.

Using the final documented `sb secrets` syntax, place each controller's credential through
the protected local action. Do not copy a credential between controllers. Register only
non-secret metadata in project configuration.

Run the same fixed profile from Linux and macOS and retain only secret-free envelopes and
audit evidence. Then prove these negative cases:

1. Project A cannot retrieve Project B's item.
2. Request-time project, environment, path, key, endpoint, and child overrides are refused.
3. Listing, search, reveal, export, writes, deletion, rotation, migration, and identity
   administration are unavailable through CLI and MCP.
4. Revoking Linux's credential denies Linux while macOS continues using its independent
   credential.
5. Locked/missing stores, Tailscale loss, DNS failure, TLS failure, 401/403, 404, 429, 5xx,
   malformed JSON, oversized response, and timeout produce the contracted outcome without
   retry or fallback.
6. Source info and key inspection produce zero secret reads.
7. Repository, configuration, argv, parent environment, routine output, logs, audit, and
   retained job evidence contain zero copies of the synthetic item, bootstrap secrets, and
   access tokens.
8. When the provider refuses creation of another distinct identity or independently
   revocable controller credential because capacity is unavailable, leave the new
   registration without that required unique identifier/credential and prove Sandbox
   refuses it before authentication or item retrieval. Do not share an existing identity
   or credential to make the test pass.

The acceptance record must contain only a schema version, tested Infisical release,
observation time, private-route class, built-in Viewer result, negative cross-project
result, distinct-controller result, capacity-boundary result, and pass/fail/unknown
outcomes. It must not contain an
endpoint credential, access token, secret value, provider response, OS-store object ID, or
raw network address. A successful Tailscale route is a deployment observation, not secret
authorization; the credential and provider role must still be required.

Empty, malformed, or incomplete evidence is `acceptance_unknown`, never success. Provider
deployment, migration, rotation, backup, deletion, and remote Sandbox update remain outside
this acceptance and require their own authorization.
