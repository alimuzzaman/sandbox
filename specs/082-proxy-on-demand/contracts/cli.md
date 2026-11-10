# CLI contract: 082 proxy on demand

## Proxy lifetime

| Command | Before | After |
| --- | --- | --- |
| engine restart | proxy restarts itself | proxy stays stopped |
| `sb up` (instance with a `.<tld>` domain) | no proxy call | starts a stopped, already-provisioned proxy; never reloads a running one; no sudo |
| `sb ensure --create` | starts the proxy through secure-at-create | unchanged |
| `sb down` | no proxy call | idle check; may stop the proxy |
| `sb instance delete` | reloads routes if the proxy runs | then idle check; may stop the proxy |
| `sb domains up` / `setup` | starts the proxy | unchanged; refused when remote-only; no start when `disabled` |
| `sb domains down` | stops the proxy | unchanged; with no `docker` binary, reports nothing to stop |

The idle check prints one line only when it stops the proxy:
`stopped the clean-URL proxy: no local instance needs it (sb up starts it again)`.

## Remote-only switch

```yaml
# $SANDBOX_HOME/sandbox.local.yml
controller:
  local_runtime: none   # docker (default) | none
```

Env override: `SANDBOX_LOCAL_RUNTIME=none|docker`.

With `none`:

- `sb ensure` for a Docker instance fails before any state write with code
  `local_runtime_none` and the refusal message below.
- Any local Docker instance Compose call (`up`, `down`, `wp`, `logs`, `exec`, …) exits 1
  with the refusal message, before running `docker`.
- Herd instances work as before; the Docker proxy is never started.
- `sb instances` / `sb web` list Docker rows as not running, with no engine call.
- `sb doctor` needs no local instance. It prints a `Local runtime` section with
  `✓ local runtime: none (remote-only; local Docker checks skipped)`, then the remote
  target, storage and credential sections. JSON adds
  `"local_runtime": {"mode": "none", "source": ..., "remote_only": true}`.
- Remote commands are unchanged.

Refusal message:

```text
this controller is set to remote-only (controller.local_runtime: none), so local Docker
instances are not available. Use --remote, or remove controller.local_runtime from
sandbox.local.yml (and unset SANDBOX_LOCAL_RUNTIME) to run them here.
```

With `docker` (default), `sb doctor` adds `"local_runtime"` to its JSON. When the
`docker` binary is missing it reports `✗ Docker CLI available` with the hint to install
Docker or set `controller.local_runtime: none`, and it skips the container checks
rather than crashing.
