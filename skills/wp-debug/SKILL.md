---
name: wp-debug
description: Diagnose WordPress and plugin runtime errors with Sandbox logs, Query Monitor, WP-CLI, and Xdebug.
---

# WP Debugging

Tools and patterns for diagnosing WordPress / plugin errors in the sandbox.
Use when you see a fatal, a white screen, a 500, an unexpected REST 4xx, a
"there was a critical error," or anything in the debug log.

---

## Escalation ladder (cheap → heavy — spec 007)

Climb only as far as the bug needs:

1. **`dump()` / `dd()` + `tail_log`** — on a local instance, drop `dump($x)` in
   plugin code (captured, not echoed) and read `tail_log(file="dump")` or
   `./sb dump`. The selector also accepts `debug` (default) and `qm`; use the
   latter only to inspect raw JSONL. Fastest signal.
2. **`qm_capture`** (`./sb qm`) — load a URL and capture **Query Monitor**:
   queries (+ slow/dupe), PHP errors/notices, HTTP calls, and timing. The right
   tool for "why is this slow / which query." It auto-activates QM on first use.
3. **`xdebug`** (`./sb xdebug on`) — real breakpoints when you need to step. See
   below; trigger-gated so normal traffic isn't blocked.

---

## Step-debug with Xdebug (real breakpoints)

```bash
./sb xdebug on        # installs xdebug + writes the ini, restarts wp
./sb xdebug status    # confirms on|off
./sb xdebug off       # removes the ini, restarts wp
```

Mode is `trigger` — Xdebug only attaches when the request carries
`XDEBUG_TRIGGER` (cookie, GET param, or env). That keeps normal traffic
fast and stops the debugger from breaking on every background cron.

Client: VS Code "PHP Debug" extension, port **9003**, path mapping from
`${plugins_home}/<slug>` → `/var/www/html/wp-content/plugins/<slug>` (and
similarly for any source bind-mount). The sandbox bind-mounts plugin sources
at the **same absolute path** inside the container, so path mapping is 1:1.

Browser: install any "Xdebug helper" extension or append `?XDEBUG_TRIGGER=1`
to the URL.

CLI: `XDEBUG_TRIGGER=1 ./sb wp …`

On a Herd instance, `./sb xdebug status` reports whether the host PHP has
Xdebug loaded. `on` and `off` cannot alter it per instance because Herd shares
host PHP; configure Herd/PHP instead, then send a request with `XDEBUG_TRIGGER`.

---

## debug.log — the first place to look

```
./sb wp config get WP_DEBUG_LOG       # verify it's enabled
./sb wp eval 'error_log("ping " . time());'   # write a marker, then look for it
```

Then use `tail_log` (MCP) — it scopes to `wp-content/debug.log` and supports
`since:` to grab only entries newer than your last check. Always print a
marker before triggering the broken flow, then read from-marker onward. Cuts
noise from background cron 10×.

---

## Symptom → likely cause table

| Symptom | Likely cause | First check |
|---|---|---|
| REST returns 401 on Application Password auth | `WP_ENVIRONMENT_TYPE` is not `local` | `wp_cli config get WP_ENVIRONMENT_TYPE` |
| Plugin appears in `wp plugin list` but not in admin UI | symlink at depth > 1, or target not bind-mounted at same abs path | `ls -lL wp-content/plugins/<slug>` inside the container |
| White screen, nothing in debug.log | fatal in a `mu-plugin` or before `WP_DEBUG_LOG` define | `wp_cli --skip-plugins eval 'echo "alive";'` |
| "Critical error" email but no stack | site health recovery email triggered, real trace is in `debug.log` | tail_log right after reproducing |
| Block "this content is unexpected" validation error | block `save()` output drifted from saved markup | run a `wp_cli post get <id> --field=post_content` diff against the new save() output, add a `deprecated[]` entry |
| `WP_DEBUG_LOG` log line is missing | a plugin called `ini_set('log_errors', 0)` or overwrote the handler | grep plugin source for `ini_set\|set_error_handler\|error_reporting` |
| Cron hook never fires | scheduled but DISABLE_WP_CRON=true and nothing pings wp-cron.php | `wp_cli cron event list`, then run by hand |
| Email shows in code but not Mailpit | mailer overridden by a plugin (FluentSMTP, WP Mail SMTP) | check `mail_list`; if empty, look for `wp_mail` filter overrides |
| Object cache won't bust | persistent cache plugin active (Redis, Memcached) | `wp_cli cache flush` then re-test |
| REST endpoint 404 but registered | rewrite rules not flushed | `wp_cli rewrite flush` |

---

## Query Monitor (recommended for any non-trivial debug session)

```bash
./sb qm / --collectors db_queries,timing,php_errors
./sb qm off                  # deactivate after the session, if desired
```

QM is installed inactive by default, then auto-activates for the first capture.
Each command tags its actual anonymous HTTP request and returns that exact JSON
record, rather than a stale final JSONL line. It works on Docker and Herd.
The default response omits the large `hooks` collector; request an explicit
subset with `--collectors`. `tail_log(file="qm")` is the raw JSONL fallback.

---

## Common dead-ends to skip

- "Restart Docker" — almost never the fix. `./sb doctor` first.
- "Clear browser cache" — for a server-side bug, no.
- "Bump WP version" — only when you've ruled out the plugin.
- "It works on my machine" — every dev runs the same stack here; if you
  see divergence, suspect host file permissions or stale symlinks first.
