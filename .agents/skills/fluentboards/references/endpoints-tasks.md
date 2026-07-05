# Tasks (cards) endpoints

Base: `/wp-json/fluent-boards/v2`. For most reads/writes use the dedicated scripts (`get-task.sh`, `create-task.sh`, `update-task.sh`, `move-task.sh`); use `request.sh` for anything not wrapped.

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 1 | GET | `/projects/{board_id}/tasks` | Paginated task list. |
| 2 | GET | `/projects/{board_id}/tasks/{task_id}` | Single task + assignees, attachments, labels, settings. |
| 3 | POST | `/projects/{board_id}/tasks` | Create a task. |
| 4 | PUT | `/projects/{board_id}/tasks/{task_id}` | Update one property (see `{property, value}` shape below). |
| 5 | DELETE | `/projects/{board_id}/tasks/{task_id}` | Delete a task. |
| 6 | PUT | `/projects/{board_id}/tasks/{task_id}/move-task` | Move between stages/boards. |
| 7 | POST | `/projects/{board_id}/tasks/{task_id}/clone-task` | Clone with selective content. |
| 8 | GET | `/projects/{board_id}/tasks/{task_id}/comments` | Task comments (paginated). |
| 9 | GET | `/projects/{board_id}/tasks/{task_id}/activities` | Task activity log. |

## 3. POST — create

```json
{
  "task": {
    "title": "Investigate cache stampede",
    "board_id": 35,
    "stage_id": 100,
    "priority": "high",
    "crm_contact_id": 12,
    "is_template": "no"
  }
}
```

`title`, `board_id`, `stage_id` are required.

## 4. PUT — update (⚠ quirky `{property, value}` shape)

Fluentboards does NOT use a standard merge-patch body. Each call updates exactly one property:

```json
{ "property": "priority", "value": "high" }
```

Supported `property` values include:

- `title` (string)
- `description` (HTML string — rendered by TinyMCE, **not** Markdown; use `<p>`, `<strong>`, `<ul>/<li>`, `<code>`, `<pre>`, `<a href>`, `<br>`, `<hr>`, etc.)
- `status` (string)
- `priority` (`low` | `medium` | `high`)
- `due_at` (ISO-like datetime, e.g. `"2026-05-01 00:00:00"`)
- `started_at` (datetime)
- `assignees` (array of user IDs) — ⚠ **toggle semantics, not replace.** Each ID in the array is flipped against the current assignee set (present → removed, absent → added). Sending `[A, B, C]` does not set assignees to `{A, B, C}`; it toggles each in sequence. Use `assign-user.sh` (which handles this correctly) or send a single-ID array per change.
- `crm_contact_id` (integer)
- `parent_id` (integer — convert to subtask)
- `is_watching` (boolean)
- `archived_at` (datetime to archive, `null` to restore)
- `last_completed_at` (datetime)
- `board_id` (integer — move to a different board)
- `type` (string)
- `reminder_type`, `remind_at` (reminder settings)
- `settings` (object; task-specific settings)
- `is_template` (`yes` | `no`)

`update-task.sh` wraps this and coerces the string `value` into the right JSON type (bool / null / integer / array / string).

## 6. PUT — move-task

```json
{ "newStageId": 204, "newIndex": 0, "newBoardId": 41 }
```

`newStageId` required. `newIndex` is 0-based position in the target stage. `newBoardId` is optional — only needed for cross-board moves.

## 7. POST — clone-task

```json
{
  "title": "Clone of X",
  "stage_id": 100,
  "assignee": true,
  "subtask": true,
  "label": true,
  "attachment": false,
  "comment": false
}
```

All fields required. The boolean flags choose which child data copies over.

## 8. GET — task comments

Query: `filter` (`latest` | `oldest`), `page`, `per_page`, `type`, `privacy`, `include_replies` (default true), `include_images` (default true).

## 9. GET — task activities

Query: `filter` (`newest` | `oldest`), `page`, `per_page`. Each record has `action`, `column`, `old_value`, `new_value`, `user`, `timestamp`.
