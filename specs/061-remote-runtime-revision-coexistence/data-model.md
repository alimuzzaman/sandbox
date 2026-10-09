# Data Model: Remote Runtime Revision Coexistence

## ControlProtocol
- `spoken: int >= 1`, `oldest_served: int >= 1`, `oldest_served <= spoken`.
- Encoded in the unit environment as `SANDBOX_REMOTE_MCP_CONTROL_PROTOCOL=<spoken>:<oldest_served>`.

## Verdict
- `state`: `compatible | protocol_newer | protocol_too_old | exact_only | unknown`
- `ok: bool` (compatible, or exact_only with equal revisions, and strict conditions met)
- `reason: str` (fixed vocabulary), `local` and `installed`: `{revision, protocol}`; `strict: bool`.

## Pin (remote file, one per holder)
- `schema: 1`, `holder`, `checkout_path`, `controller_home_digest`, `revision`, `purpose`, `registered_at`, `renewed_at`, `expires_at` (≤ 4 h after `renewed_at`), `state`: `active | broken`, `broken_by` (holder), `broken_at`.
- Derived on read: `expired` when `now >= expires_at`.
- Validation: holder `^h-[0-9a-f]{16}$`; revision `^[0-9a-f]{24}$`; purpose ≤ 120 printable chars; checkout path ≤ 4096; no secret-shaped values (checked with the existing redaction helper).
- Transitions: none → active (strict register) → active (renew) → broken (acknowledged migrate) → removed (release or expiry cleanup on next write).

## MismatchRefusal
- `code` (`remote_runtime_revision_mismatch`, `runtime_revision_mismatch`, `strict_pin_unverifiable`, `remote_runtime_unknown`), `message`, `verdict`, `local`, `installed`, `remedies: [str]`, optional `broken_pin`.

## Registration lock holder
- `pid`, `command` (argv[0..2]), `remote`, `started_at`; written into the held lock file.
