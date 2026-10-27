# Contract: `secrets.ownership` configuration

Location: under `secrets` in `sandbox.config.json` (project layer) **or** `$SANDBOX_HOME/sandbox.local.yml` machine override — exactly one layer. `_SECRET_KEYS` becomes `{"sources","useProfiles","ownership"}`.

```jsonc
"secrets": {
  "sources": { "personal-file": {"type":"file","path":".secrets/personal.env","format":"dotenv","mcpModes":["keys"]} },
  "ownership": {
    "profiles": {
      "office":   { "domain": "office",
                    "destination": { "endpoint": "https://manager.example", "projectId": "<office-project>",
                                     "environment": "prod", "path": "/",
                                     "readClientId": "<office-read-identity>",
                                     "createClientId": "<office-create-identity>",
                                     "timeoutSeconds": 30, "gateway": "none" } },
      "personal": { "domain": "personal", "destination": { ... different projectId and client ids ... } }
    },
    "entries": [
      { "source": "personal-file", "key": "OPENAI_API_KEY", "profile": "personal",
        "name": "OPENAI_API_KEY", "consumer": "OPENAI_API_KEY", "origin": "source" },
      { "source": "personal-file", "key": "JIRA_TOKEN", "profile": "undecided",
        "name": "JIRA_TOKEN", "consumer": "JIRA_TOKEN", "origin": "source" }
    ]
  }
}
```

Allowed keys: ownership `{profiles, entries}`; profile `{domain, destination}`; destination `{endpoint, projectId, environment, path, readClientId, createClientId, timeoutSeconds, gateway}`; entry `{source, key, profile, name, consumer, origin}`. Unknown key, non-string, secret-like metadata (`_SECRET_LIKE_METADATA`), any field named like a value (`value`, `secret`, `secretValue`, `token`) → `ValueError`.

Gateway is an explicit enum `none` (default) or `cloudflare-access`. It changes
credential format, never enables fallback. With `none`, protected credential input
is the opaque Infisical client secret. With `cloudflare-access`, protected input
is exactly a JSON object with `clientSecret`, `gatewayClientId` and
`gatewayClientSecret`; all three remain only in the native store/private pipe and
worker memory, never in config, argv, audit, output or journals. The worker sends
only the fixed CF-Access-Client-Id/CF-Access-Client-Secret headers. No cookie reuse,
portal policy changes or Cloudflare administration is authorized. Gateway token
revocation is explicitly operator-managed and never implied by derived Infisical
access-token revocation.

Initial ownership source reads and migration support registered file/dotenv
aliases only; other source kinds/formats report `source_unsupported`. Existing
commands keep all of their existing supported source formats. Destination-origin
consumption uses the new separate read grant, never a migration write grant.

## Normalization (`sandbox.secrets.ownership.config.normalize_ownership`)
- Called from `normalize_secret_config` after sources are normalized; returns `{"sources":…, "useProfiles":…, "ownership": <normalized>}` **only when** an `ownership` key existed; otherwise the returned dict is unchanged (no `ownership` key).
- Raises (structural): both layers; unknown keys; bad slug/name/env; `undecided` used as a profile name; profile without domain/destination; unregistered `source` alias; entry with `origin: destination` while destination lacks `readClientId`; profiles of different domains sharing projectId/readClientId/createClientId; `readClientId == createClientId`.
- Never raises for ownership decisions (undecided, multiply assigned, duplicate destination name, other-domain); those are kept verbatim in `entries` order for `classify`.
- Output entries are sorted by `(source, key, profile, name)`; canonical JSON for digests: `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=True)`.

## Digests (`sandbox/secrets/authority.py`)
```python
def ownership_digest(profile_name: str, ownership: Mapping, capability: Literal["read","create"], *, project_root: object) -> str  # "o1-" + sha256 hex
```
Payload: `{"version":1,"kind":"ownership","capability":…,"project_root":<resolved>,"profile":name,"domain":…,"destination":<all fields>,"clientId":<readClientId|createClientId>,"entries":[all entries of this profile (both read and create)],"names":<all sorted mapped destination names>}`.

`_fingerprint(alias, source, profiles, project_root, ownership_consumers=())`: when `ownership_consumers` is non-empty, payload gains `"ownershipConsumers": sorted([{profile,key,consumer,ownershipSha256}])`; when empty the payload and every existing `a1-` digest are byte-identical. `bind_provider_authorities(sources, profiles, *, project_root, ownership=None)` passes consumers for entries with `origin: source` on infisical sources. Regression test: digests for all existing fixtures with `ownership=None` equal pre-change values.

Every ownership consumer includes the hash of the complete canonical normalized
ownership block (metadata only). Changing domain, origin, target, gateway,
identity or any map entry invalidates approval/credential authority. The o1-
authority payload also includes this complete-map hash. No digest is derived
from source values. A matching destination name in two isolated projects is not
an other-domain conflict; duplicate destination targets are compared by full
endpoint/project/environment/path/name tuple.

Read authority covers all mapped destination names so separately approved source
cleanup can verify copied entries while their normal consumption origin remains
source. Normal scoped-run selection still reads a destination only when that
entry explicitly declares destination origin. Cleanup obtains a read grant, not
a write grant. Native credential locator derivation recomputes the complete
canonical authority and validates the selected-name subset; it never trusts an
unvalidated caller-supplied digest.
