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
./sb connect --client cursor
```
Paste the emitted config into a fresh MCP client; confirm it lists the abilities and
can call `execute-php`. Discovery includes the Sandbox instructions block.

## 4. File abilities are jailed

- `wp_file_write` a file under `wp-content/` → succeeds.
- Attempt a path outside ABSPATH (and via a symlink) → rejected (`path_outside_base`).
- Attempt to write a new `.php` outside `sandbox-code/` → rejected (`php_sandbox_required`).

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
