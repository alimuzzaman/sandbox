# Boards (projects) endpoints

Base: `/wp-json/fluent-boards/v2`. Call via `request.sh METHOD PATH [BODY]`.

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 1 | GET | `/projects` | List boards (paginated). |
| 2 | GET | `/projects/{board_id}` | Single board with labels, custom fields, owner, stages. |
| 3 | POST | `/projects` | Create a board. |
| 4 | PUT | `/projects/{board_id}` | Update board title/description. |
| 5 | DELETE | `/projects/{board_id}` | Delete a board. |
| 6 | PUT | `/projects/{board_id}/archive-board` | Archive. Sets `archived_at`. |
| 7 | PUT | `/projects/{board_id}/restore-board` | Restore. Clears `archived_at`. |
| 8 | POST | `/projects/{board_id}/duplicate-board` | Clone a board. |
| 9 | GET | `/projects/{board_id}/users` | Board members (with roles + admin flags). |
| 10 | GET | `/projects/{board_id}/activities` | Paginated activity log. |

## 1. GET /projects — list

Query params: `per_page` (10), `page` (1), `search`, `type` (`to-do`|`roadmap`), `order_by` (default `id`), `order_type` (default `DESC`).

Response envelope: `{ data: [...], total, current_page, per_page }`.

## 3. POST /projects — create

```json
{
  "board": {
    "title": "Engineering roadmap",
    "description": "…",
    "type": "to-do",
    "currency": "USD"
  },
  "folder_id": 12,
  "stages": [ { "title": "Backlog" }, { "title": "In progress" }, { "title": "Done" } ]
}
```

`board.title` and `board.type` are required. `stages` is optional — omit to use defaults.

## 4. PUT /projects/{board_id} — update

```json
{ "title": "New name", "description": "Revised summary" }
```

Only `title` is required. Does NOT use the `{property, value}` shape that tasks do.

## 8. POST /projects/{board_id}/duplicate-board

```json
{
  "board": { "title": "Clone of Engineering roadmap" },
  "isWithTasks": "yes",
  "isWithLabels": "yes",
  "isWithTemplates": "no"
}
```

All `isWith*` fields take `"yes"`/`"no"` strings.

## 10. GET /projects/{board_id}/activities

Query params: `per_page` (20), `page` (1).

Each activity entry carries `id`, `object_type`, `action`, `column`, `old_value`, `new_value`, `created_at`, and user info.
