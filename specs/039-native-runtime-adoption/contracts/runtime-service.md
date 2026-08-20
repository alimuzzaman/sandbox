# Contract: Native Runtime Service

## Configuration

`wordpressRuntime` is normalized by an explicit common config provider:

```json
{
  "wordpressRuntime": {
    "mode": "managed-native",
    "adapter": "ubuntu-nspawn",
    "php": "8.3",
    "database": "mariadb-10.11",
    "webServer": "nginx",
    "resources": {},
    "egress": []
  }
}
```

Non-Compose activation is valid only from the gitignored machine override. The committed
project may declare compatible requirements, but detection never opts in. Legacy
`server: herd` is imported into the adapter model during compatibility staging and remains
truthfully lower-isolation.

## Required operations

Every adoptable native adapter declares and implements:

- `preflight`, `ensure`, `status`, `health`, `open`
- `wordpress_cli`, bounded `exec`, filesystem and log access
- isolated production/test database handling and `test`
- `apply`/reconcile and conservative `destroy`

Optional operations are independently declared: stop, aggregate logs, snapshots, mail,
Xdebug, subdomain multisite, server switching, and remote deployment.

Capability checks occur before any package, runtime, route, database, or file side effect.

## Operation envelope

```json
{
  "ok": false,
  "operation": "ensure",
  "project_kind": "wordpress",
  "runtime": {
    "mode": "managed_native",
    "adapter": "ubuntu-nspawn",
    "isolation": "managed_container",
    "support_tier": "adoptable"
  },
  "state": "blocked",
  "capabilities": {"required": {}, "optional": {}},
  "backend": null,
  "health": {"ready": false},
  "reason": {"code": "isolation_prerequisite_missing", "message": "Managed-native cannot prove its network boundary; use Compose."},
  "mutated": false
}
```

Stable reason codes include `explicit_selection_required`, `pending_install_confirmation`,
`unsupported_matrix`, `version_unavailable`, `isolation_prerequisite_missing`,
`isolation_drift`, `foreign_collision`, `runtime_mode_change`, `php_version_mismatch`,
`unsupported_capability`, `cleanup_incomplete`, and `incumbent_lower_isolation`.

## C/A/B boundary

Successful C preflight/ensure returns only:

- backend veth address/port or incumbent document-root/backend requirements;
- PHP/database/execution capabilities and health;
- no hostname, DNS, ingress route, or TLS mutation.

The clean-URL orchestration then invokes A/B. Destroy asks A to clean its route separately;
C removes only unchanged C-owned backend/runtime state.

## Cleanup observation

Each resource is observed before it is removed, and every observation answers two
questions: which resource this is, and whether the host still has it. There are
exactly three outcomes, and conflating any two of them breaks cleanup:

| Outcome | Meaning | Cleanup |
|---|---|---|
| `present`, identity matches | the resource exists and is still ours | remove it |
| `present`, identity differs | it exists but changed | stop; retain `owned_state_drifted` |
| `absent` | a successful read found nothing to remove | count as removed, continue |
| observation failed | the question went unanswered | stop; retain `runtime_unavailable` |

`absent` is always a positive read — a registry that listed no such machine, a
profile file that does not exist while the profile list was readable without it.
It is never inferred from a failed read, and never from provisioning bookkeeping:
machine identities are reused across attempts, so a resource this attempt did not
create may still exist from an earlier one and must be observed, not assumed gone.

## Mode immutability

If a registry/runtime record with data exists and requested mode/adapter differs, every
ordinary operation returns `runtime_mode_change` with `mutated: false`. Only a separately
specified export/recreate/import operation may cross modes.

## PHP extension resolution

For a WordPress runtime with `phpExtensions`, preflight normalizes the request and
returns the immutable profile/catalog revision, content digest, safe provenance, and
four execution-plane observations. The request is not considered ready unless web PHP,
WP-CLI, bounded exec, and PHPUnit agree on enabled/disabled state and each requested
version. A missing or unobservable module, exact-version mismatch, unsupported disable,
or plane drift returns `mutated: false` before image/package/runtime work.

For `wordpress@1`, an image capability is satisfied when a fresh plane observation
reports GD or Imagick enabled, or when the selected official Apache/nginx builder
provisions the allowlisted default GD recipe. If neither condition holds, preflight
returns a missing-capability result before mutation.

An active `disabled` request is allowed only for a checked-in manifest entry marked
INI-disableable and only through an owned runtime INI artifact. Every other disable
request returns `unsupported_disable`; it never edits shared/global INI or guesses a
module-specific mechanism.

Official WordPress Apache/nginx images may be extended only through checked-in
allowlisted child-image recipes after digest validation. Custom images, LiteSpeed,
Herd, Valet, and other incumbent native runtimes are validate-only in v1; no shared
host PHP or global INI mutation is permitted. Generic Compose returns
`unsupported_capability` when the field is present.

The extension resolution is additive to the normal operation envelope:

```json
{
  "php_extensions": {
    "profile": "wordpress@1",
    "digest": "sha256:…",
    "state": "ready",
    "planes": { "web": {}, "wp_cli": {}, "exec": {}, "phpunit": {} },
    "provenance": []
  }
}
```

The digest covers normalized requirements, profile/catalog revision, parent image
digest, PHP version, server flavor, platform, and architecture. Cache/build contexts
are recreated under `$SANDBOX_HOME/runtime/build/php-extensions/<digest>/`; apply may
reconcile only web/runtime artifacts and preserves database volumes, uploads, snapshots,
and project files.
