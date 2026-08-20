# Quickstart: reclaiming a filling host

## 1. See the whole host

```sh
./sb resources status --remote scaleway-sandbox --deep --budget 180
```

Read three things: the per-class table (PROTECTED / LIVE / STOPPED / REGONLY /
BASE / ORPHAN with counts and bytes), the tier totals line, and the capacity
pressure line. If it says `PARTIAL`, the numbers below it are what could be
measured — not the whole host.

`--fast` answers from the cached directory index in seconds but cannot prove
container state, so it reports `UNKNOWN` and zero candidates. Use it to check
capacity, not to decide a deletion.

## 2. Preview

```sh
./sb resources plan --remote scaleway-sandbox --tier safe
```

Nothing on the host changes. Read the `skipped` list as carefully as the
candidate list: a protection rule always appears there with its reason, so you
can see *why* something you expected is absent.

Compare tiers before choosing:

```sh
./sb resources plan --remote scaleway-sandbox --tier all
```

`safe` takes orphaned and released workspaces. `tmp` adds disposable runtime
scratch. `all` adds expired stopped workspaces and expired one-shot base
deployments — review that one line by line.

## 3. Reclaim

```sh
./sb resources cleanup --remote scaleway-sandbox --tier safe --confirm
```

Note the `manifest:` path in the output. If a run is interrupted, re-run the
same tier — already-removed candidates report `already_absent` — or resume the
exact reviewed set with `--plan-id <id> --confirm`.

## 4. Answer "what happened to X?"

```sh
./sb exec --remote scaleway-sandbox -- \
  grep -F '/deploy-src/x-workspace-8fd1' \
  "$SANDBOX_HOME/runtime/resources/deletions/"*.jsonl
```

Every removal has an `intent` record (path, bytes, class, tier, reason, trigger,
time) written before it happened and an `outcome` record after it.

## 5. Stop it recurring

As an agent, when you finish with a workspace:

```sh
./sb workspace release my-task-workspace-8fd1 --remote scaleway-sandbox
```

When you need one longer than the 7-day default:

```sh
./sb workspace ttl my-task-workspace-8fd1 --ttl 14d --remote scaleway-sandbox
```

Periodically:

```sh
./sb workspace reap --remote scaleway-sandbox --dry-run
./sb workspace reap --remote scaleway-sandbox --confirm
```

## Things the tool will refuse, on purpose

- Any volume that is not a workspace-scoped `node_modules` volume — including
  ones the engine reports as unused. `docker volume prune` on a Sandbox host
  destroys live site databases and uploads; that is why this exists.
- Anything under `deploy-src/hosts/` or belonging to a registered hosted site.
- A candidate whose modification time advanced since the plan.
- A removal that only partly succeeded: it is reported `failed` with
  `partial_removal_detected`, and its bytes are not counted as reclaimed.
