# Workflow: Reproduce a ticket

Run by the **support** skill. Output: a deterministic repro doc the developer
skill can act on.

## Inputs

- Ticket ID (FluentBoards `fbs-XXXXX` or a Zoobbe card)
- Affected plugin slug(s) and version(s)
- Customer's WP version, theme, conflicting plugins (if known)

## Steps

1. **Boot.** `make up` — stack should already be running.
2. **Pin versions.** For each plugin in the ticket, check out the matching tag
   in the host repo (the runtime symlink will see it). Confirm with
   `wp_cli plugin list`.
3. **Match WP version** if the ticket calls it out:
   `wp_cli core update --version=<X> --force`.
4. **Activate plugins** in the order the customer has them.
5. **Seed content.** Either:
   - `import_content "<seed>.xml"` from `runtime/seeds/`, or
   - build the minimum via `wp_cli post create` / `wp_rest`.
6. **Trigger.** Perform the exact action — REST call, frontend visit (curl),
   or wp-admin flow.
7. **Capture.** `tail_log 200`. Save the relevant lines.
8. **Write repro doc** to `memory/repros/<ticket-id>.md`:

   ```markdown
   # <ticket-id> — <one-line summary>

   ## Env
   - WP: 6.7.1
   - Plugins: embedpress 4.5.2, embedpress-pro 3.9.0
   - Theme: twentytwentyfour

   ## Steps
   1. ...

   ## Expected
   ...

   ## Actual
   ...

   ## Evidence
   ```
   [debug.log excerpt]
   ```
   ```

9. **Hand off.** Tell the developer skill where the repro doc lives.

## Done criteria

- Repro is reproducible by running the steps from a clean `make clean && make up && make install`.
- `tail_log` shows the exact same error lines on every run.
