# Data Model

- **Remote registration** `storage_floor_gb: int` (absent/0 = no floor).
- **Reservation** (remote `runtime/storage/reservations.json`, list): `id` (hex16), `target` (source-family path), `estimate_bytes`, `created_at`, `expires_at` (epoch s). Expired entries pruned only on successful admission under `reservations.lock`.
- **Storage decision**: `ok`, `code`, `reason`, `free_bytes`, `reserved_bytes`, `estimate_bytes`, `estimate_source`, `floor_bytes`.
- **Inventory entry** (+) `finished_job_idle: bool`.
- **Heal candidate**: `container`, `id`, `project`, `restart_count`, `max_retries`, `exit_code`.
