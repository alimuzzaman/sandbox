# Hermes State Sync Quickstart

## Configure

Associate the remote with the private repository:

```bash
./sb hermes state setup --remote scaleway-sandbox \
  --repo https://github.com/alimuzzaman/hermes-agent-state.git --confirm --json
```

## Restore on setup

```bash
./sb hermes setup --remote scaleway-sandbox --json
```

The command reports the restored revision and automatically publishes any changed
allowlisted state. Provider OAuth and Git credentials still require explicit
operator authentication.

## Publish changes

```bash
./sb hermes state sync --remote scaleway-sandbox --confirm --json
```

Verify that exactly one commit is created when allowed state changes, and that
credential/session/log paths are absent from the repository tree.

## Rebuild validation

On a disposable remote, configure the same repository, run setup, and confirm the
profile, Sandbox integration, policy, and memory files are restored without any
provider credential files.

## Recorded verification

- Live `scaleway-sandbox`: setup restored from the configured repository and
  `state sync --confirm` pushed commit `6d7863f`.
- Local Hermes tests: 84 passed.
- Full test suite: 414 passed, 1 existing skip.
