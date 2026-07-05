# Labels endpoints

Base: `/wp-json/fluent-boards/v2`. Use `assign-label.sh` for attaching existing labels; everything else via `request.sh`.

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 1 | GET | `/projects/{board_id}/labels` | All labels on a board. |
| 2 | POST | `/projects/{board_id}/labels` | Create a label. |
| 3 | PUT | `/projects/{board_id}/labels/{label_id}` | Update a label. |
| 4 | DELETE | `/projects/{board_id}/labels/{label_id}` | Delete a label. |
| 5 | GET | `/projects/{board_id}/labels/used-in-tasks` | Labels currently assigned to tasks on this board. |
| 6 | GET | `/projects/{board_id}/tasks/{task_id}/labels` | Labels on one task. |
| 7 | POST | `/projects/{board_id}/labels/task` | Assign a label to a task. |
| 8 | DELETE | `/projects/{board_id}/tasks/{task_id}/labels/{label_id}` | Remove a label from a task. |

## 2. POST — create label

```json
{ "label": "bug", "color": "#ffffff", "bg_color": "#c53030" }
```

`color` and `bg_color` are required hex strings. `label` (the display name) is optional; some teams use colour-only labels.

## 3. PUT — update label

```json
{ "label": "critical-bug", "color": "#ffffff", "bg_color": "#9b2c2c" }
```

`bg_color` is required; `label` and `color` are optional.

## 7. POST — assign label to task

```json
{ "taskId": 80927, "labelId": 18 }
```

`assign-label.sh` wraps this. To unassign, use endpoint #8 (DELETE).
