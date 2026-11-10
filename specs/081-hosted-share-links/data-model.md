# Data model: Hosted share links

All gate records are JSON files on the `sandbox-login` tmpfs, mode 0600, written
atomically, and named by the SHA-256 of their secret or by their public id. No record
holds a token or a session secret.

## ShareDeclaration (manifest, normalized)

| Field | Type | Rule |
|---|---|---|
| `hostname` | str | A served alias of the target, never the primary or a wildcard |
| `private` | bool | Default false |
| `agent_mint` | bool | Default false |
| `stack_sign_in` | list[str] | Optional names of `autologin.entries` of mode `magic_link` or `inbox_gate` |
| `image`, `enabled_until`, `allowed_host_suffixes`, `session_ttl_seconds` | | Required (except `session_ttl_seconds`, default 43200) when `autologin.login_service` is absent. Refused when it is present: it holds them |

The block compiles into a login-service declaration with one reserved `site_gate`
entry named `share`. Its upstream is `http://<compose.service>:<container_port>`.

## Link (`sandbox.share-link.v2`), `sites/<entry>/links/<token_sha256>.json`

| Field | Type | Rule |
|---|---|---|
| `entry` | str | The `site_gate` entry |
| `id` | str | `l-` + 32 lowercase hex (128 bits) |
| `token_sha256` | str | 64 hex; matches the file name |
| `issued_at`, `expires_at` | int | `60 <= expires - issued <= 2592000` |
| `uses` | int or null | 1 to 20, or null for no limit |
| `scope` | str | `/`, a folder ending `/`, or an exact path; passes the path rules |
| `kind` | str | `site` or `stack` |

State: live (unexpired and, with a limit, with a slot free), then spent, expired or
revoked (file removed). Use markers: `used/<token_sha256>.<slot>`, created with
`O_CREAT|O_EXCL`.

## Session (`sandbox.share-session.v2`), `sites/<entry>/sessions/<secret_sha256>.json`

| Field | Type | Rule |
|---|---|---|
| `id` | str | `s-` + 32 hex |
| `link` | str | Link id |
| `link_sha256` | str | The link's file name; the link file must exist on every request |
| `created_at`, `expires_at` | int | `expires = min(link.expires_at, created + session_ttl, enabled_until)` |
| `scope`, `kind` | | Copied from the link |

## StackPage (`sandbox.share-stack.v1`), `sites/<entry>/stacks/<link id>.json`

| Field | Type | Rule |
|---|---|---|
| `link` | str | Link id; removed with the link |
| `title` | str | Stack slug (`[a-z0-9-]{1,63}`) |
| `hosts` | list | `{hostname, role, probe: "internal" or "public"}`; at most 16; every hostname on `allowed_host_suffixes` |
| `wordpress` | obj or null | `{kid, key, url}`: `kid` 32 hex, `key` 64 hex, `url` = `https://<primary><request_path>` |
| `sign_in` | list | `{entry, email?}` for each `stack_sign_in` entry |

## WordPress share key (mu-plugin `sandbox-share-<expires_at>-<kid>.php`)

Constants: kid, key (hex), user, expires_at. Token `<exp>.<nonce>.<mac>`:
`exp` within 600 s of now and before `expires_at`; `nonce` 22-64 base64url;
`mac = hex(HMAC-SHA256(key, "sandbox-share-login.v1|<kid>|<exp>|<nonce>"))`. Single use
via `add_site_option('sandbox_share_n_' . substr(sha256(kid . nonce), 0, 40))`.

## AuditRecord (`audit.jsonl`, controller and server)

`{"at", "op": "create|list|revoke", "controller", "owner", "remote", "target", "entry",
"kind", "link_id", "scope", "expires_at", "uses", "outcome", "code"}`. No token, hash,
session secret, email or link URL.
