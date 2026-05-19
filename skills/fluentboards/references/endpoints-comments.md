# Comments endpoints

Base: `/wp-json/fluent-boards/v2`. For posting, prefer `post-comment.sh`. For everything else use `request.sh`.

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 1 | GET | `/projects/{board_id}/tasks/{task_id}/comments` | Paginated comment list. |
| 2 | POST | `/projects/{board_id}/tasks/{task_id}/comments` | Create a comment or a threaded reply. |
| 3 | PUT | `/projects/{board_id}/tasks/{task_id}/comments/{comment_id}` | Edit an existing comment. |
| 4 | DELETE | `/projects/{board_id}/tasks/comments/{comment_id}` | Delete a comment. ⚠ Path omits `tasks/{task_id}`. |
| 5 | POST | `/projects/{board_id}/tasks/{task_id}/comment-image-upload` | Upload an image to embed in a comment. |

## 1. GET — list

Query: `page` (1), `per_page` (10), `type`, `privacy`, `include_replies` (true), `include_images` (true).

Each comment record has: `id`, `board_id`, `task_id`, `parent_id`, `type`, `privacy`, `description`, `created_by`, `created_at`, `replies_count`, `avatar`, `user`, `images`.

## 2. POST — create (or reply)

```json
{
  "comment": "Deployed the fix to staging.",
  "comment_type": "comment",
  "parent_id": 4821,
  "images": [12, 13],
  "mentionData": [42]
}
```

- `comment` (string, required) — the body; URLs auto-linkify.
- `comment_type` (`comment` | `reply`) — required. Use `reply` with `parent_id`.
- `parent_id` (integer) — required for replies, omit for top-level comments.
- `comment_by` (integer, optional) — post as another user (requires permissions).
- `images` — array of image IDs produced by endpoint #5.
- `mentionData` — array of user IDs to @-mention.

`post-comment.sh` handles the JSON escaping and the `comment`/`reply` switch automatically.

## 5. POST — upload image for a comment

Multipart body with `file` (single file). Accepted types: JPEG, GIF, PNG, BMP, TIFF, WebP, AVIF, ICO, HEIC.

Response returns an image record with `id`, `full_url`, `secure_url`, `file_size`, `file_hash`, timestamps. Reference the `id` in the `images` array of a subsequent comment create/update.

Note: there is no pre-built script for comment-image upload; use `request.sh` with multipart — or compose a one-off `curl -u "$FLUENTBOARDS_USER:$FLUENTBOARDS_APP_PASSWORD" -F file=@path …` call.
