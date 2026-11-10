# Quickstart: validate secrets unset and copy

## Automated

Run in a Haiku test helper from the repo root:

```sh
.cli-venv/bin/python -m unittest tests.test_secret_unset_copy tests.test_secret_service tests.test_secret_commands -v
```

Covers: unset happy path, rerun `key_missing`, stale revision, provider refusal,
non-dotenv refusal, invalid key, byte preservation, audit events; copy through a fake
sink (created, updated, already_current, intent conflicts, unknown and unprovisioned
remote, line-break value), SSH sink command construction and response parsing,
canary absence from argv, env, output and audit; `set --skip-unchanged`.

## CLI smoke (temp source only)

With a temporary `SANDBOX_HOME` and a temporary project whose `sandbox.config.json`
registers a dotenv source `fixture` pointing at a 0600 file of fake values:

```sh
./sb secrets inspect --source fixture --project-dir "$TMP_PROJECT"
./sb secrets unset --source fixture EXAMPLE_KEY --project-dir "$TMP_PROJECT" --json
./sb secrets unset --source fixture EXAMPLE_KEY --project-dir "$TMP_PROJECT" --json   # key_missing
```

## Owner, after merge (not run by agents)

1. Update the remote's staged runtime so it knows `--skip-unchanged`:
   `sb remote service migrate <remote> --confirm`, then check
   `runtime_revision_state: match` in `sb remote service status <remote>`.
2. `sb secrets copy --source personal --key EXAMPLE_TOKEN --as TARGET_TOKEN --remote <remote> --target-source personal`
3. Rerun the same command: expect `already_current`.
