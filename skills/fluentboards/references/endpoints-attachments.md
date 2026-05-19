# Attachments endpoints

Base: `/wp-json/fluent-boards/v2`. For file uploads use `upload-attachment.sh`; for URL-type attachments and updates/deletes use `request.sh`.

⚠ **Path inconsistency**: some attachment paths are nested under `/projects/{board_id}/…` and some sit at the top level (`/tasks/{task_id}/…`). The table below is copied from the upstream docs — use it literally.

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 1 | GET | `/projects/{board_id}/tasks/{task_id}/attachment` | List task attachments. |
| 2 | POST | `/projects/{board_id}/tasks/{task_id}/add-task-attachment-file` | Upload one or more files (multipart `file[]`). |
| 3 | POST | `/tasks/{task_id}/add-attachment` | Add a URL-type attachment. |
| 4 | PUT | `/tasks/{task_id}/attachment-update/{attachment_id}` | Rename an attachment. |
| 5 | DELETE | `/tasks/{task_id}/attachment-delete/{attachment_id}` | Delete an attachment. |

## 1. GET — list attachments

Response: `{ attachments: [ { id, file_hash, object_type, attachment_type, full_url, secure_url, title, file_size, created_at, updated_at }, … ] }`.

## 2. POST — upload files (multipart)

Use `upload-attachment.sh` for this — it handles the multipart layout. Equivalent raw call:

```bash
curl -u "$FLUENTBOARDS_USER:$FLUENTBOARDS_APP_PASSWORD" \
  -F 'file[]=@/tmp/a.png' \
  -F 'file[]=@/tmp/b.pdf' \
  "$FLUENTBOARDS_SITE/wp-json/fluent-boards/v2/projects/35/tasks/80927/add-task-attachment-file"
```

Response: `{ message, attachments: [ … ] }`.

## 3. POST — add URL attachment (no `board_id` in path)

```json
{ "title": "Design doc", "url": "https://docs.example.com/rollout-plan" }
```

Body is JSON (not multipart). Note the path starts with `/tasks/{task_id}/…`.

## 4. PUT — rename

```json
{ "title": "Design doc (v2)" }
```

## 5. DELETE

No body. Returns `{ message, attachments: [ …remaining ] }`.
