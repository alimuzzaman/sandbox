# URL formats & the resolver

## Accepted inputs

Any script with a `<url-or-id>` parameter accepts one of these shapes:

| Form | Example | Board ID | Task ID |
|------|---------|----------|---------|
| Full wp-admin URL | `https://host/wp-admin/admin.php?page=fluent-boards#/boards/35/tasks/80927-Bug-Fix-%7C-slug` | parsed | parsed |
| `B/T` | `35/80927` | parsed | parsed |
| Short URL | `https://host/fbs-80927`  or  `https://host//fbs-80927` | resolved (best-effort) | parsed |
| Bare integer | `80927` | resolved (best-effort) | parsed |

**Prefer the full URL or `B/T` form** — both parse instantly with zero network calls. The other two shapes need resolution and can fall through to a slow board-scan.

Notes on the full URL form:
- The IDs live after `#` (the URL fragment), so they never reach any server as part of a redirect. The parser reads them client-side.
- Task slug (`-Bug-Fix-%7C-…`) is stripped — only the integer before the first `-` is used as the task ID.

Notes on the short URL form:
- `/fbs-{task_id}` is **not** a Fluentboards feature. On projects.startise.com it's a BetterLinks-plugin short link created by a custom "create share link" button in the Fluentboards UI. The short link only exists if somebody manually clicked that button for the specific task — otherwise the URL 404s.
- When the short link exists, the resolver's HTML-scrape strategy will find `board_id` embedded in the landing page; otherwise, it falls through to cache → board iterate.
- The double-slash `//fbs-` that some hand-copied URLs contain is handled gracefully.

## Resolver (for forms where only the task ID is known)

Implemented in [`scripts/lib/resolve.sh`](../scripts/lib/resolve.sh). On a cache miss, the resolver tries strategies in order and stops at the first success.

1. **Cache** — `${TMPDIR:-/tmp}/fb-resolve-cache.txt`, 1-hour TTL. Line format `task_id\tboard_id\tunix_ts`. Trimmed to last 500 entries. `flock` guards concurrent appends when available.
2. **HTML scrape of `$SITE/fbs-{task_id}`** — only works when the BetterLinks short link exists (see above). Returns immediately on 404, so the cost is one HEAD-weight GET. Looks for `board_id=N`, `boardId: N`, or an embedded `#/boards/N/tasks/{task_id}` link.
3. **Board iteration** — `GET /projects?per_page=100`, then `GET /projects/{B}/tasks?per_page=200` for each board, scanning for `"id": {task_id}`. Slow (N+1 calls). Always works if the user has access to the board.

Set `FB_RESOLVE_NO_ITERATE=1` in the environment to disable strategy 3 and fail fast.

**Fastest path:** feed the resolver a full wp-admin URL or `B/T`. Nobody should wait for a board scan when the caller already has the board ID on the clipboard.

## Why no `GET /tasks/{T}` shortcut?

The Fluentboards task-read endpoint requires a board prefix (`GET /projects/{B}/tasks/{T}`). A few task-scoped endpoints (notably `/tasks/{T}/add-attachment`, `/tasks/{T}/attachment-update/{A}`, `/tasks/{T}/attachment-delete/{A}`) do accept a bare task ID, but they are write paths and not useful for resolution. If a future plugin version adds a board-less `GET /tasks/{T}`, drop it into `resolve.sh` as a faster strategy 2.

## Why not query BetterLinks directly?

The plugin does expose `/wp-json/betterlinks/v1/links`, but its permission callback requires `manage_options`. App passwords scoped to a contributor (the recommended least-privilege setup for this skill) come back with `rest_forbidden`, so we can't use it as an auto-resolve path for typical users.

## Response envelope

List endpoints wrap results in:

```json
{
  "data": [...],
  "message": "…",
  "total": 100,
  "current_page": 1,
  "per_page": 15
}
```

Pagination params: `page` (1-based), `per_page` (default 15). Single-resource endpoints return the resource object directly (no envelope) or nest it under a key like `task`, `comment`, etc.

## Error responses

4xx responses carry a `message` string with a human-readable explanation. The HTTP wrapper in [`scripts/lib/http.sh`](../scripts/lib/http.sh) extracts and prints it to stderr before exiting `3`, so the agent sees the real cause rather than just the status code.
