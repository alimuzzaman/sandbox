# Subtasks endpoints

Base: `/wp-json/fluent-boards/v2`. Call via `request.sh METHOD PATH [BODY]`.

Subtasks are grouped under a parent task into one or more **subtask groups** (a kind of checklist section). Many endpoints take `task_id` in the path — for most subtask ops, `task_id` refers to the **parent** task; for a few (delete, clone, move-to-board), `task_id` refers to the **subtask itself**.

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 1 | GET | `/projects/{board_id}/tasks/{task_id}/subtasks` | List subtask groups + their subtasks. |
| 2 | POST | `/projects/{board_id}/tasks/{task_id}/subtasks` | Create a subtask. |
| 3 | POST | `/projects/{board_id}/tasks/{task_id}/subtask-group` | Create a subtask group. |
| 4 | PUT | `/projects/{board_id}/tasks/{task_id}/subtask-group` | Rename a subtask group. |
| 5 | DELETE | `/projects/{board_id}/tasks/{task_id}/subtask-group` | Delete a subtask group. |
| 6 | DELETE | `/projects/{board_id}/tasks/{task_id}/delete-subtask` | Delete a subtask (here `task_id` = subtask). |
| 7 | POST | `/projects/{board_id}/tasks/{task_id}/move-subtask` | Move subtask(s) between groups (same parent). |
| 8 | PUT | `/projects/{board_id}/tasks/update-subtask-position/{subtask_id}` | Reorder a subtask. |
| 9 | PUT | `/projects/{board_id}/tasks/{task_id}/convert-to-subtask` | Convert a task to a subtask of another task. |
| 10 | PUT | `/projects/{board_id}/tasks/{task_id}/move-to-board` | Move subtask to a different board/stage. |
| 11 | POST | `/projects/{board_id}/tasks/{task_id}/clone-subtask` | Clone a subtask. |

## 2. POST — create subtask

```json
{ "title": "Open PR", "group_id": 18, "due_at": "2026-04-22", "add_to_top": true }
```

`title` and `group_id` are required. `add_to_top=true` inserts at position 0.

## 3–5. Subtask groups

```json
// create
{ "title": "Rollout checklist" }

// update
{ "group_id": 18, "title": "Rollout checklist (v2)" }

// delete
{ "group_id": 18 }
```

## 7. POST — move subtasks between groups

```json
{ "group_id": 20, "subtask_id": [101, 102, 103] }
```

`subtask_id` accepts a single integer or an array.

## 8. PUT — reorder a subtask

```json
{ "newPosition": 3, "newSubtasksGroupId": 20 }
```

## 9. PUT — convert task to subtask

```json
{ "parent_id": 80927, "assigneeId": 42, "subtaskGroupId": 18 }
```

Demotes the task and nests it under `parent_id`. `subtaskGroupId` targets a specific checklist; `assigneeId` optionally sets the new owner.

## 10. PUT — move subtask to a different stage

```json
{ "stage_id": 512 }
```

The `task_id` in the path is the subtask's own ID.
