# T3 access and transcript evidence (read-only)

Observation date: 2026-08-24 (Asia/Dhaka). This is a local metadata-only check. Counts are point-in-time logical file bytes and can change while T3 Code or Edge is running.

## Result

T3 Code is installed and running locally, but a supported transcript export/API or an owner-shared transcript is **not verified** from the authorized local scope. The available stores are Electron/Chromium IndexedDB/LevelDB/blob/session stores and browser History; their records were not opened or decoded. No standalone transcript/export artifact was found by safe filename inspection.

The app bundle contains generic session/transcript/conversation/export/share implementation strings. Those strings are not a T3 contract or proof of an enabled export endpoint; they may come from bundled dependencies. No documented endpoint, export file, or owner-provided share URL was available to verify.

## Safe-label inventory

Exact user and browser paths are intentionally omitted. Labels below identify the inspected roots without disclosing sensitive paths or content.

| Safe label | Metadata observed | Schema / availability status |
| --- | --- | --- |
| `T3-MAC-SUPPORT` | Present; 9,430 regular files; 769,221,949 bytes at the final snapshot. | Electron user-data root available locally. Contains app cache/state classes; no transcript-named file, `.jsonl`, Markdown, CSV, ZIP, or text export was found by filename-only scan. |
| `T3-MAC-IDB-LEVELDB` | 8 regular files; 6,724,739 bytes. `LOCK` exists (0 bytes). | Present and active-looking Chromium IndexedDB LevelDB store; opaque schema/records, intentionally not decoded. |
| `T3-MAC-IDB-BLOB` | 125 regular files; 76,919,424 bytes. | Present blob side-store for the app IndexedDB; opaque, intentionally not decoded. |
| `T3-MAC-SESSION-STORAGE` | 6 regular files; 23,391 bytes. `LOCK` exists (0 bytes). | Present Chromium session store; opaque, intentionally not decoded. |
| `T3-NIGHTLY-BUNDLE` | App bundle present; bundle id `com.t3tools.t3code`, version `0.0.34-nightly.20260824.1176`; packaged `app.asar` is 216,015,133 bytes. | Installed metadata is readable. A T3 server process and loopback listener on port 3773 were observed; this establishes local availability, not transcript authorization or export support. |
| `EDGE-PROFILE1-REMOTE-T3-IDB` | 6 regular files; 140,667 bytes. | Remote T3-origin IndexedDB LevelDB store present; opaque schema/records, intentionally not decoded. |
| `EDGE-PROFILE1-REMOTE-T3-BLOB` | 3 regular files; 439,924 bytes. | Remote T3-origin blob side-store present; opaque, intentionally not decoded. |
| `EDGE-PROFILE1-LOCAL-T3-IDB` | 5 regular files; 4,034 bytes. | Local loopback T3-origin IndexedDB LevelDB store present; opaque, intentionally not decoded. |
| `EDGE-PROFILE1-ALT-LOCAL-T3-IDB` | 5 regular files; 4,048 bytes. | Alternate local-address T3-origin IndexedDB LevelDB store present; opaque, intentionally not decoded. |
| `EDGE-PROFILE1-HISTORY` | History file: 22,216,704 bytes; journal: 12,824 bytes. `file` identifies SQLite 3.x, schema version 4. | Browser History metadata is available, but no rows were queried. It cannot establish a transcript/share URL under this audit boundary. |

## Exact exclusions

- No IndexedDB, LevelDB, blob, SQLite, WebStorage, HTTP-storage, cache, or application database was opened, decoded, queried, exported, or copied.
- No prompts, transcript text, conversation records, URLs from History, cookies, tokens, credentials, account identifiers, or private owner content were read or included.
- T3 `Cookies`/`Cookies-journal`, Trust Tokens, HTTP storage, cache databases, app Preferences/Local State, Edge Local Storage/Session Storage bodies, and all unrelated Edge origins were metadata-only or excluded.
- No ACL, permission, lock, application, browser, network, or product state was changed. No API request or UI action was made to retrieve a transcript.
- Process command lines, user-home paths, and origin strings are omitted from this report; only safe labels and non-sensitive availability metadata are retained.

## Recommended authorized next step

Have the transcript owner use a supported T3 Code UI action to export or share the specific session, then provide the resulting file or share URL with explicit read scope. Verify that artifact/URL independently (metadata first) without touching local stores. If T3 has an official authenticated transcript API, the owner should provide its current vendor documentation and authorize that API path; do not infer an API from bundled strings or attempt local database extraction.
