---
name: sandbox-fluentboards
description: Core sandbox skill — interact with Fluentboards (the company's Trello-like WP plugin used as our task tracker) via its REST API. Use to read/update cards, post comments, manage subtasks/labels/attachments, and close out tickets at the end of `ship-fix`. Accepts short-link URLs (fbs-TASKID), wp-admin deep-link URLs with board/task IDs, raw IDs, or `B/T` form.
compatibility: Requires curl. jq and python3 are optional (used as JSON parsing fallbacks). Needs FLUENTBOARDS_SITE, FLUENTBOARDS_USER, FLUENTBOARDS_APP_PASSWORD env vars.
---

# Fluentboards (sandbox core skill)

This is the sandbox's canonical ticket-tracker integration. The `developer`
and `support` skills lean on it to close cards at the end of `ship-fix`, and
any agent in the sandbox can call its scripts directly. Scripts live in
`skills/fluentboards/scripts/` inside this repo — invoke them with
`bash skills/fluentboards/scripts/<name> …` (the `<skill_path>` placeholder
in the examples below means `skills/fluentboards` when running from the
sandbox root).

Every script is a single-purpose command with a clear usage line, a standard exit-code contract, and server error messages surfaced on stderr.

## Restrictions — read before doing anything

**Never** create, update, delete, or archive **boards** or **stages** — even if the user asks. These are structural changes that must be made manually in the Fluentboards UI. Refuse politely and explain the restriction.

Allowed write operations are limited to: tasks/cards, comments, subtasks, labels, attachments, and task assignments.

## When to use

Use this skill when the user wants to:

- Read any board, stage, card/task, comment, subtask, label, attachment, or member.
- Create or update tasks/cards, comments, subtasks, labels, or attachments.
- Post a comment (or threaded reply) to a task.
- Dump "everything about this card" from a short URL or a wp-admin deep link.
- Move, clone, archive, or relabel tasks.
- Upload file attachments to a task.

## Description formatting

The Fluentboards description field is rendered by a **TinyMCE rich-text editor** — it does **not** render Markdown. Always write descriptions using basic HTML:

- Structure: `<p>`, `<br>`, `<hr>`
- Text style: `<strong>`, `<em>`, `<u>`, `<s>`, `<code>`, `<pre>`
- Lists: `<ul><li>…</li></ul>`, `<ol><li>…</li></ol>`
- Links: `<a href="…">text</a>`
- Inline style: use sparingly, e.g. `<span style="color:#e53e3e">critical</span>`

Do **not** use Markdown syntax (` ``` `, `**`, `##`, etc.) in descriptions — it will appear as raw text.

## Setup (one-time)

Credentials live in three env vars. The skill's [`README.md`](README.md) has copy-pasteable zsh/bash snippets. Quick reminder:

- `FLUENTBOARDS_SITE` — site URL, e.g. `https://projects.startise.com`
- `FLUENTBOARDS_USER` — WordPress username
- `FLUENTBOARDS_APP_PASSWORD` — Application Password generated at `{SITE}/wp-admin/profile.php`

Verify setup:

```bash
bash <skill_path>/scripts/request.sh GET /projects | head
```

If credentials are missing the script points the user back at `README.md` and exits `1`.

## Quick start

All scripts live under `<skill_path>/scripts/`. Everything below uses `bash <skill_path>/scripts/<script>`.

```bash
# 1. Fetch EVERYTHING about a card from either URL form
bash <skill_path>/scripts/get-card.sh 'https://projects.startise.com//fbs-80927'
bash <skill_path>/scripts/get-card.sh 'https://projects.startise.com/wp-admin/admin.php?page=fluent-boards#/boards/35/tasks/80927-Bug-Fix-%7C-...'

# 2. Post a comment on a card (text is auto JSON-escaped)
bash <skill_path>/scripts/post-comment.sh 80927 'Deployed the fix to staging — ready for QA.'

# 3. Update a single task property (priority, title, due_at, status, …)
bash <skill_path>/scripts/update-task.sh 80927 priority high

# 4. Fetch something the pre-built scripts don't wrap — use request.sh
bash <skill_path>/scripts/request.sh GET '/projects/35/labels'
```

## URL forms accepted

Scripts that accept `<url-or-id>` understand all four of these. See [references/url-formats.md](references/url-formats.md) for details and the resolver's fallback order.

| Form | Example | Board ID | Task ID |
|------|---------|----------|---------|
| **Full wp-admin URL** ⭐ | `…/admin.php?page=fluent-boards#/boards/35/tasks/80927-slug…` | parsed from fragment | parsed from fragment |
| **`B/T` explicit** ⭐ | `35/80927` | parsed | parsed |
| Short URL | `https://site//fbs-80927` (or single slash) | resolved (best-effort; often 404s) | parsed |
| Bare integer | `80927` | resolved (falls back to board scan) | parsed |

⭐ **Prefer these two**: zero network calls, instant. Short URLs are a **site-specific BetterLinks integration** (not a Fluentboards feature) — they only resolve for tasks where someone manually clicked the "create share link" button in the UI. Bare integers force a board scan if nothing else matches.

Resolution results are cached in `${TMPDIR:-/tmp}/fb-resolve-cache.txt` for 1 hour.

## Scripts

Every script lives in `<skill_path>/scripts/`. Call with `bash <skill_path>/scripts/<name> …`.

### Authoring primitives (use for anything the wrappers don't cover)

| Script | Usage | Purpose |
|--------|-------|---------|
| `request.sh` | `METHOD PATH [BODY_JSON \| @file]` | Raw authenticated HTTP. Catch-all for every endpoint in `references/endpoints-*.md`. |
| `resolve.sh` | `<url-or-id>` | Print `board_id<TAB>task_id` — use when you need the IDs but not the task body. |

### Reading

| Script | Usage | Purpose |
|--------|-------|---------|
| `get-card.sh` | `<url-or-id>` | Labelled dump: task + comments + subtasks + labels + activities + attachments. |
| `get-task.sh` | `<url-or-id>` | Single task JSON only. |
| `get-board.sh` | `<board_id>` | Single board with stages/labels/members. ⚠ Embedded labels have `title: null` on some installs — use `list-labels.sh` if you need label titles. |
| `list-boards.sh` | `[--page=N] [--per-page=N] [--search=TEXT] [--type=to-do\|roadmap]` | Paginated board list. |
| `list-tasks.sh` | `<board_id> [--page=N] [--per-page=N]` | Paginated task list on a board. |
| `list-labels.sh` | `<board_id>` | Full label catalogue (id, title, colors). Prefer over `get-board.sh` for label lookups. |
| `list-members.sh` | `<board_id>` | Formal **board members** with roles (via `/projects/B/users`). Use this for permission/role questions. |
| `list-assignees.sh` | `<board_id>` | Users who can be **assigned to tasks** on the board (via `/projects/B/assignees`). Use this before calling `assign-user.sh`, and to find people who aren't formal members. |
| `whoami.sh` | (no args) | Print the authenticated user's WP profile (ID, username, email, role) via `/wp/v2/users/me`. Use when you need your own user ID — Fluentboards has no `/me` endpoint and the `/fluent-boards-users` routes 404 on some installs. |

### Writing

| Script | Usage | Purpose |
|--------|-------|---------|
| `post-comment.sh` | `<url-or-id> <text> [--parent=ID]` | Post a comment or threaded reply. |
| `create-task.sh` | `<board_id> <stage_id> <title> [--priority=…] [--desc=…] [--crm-contact=…]` | Create a task. |
| `update-task.sh` | `<url-or-id> <property> <value>` | Update a single task property (wraps Fluentboards' `{property,value}` PUT quirk). |
| `move-task.sh` | `<url-or-id> <stage_id> [--index=N] [--board=N]` | Move a task to a stage, optionally another board. Defaults to `--index=0` (top of stage). |
| `upload-attachment.sh` | `<url-or-id> <file> [<file2> …]` | Multipart file upload (supports many files). |
| `assign-label.sh` | `<url-or-id> <label_id>` | Attach an existing label. Create labels via `request.sh POST /projects/B/labels`. |
| `assign-user.sh` | `<url-or-id> <user_id> [--remove]` | Add (or remove with `--remove`) a user to a task's assignees. Idempotent — fetches current assignees and merges, since the underlying PUT takes the full list. |
| `create-subtask-group.sh` | `<url-or-id> <title>` | Create a subtask group (checklist section). Always emits `{group_id, title, …}`; re-fetches the subtask tree if the create response omits the id. |
| `create-subtask.sh` | `<url-or-id> <group_id> <title> [--due=YYYY-MM-DD] [--top]` | Add a subtask to an existing group. |

### Global flags (every script)

- `--verbose` — log resolved method/URL to stderr (never the auth header)
- `--site=URL`, `--user=NAME` — one-off credential overrides

## Anything not wrapped: use `request.sh`

For endpoints not wrapped by a dedicated script (stages, subtasks, duplicating boards, archiving, user role changes, etc.), construct the call with `request.sh` and consult the appropriate file in `references/`:

| If you're working on… | Read |
|-----------------------|------|
| Boards (create/duplicate/archive/restore/activities) | [references/endpoints-boards.md](references/endpoints-boards.md) |
| Stages (create/reposition/archive/sort) | [references/endpoints-stages.md](references/endpoints-stages.md) |
| Tasks (full property list, clone, task activities) | [references/endpoints-tasks.md](references/endpoints-tasks.md) |
| Comments (filter/privacy/replies/image upload) | [references/endpoints-comments.md](references/endpoints-comments.md) |
| Subtasks (groups, positions, convert, clone) | [references/endpoints-subtasks.md](references/endpoints-subtasks.md) |
| Labels (CRUD, used-in-tasks) | [references/endpoints-labels.md](references/endpoints-labels.md) |
| Users & members (roles, search, sync — some Pro-only) | [references/endpoints-users.md](references/endpoints-users.md) |
| Attachments (URL-type attachments, update, delete) | [references/endpoints-attachments.md](references/endpoints-attachments.md) |

## Common recipes

```bash
# Move a task into "Done" (find stage_id first via get-board.sh).
bash <skill_path>/scripts/move-task.sh 80927 204

# Assign to a user (assignees takes an array; JSON value must parse).
bash <skill_path>/scripts/update-task.sh 80927 assignees '[42]'

# Archive a task.
bash <skill_path>/scripts/update-task.sh 80927 archived_at '2026-04-16 10:00:00'

# Stop watching.
bash <skill_path>/scripts/update-task.sh 80927 is_watching false

# Create a label, then attach it.
bash <skill_path>/scripts/request.sh POST '/projects/35/labels' '{"label":"needs-review","color":"#ffffff","bg_color":"#c05621"}'
bash <skill_path>/scripts/assign-label.sh 80927 <new_label_id>

# Delete a task.
bash <skill_path>/scripts/request.sh DELETE '/projects/35/tasks/80927'
```

## Error handling

Every script follows this contract:

| Exit | Meaning | Likely cause |
|------|---------|--------------|
| `0` | Success | — |
| `1` | Bad usage / missing env vars | Missing positional, invalid flag, unset `FLUENTBOARDS_*` |
| `2` | URL/ID could not be resolved | Task not visible to the user, typo in short URL, board iteration failed |
| `3` | HTTP / network error | 4xx/5xx from server, DNS/TLS/timeout. Server's `message` field is on stderr. |

`stdout` is always machine-readable (JSON for most scripts, labelled sections only for `get-card.sh`). All diagnostics go to `stderr`, so pipes stay clean.

On HTTP 429 or 5xx the HTTP wrapper automatically retries once with 1 s backoff before surfacing the error.

## Security

- Never print `FLUENTBOARDS_APP_PASSWORD` or the `Authorization` header. All scripts use curl's `-u` flag so curl handles encoding and redacts on verbose.
- Treat the WordPress app password as a secret: it grants full API access for that user. Rotate at `{SITE}/wp-admin/profile.php` → Application Passwords if suspected leaked.
- The resolver cache at `/tmp/fb-resolve-cache.txt` only stores public IDs (board_id, task_id, timestamp) — no secrets.
