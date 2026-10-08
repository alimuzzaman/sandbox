# Evidence: quickstart step 5, a session past 30 minutes (T030)

Status: NOT RUN YET. This needs a human at a real terminal for about 32
minutes. An agent must not run it (session mode is operator-only).

## How to run it

1. Use a throwaway fixture source with a synthetic value, registered in a
   project's `sandbox.config.json` (file mode 0600), never a real credential.
2. In your own terminal window, not an agent tool:

   ```bash
   ./sb secrets run --session --lifetime-seconds 1900 --source fixture \
     --key FIXTURE_TOKEN --project-dir PROJECT_DIR -- python3 -m http.server 8765
   ```

   Use another port if 8765 is taken.
3. Copy the start line below.
4. At minute 31, from a second terminal: `curl -sS -o /dev/null -w '%{http_code}\n'
   http://127.0.0.1:8765/` should print `200`, and the request's log line should
   appear in the session terminal.
5. Leave it alone. At about 31 min 40 s the end line should show
   `end_reason=lifetime_expired`; then run `echo $?` (expect 0) and
   `lsof -nP -iTCP:8765 -sTCP:LISTEN` (expect nothing within 5 s of the end).
6. Fill in the results below, then tick T030 in `tasks.md` and mark feedback
   `2cfab06f` addressed.

## Results (fill in)

- Date / machine:
- Start line:
- HTTP status at minute 31:
- End line:
- Exit status:
- Port free within 5 s of the end: yes / no
