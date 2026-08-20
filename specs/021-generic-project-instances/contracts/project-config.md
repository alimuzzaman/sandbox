# Contract: Generic Project Configuration

## Backward compatibility

Any current `sandbox.config.*` without `kind` is interpreted exactly as `kind: "wordpress"`. `.wp-env.json` always resolves to WordPress. WordPress plugin slug validation is applied only after WordPress kind selection.

## Minimal explicit Compose project

```json
{
  "kind": "compose",
  "name": "alimuzzaman.me",
  "runtime": {
    "composeFile": "compose.yaml",
    "service": "web",
    "containerPort": 4321,
    "healthPath": "/"
  }
}
```

Required behavior:

- Unknown top-level kinds fail with supported values.
- `composeFile` resolves relative to the project root and cannot escape it.
- The declared service and port are validated against `docker compose config` before mutation.
- Generic configuration does not inherit user-global WordPress plugins, themes, mappings, versions, admin credentials, database, mail, multisite, or WP configuration.
- A per-label configuration may change safe adapter settings but cannot change `kind`.
- Secrets remain in existing machine-local secret/override locations or project-owned Compose mechanisms; Sandbox never copies them into its registry or generated overlay.

## Astro initialization output

The Astro preset writes an explicit Compose descriptor. When no project Compose file is selected, it proposes `sandbox.compose.yml` containing a conventional Node development service, source bind mount, reviewed package command, `0.0.0.0` bind, and declared internal port. The generated file is project-owned and reviewable; ensure does not regenerate it silently.

## WordPress PHP extension requirements (v1)

`phpExtensions` is an additive field of the WordPress descriptor. Its absence has
exactly the legacy meaning: Sandbox performs no extension validation, image build,
package resolution, or new readiness check. The field is not part of the generic
Compose descriptor.

```json
{
  "phpVersion": "8.3",
  "phpExtensions": {
    "profile": "wordpress@1",
    "extensions": {
      "gd": true,
      "imagick": { "state": "enabled", "version": "3.7.0" },
      "xdebug": false
    }
  }
}
```

The accepted shorthand and canonical form are:

| Input | Canonical meaning |
|---|---|
| `true` | `{ "state": "enabled" }` at the resolved/catalogued runtime version |
| `"VERSION"` | `{ "state": "enabled", "version": "VERSION" }` |
| `false` | `{ "state": "disabled" }` |
| `{ "state": "enabled|disabled", "version": "VERSION" }` | unchanged after validation |

`VERSION` is either an exact extension version, `X.Y.*`, or `php` (the active PHP
major/minor); no other wildcard, range, package name, URL, shell fragment, or INI
path is accepted. Unknown extension names, keys, profiles, states, malformed
versions, and contradictory profile entries fail before any runtime, package, image,
database, or filesystem mutation. Runtime readiness compares the version reported by
the extension itself (for PHP modules, `ReflectionExtension::getVersion()`); package
or artifact versions remain separate provenance fields.

`wordpress@1` is an immutable profile, not a rolling alias. It requires `curl`,
`dom`, `exif`, `fileinfo`, `hash`, `json`, `mbstring`, `mysqli`, `openssl`, `pcre`,
and `xml`, plus an image capability (`gd` or `imagick`) that MUST be satisfied by a
fresh runtime observation or an allowlisted official-image build. It recommends
`intl`, `sodium`, `zip`, and `opcache`. Explicit entries are hard requirements;
profile-required capabilities cannot be disabled unless no profile is selected (the
image-capability pair may choose either member, but cannot disable both). Omitting both
image names from config is allowed only when the selected runtime can observe one
already enabled or safely auto-provision the allowlisted default GD capability.

`false` is an active disable request only when the checked-in extension manifest marks
that module as INI-disableable. Otherwise it is a validation-only assertion and MUST
fail with `unsupported_disable` if the observed module is enabled; Sandbox MUST never
edit an unknown/global INI file or infer a disable mechanism.

For `kind: "compose"`, a present `phpExtensions` field fails closed with a stable
unsupported-capability result in v1; Sandbox does not mutate a project-owned image or
Compose file. A future PHP-specific adapter must be separately specified before this
field is accepted for generic projects.
