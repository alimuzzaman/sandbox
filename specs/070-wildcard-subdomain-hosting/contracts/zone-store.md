# Contract: Remote Zone Coverage Store

Fixed program sent over the authenticated SSH transport, in the style of `sandbox/hosting/coordination/program.py`. Input: remote Sandbox home and one base64 JSON request. Output: one JSON line. Refusals are `{"ok": true, "refused": "<code>", ...}`. Anything else raises `coverage_store_unavailable`.

Layout: `<home>/runtime/zone-coverage/coverage.lock` (flock, 10 s wait), `zones/<zone>.json` (0600, written to a temp file, fsynced, renamed, parent fsynced). A corrupt document refuses, and is never rewritten.

| Action | Effect |
|---|---|
| `read` | Returns the document or `absent` |
| `list` | Returns zone names with state, no bodies |
| `begin` | Sets `operation` and `state`, given `expected_generation`. Refuses a live lease, stale generation or stale `expected_dependents_revision`, all under the flock. With `resume: true`, takes over an expired same-kind operation with a new id |
| `phase` | Records phase and any fields and renews the lease, for the current operation id only |
| `finish` | Sets the final `state`, clears `operation`, appends history, raises generation when told |
| `dependent-bind` | Adds or updates one dependent, `pending` or `bound`, and raises `dependents_revision`. Refuses `pending` unless the state is `active` with no live disable operation |
| `dependent-release` | Removes one dependent and raises `dependents_revision`. Idempotent |

Bounds: request 64 KiB, document 256 KiB, 512 dependents, history 64. Zone pattern: normalized IDNA hostname. Dependent keys go through the same check as coordination's `_check_key`.
