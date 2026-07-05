# Users & members endpoints

Base: `/wp-json/fluent-boards/v2`. Most ops need admin-level permissions on the board. Role-changing endpoints are **Pro-only** — the non-Pro plugin returns 403.

⚠ **Verified 404 on projects.startise.com (2026-04-16)**: rows 1 and 2 below (`/fluent-boards-users`, `/search-fluent-boards-users`) return `rest_no_route` — i.e. the routes are simply not registered on that install (not a permissions issue; missing routes yield `rest_no_route`, not `rest_forbidden`). The Fluentboards UI itself never calls these; it uses `/projects/{board_id}/users` (row 3) for every user lookup, since the response already includes `global_admins`. **Use `list-members.sh BOARD_ID` for any board-scoped user question**, and the **WP core fallback** at the bottom of this file for the authenticated user's ID or a system-wide username search. Rows 3–12 work normally.

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 1 | GET | `/fluent-boards-users` | All system users (with their board memberships). ⚠ May 404. |
| 2 | GET | `/search-fluent-boards-users` | Search users by display name. ⚠ May 404. |
| 3 | GET | `/projects/{board_id}/users` | Board members. |
| 4 | POST | `/projects/{board_id}/add-members` | Add a user to a board. |
| 5 | POST | `/projects/{board_id}/user/{user_id}/remove` | Remove a user from a board. |
| 6 | POST | `/projects/{board_id}/user/{user_id}/make-manager` | Promote to manager. **Pro.** |
| 7 | POST | `/projects/{board_id}/user/{user_id}/remove-manager` | Demote from manager. **Pro.** |
| 8 | POST | `/projects/{board_id}/user/{user_id}/make-member` | Convert to member role. **Pro.** |
| 9 | POST | `/projects/{board_id}/user/{user_id}/make-viewer` | Convert to viewer role. **Pro.** |
| 10 | DELETE | `/remove-user-from-board` | Alternate removal endpoint. |
| 11 | POST | `/managers/roles/{user_id}` | Bulk role sync across boards. **Pro.** |
| 12 | GET | `/projects/{board_id}/assignees` | Users assignable to tasks on this board. |

## 4. POST /projects/{board_id}/add-members

```json
{ "memberId": 42, "isViewerOnly": "no" }
```

`isViewerOnly` takes `"yes"` / `"no"` strings.

## 11. POST /managers/roles/{user_id} (Pro)

```json
{ "roles": { "35": "manager", "41": "member", "52": "viewer" } }
```

Keys are board IDs, values are role names.

## Missing from the public API

- No endpoint to assign a user to a single task standalone. To assign, use `update-task.sh 80927 assignees '[42]'` (the task PUT endpoint with `property=assignees`).
- No Fluentboards-native `/me` endpoint. See the WP core fallback below.
- No WordPress profile-update endpoints here. Manage user profiles through WordPress core (`/wp-json/wp/v2/users`).

## WP core fallback — `/wp/v2/users/me` (the reliable "who am I?")

Use this whenever you need the authenticated user's ID and the Fluentboards
user routes are unavailable. The skill ships a dedicated wrapper:

```bash
bash <skill_path>/scripts/whoami.sh
```

This hits `{SITE}/wp-json/wp/v2/users/me?context=edit` with the app password
already in scope. Response includes `id`, `username`, `name`, `email`, and
`roles`. `context=edit` is important — the default (`view`) omits `email` and
`roles`.

**To map an ID → Fluentboards context**: once you have the authenticated user's
WP ID, pair it with any board membership list (`list-members.sh BOARD_ID`) to
see that user's role on a specific board.

**To find another user's ID** (when Fluentboards search is down): search WP
core by username/email —

```bash
bash <skill_path>/scripts/request.sh GET '/wp-json/wp/v2/users?search=hurayra'
```

Note the path starts with `/wp-json/...` — `request.sh` treats absolute API
paths beginning with `/wp-json/` the same as `/wp-json/fluent-boards/v2/...`
because the underlying curl builder passes absolute URLs through unchanged.
If that convenience ever stops working, fall back to the full URL form
(`"$FLUENTBOARDS_SITE/wp-json/wp/v2/users?search=…"`).

## Probing Pro vs free

The cleanest check: call any Pro endpoint (e.g. `POST /projects/1/user/1/make-manager`). A 403 with `message` "Pro feature" or similar indicates the site lacks Fluentboards Pro. 404 indicates the endpoint path is wrong.
