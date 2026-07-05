# Fluentboards skill — setup

This skill needs three env vars to authenticate against your Fluentboards site. Do this once.

## 1. Install the skill

Clone this repository into your agent's skills directory.

**Claude Code** stores skills in `~/.claude/skills/`:

```bash
git clone git@github.com:HurayraIIT/fluentboards.git ~/.claude/skills/fluentboards
```

## 2. Generate a WordPress Application Password

1. Sign in to your Fluentboards site: <https://projects.startise.com/wp-admin/profile.php>
2. Scroll to the **Application Passwords** section at the bottom of the page.
3. In **New Application Password Name**, enter something you'll recognise later (e.g. `claude-fluentboards-skill`).
4. Click **Add New Application Password**.
5. Copy the 24-character password that appears — it looks like `abcd efgh ijkl mnop qrst uvwx`. WordPress shows it **once**; if you lose it you'll need to generate another.

Application Passwords are independent of your regular login password. You can revoke this one at any time from the same page without affecting anything else.

## 3. Add the env vars to your shell

Pick the snippet that matches your shell. Before pasting, replace `your-wp-username` with your WordPress username and replace the dummy app password with the one you copied in step 1.

The snippet appends the three lines to your shell rc file and reloads it, so the vars are available immediately in your current terminal and in every new one.

### zsh (macOS default)

```zsh
cat >> ~/.zshrc <<'EOF'

# Fluentboards skill credentials
export FLUENTBOARDS_SITE="https://projects.startise.com"
export FLUENTBOARDS_USER="your-wp-username"
export FLUENTBOARDS_APP_PASSWORD="abcd efgh ijkl mnop qrst uvwx"
EOF
source ~/.zshrc
```

### bash (most Linux systems, WSL)

```bash
cat >> ~/.bashrc <<'EOF'

# Fluentboards skill credentials
export FLUENTBOARDS_SITE="https://projects.startise.com"
export FLUENTBOARDS_USER="your-wp-username"
export FLUENTBOARDS_APP_PASSWORD="abcd efgh ijkl mnop qrst uvwx"
EOF
source ~/.bashrc
```

## 4. Verify it works

From the same terminal:

```bash
echo "$FLUENTBOARDS_SITE"
# should print: https://projects.startise.com

bash ~/.agents/skills/fluentboards/scripts/request.sh GET /projects | head -c 400
# should print the start of a JSON response listing your boards
```

If the verification call fails, see the **Troubleshooting** table below.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Missing Fluentboards credentials: FLUENTBOARDS_*` | Env vars didn't land in your shell. | Re-run the snippet for your shell above. Open a fresh terminal to confirm. |
| `HTTP 401 unauthorized` | Wrong username, or the app password was mistyped/revoked. | Regenerate the password at `…/wp-admin/profile.php` and paste it again. Keep the spaces as-is. |
| `HTTP 403 forbidden — the user lacks permission for this board/action` | Your user isn't a member of the board, or the feature is Fluentboards Pro only. | Ask a board admin to add you, or check whether the feature requires Pro. |
| `HTTP 404 not found` | Wrong board/task id, or the resource was deleted. | Double-check the id; try opening the task in the UI first. |
| `network: could not resolve host` | Typo in `FLUENTBOARDS_SITE`, or you're offline. | `echo "$FLUENTBOARDS_SITE"` and confirm it points at the right host. |
| `neither jq nor python3 found` (warning) | Optional JSON tooling not installed. | Install `jq` (`brew install jq`) for nicer parsing. Scripts still work with a minimal fallback. |

## Storing creds somewhere other than your rc file

If you'd rather keep credentials out of `~/.zshrc`/`~/.bashrc`, the loader also checks (in order):

1. Current environment
2. `~/.zshrc`, `~/.bashrc`, `~/.profile`
3. `.env` in the current directory, then `~/.env`, then `~/.fluentboards`

Any of these can contain `export FLUENTBOARDS_SITE=…` lines. The loader `grep`s the files — it does not source them — so the rest of your rc file is never executed.

## Revoking access

Visit `{SITE}/wp-admin/profile.php`, scroll to **Application Passwords**, and click **Revoke** next to the entry you added. Then delete the three `export` lines from your rc file.
