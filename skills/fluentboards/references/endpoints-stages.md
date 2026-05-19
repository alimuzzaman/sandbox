# Stages (columns / lists) endpoints

Base: `/wp-json/fluent-boards/v2`. Call via `request.sh METHOD PATH [BODY]`.

Stages are the vertical columns on a board (Backlog / Doing / Done, etc.). A stage also embeds inside the `GET /projects/{board_id}` response, so you rarely need a separate list endpoint.

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 1 | POST | `/projects/{board_id}/stage-create` | Create a stage. |
| 2 | PUT | `/projects/{board_id}/update-stage/{stage_id}` | Update title/settings. |
| 3 | PUT | `/projects/{board_id}/archive-stage/{stage_id}` | Archive a stage. |
| 4 | PUT | `/projects/{board_id}/restore-stage/{stage_id}` | Restore an archived stage. |
| 5 | PUT | `/projects/{board_id}/re-position-stages` | Reorder stages. |
| 6 | PUT | `/projects/{board_id}/stage/{stage_id}/archive-all-task` | Archive every task in a stage. |
| 7 | PUT | `/projects/{board_id}/stage/{stage_id}/sort-task` | Sort tasks inside a stage. |
| 8 | GET | `/projects/{board_id}/archived-stages` | List archived stages. |
| 9 | GET | `/projects/{board_id}/stage-task-available-positions/{stage_id}` | Position numbers + current task count for UI placement. |

## 1. POST /projects/{board_id}/stage-create

```json
{ "title": "Doing", "position": 2, "status": "active" }
```

`title` is required; `position` and `status` are optional.

## 2. PUT /projects/{board_id}/update-stage/{stage_id}

```json
{ "title": "Doing (WIP)", "settings": { /* stage-specific settings */ } }
```

## 5. PUT /projects/{board_id}/re-position-stages

```json
{ "list": [104, 101, 103, 102] }
```

The `list` is an array of stage IDs in the desired display order.

## 7. PUT /projects/{board_id}/stage/{stage_id}/sort-task

```json
{ "order": "priority", "orderBy": "DESC" }
```

`order` ∈ {`priority`, `due_at`, `position`, `created_at`, `title`}. `orderBy` ∈ {`ASC`, `DESC`}.

## 8. GET /projects/{board_id}/archived-stages

Query: `noPagination`, `per_page`, `page`.
