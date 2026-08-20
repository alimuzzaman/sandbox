# Quickstart: In-Instance WP Abilities — live verification

Prerequisites: a running instance (`./sb ensure` or `ensure_instance`) on a
supported WP version (Abilities API present). All checks are live-stack
(constitution IV).

## 1. Layer is provisioned + enabled

```
./sb abilities status
```
Expect: enabled + an endpoint URL. Confirm `wp-content/mu-plugins/00-sandbox-abilities.php`
+ `sandbox-abilities/` exist in the instance.

## 2. execute-php round-trip (direct + proxy)

- Proxy: `wp_eval_live(code="return get_option('siteurl');", project_dir=…)` →
  `{success:true, return_value:"http://…", execution_time_ms:…}`.
- A notice-emitting snippet appears in `errors[]`, request still succeeds.
- A throwing snippet returns `success:false` with `error_message`/`error_class`;
  the site stays up.

## 3. External client connects directly

```
./sb abilities connect
```
Paste the emitted config into a fresh MCP client; confirm it lists the abilities and
can call `sandbox/execute-php`. This real-client acceptance remains separate from
the local command/documentation checks.

Call `mcp-adapter-discover-abilities` and confirm its existing `abilities` list is
unchanged and the result also contains `sandbox_environment` with the validated
focused plugin (or `null`), a credential-free base instance URL, and the exact
reminder `Before destructive changes, use the supported Sandbox snapshot workflow.`.
The Sandbox server transport requires an authenticated `manage_options` user; the
local harness verifies the shape, permission callback, and server scoping, while the
authenticated external-client call remains the live acceptance gate.

## 4. File abilities are jailed

- From the direct client, call `sandbox/write-file` for a file under `wp-content/`
  and then `sandbox/read-file` → succeeds.
- Attempt a direct-client path outside ABSPATH (and via a symlink) → rejected
  (`path_outside_base`).
- Attempt to create a new `.php` outside `sandbox-code/` → rejected
  (`php_sandbox_required`).
- In the host-side Sandbox MCP, use the existing `fs_read`, `fs_write`, and
  `fs_list` tools; no `wp_file_*` proxy tools are registered.

## 5. Crash recovery

- `write-file` a sandbox-code PHP file that fatals on load; load any page.
- Expect: site stays up in safe mode, `.crashed` marker present, admin notice names
  the file. `?sb_safe_mode=1` forces safe mode. Remove the marker → normal load.

## 6. Gating

- `./sb abilities off` → endpoint exposes nothing; ability calls 403.
- Re-enable; call without a valid app password or without `manage_options` → 403.

## 7. Driver parity

Repeat steps 2 + 5 on a herd instance using its `https://<instance>.test/...`
endpoint.
