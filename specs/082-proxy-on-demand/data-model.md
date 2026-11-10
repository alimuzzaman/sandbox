# Data model: 082 proxy on demand

## LocalRuntimeSelection (`sandbox/config/local_runtime.py`)

| Field | Type | Rule |
| --- | --- | --- |
| `mode` | `"docker" \| "none"` | default `docker`; any other value raises `LocalRuntimeConfigError` |
| `source` | `"environment" \| "machine_override" \| "default"` | first non-empty layer wins |
| `remote_only` | derived | `mode == "none"` |

Layers: `SANDBOX_LOCAL_RUNTIME` → `controller.local_runtime` in `sandbox.local.yml` →
default. A non-object `controller` block is an error.

## ProxyDemandFacts → ProxyDemandDecision (`sandbox/application/proxy_demand.py`)

Facts (`None` = could not be read):

| Field | Type |
| --- | --- |
| `proxy_running` | `bool \| None` |
| `running_instances` | `tuple[str, ...] \| None` (running `sandbox-*` Compose projects other than the proxy) |
| `wake_routes` | `int \| None` (activation catalog routes) |
| `clean_urls_disabled` | `bool` |

Decision, first match wins:

| Condition | Action | Reason |
| --- | --- | --- |
| `proxy_running is False` | none | `proxy_not_running` |
| `proxy_running is None` | keep | `proxy_state_unknown` |
| `running_instances is None` | keep | `instances_unknown` |
| `running_instances` non-empty | keep | `instances_running` |
| `clean_urls_disabled` | stop | `clean_urls_disabled` |
| `wake_routes is None` | keep | `wake_routes_unknown` |
| `wake_routes > 0` | keep | `wake_routes` |
| otherwise | stop | `idle` |

## stop_proxy_if_idle result (`sandbox/core/_domains.py`)

`{"action": "stop"|"keep"|"none", "reason": str, "stopped": bool}`. Reasons beyond the
decision table: `local_runtime_none`, `adopted_ingress`, `docker_unavailable`,
`start_in_progress`, `stop_failed`.

## Proxy lock

`runtime/proxy/.on-demand.lock` (under `$SANDBOX_HOME`). Shared by starts, exclusive and
non-blocking for the idle stop.
