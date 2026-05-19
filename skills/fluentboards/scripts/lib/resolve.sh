#!/bin/bash
# URL / ID resolver for the fluentboards skill.
# Sourced by action scripts that accept a <url-or-id> argument.
#
# Public functions:
#   fb_resolve INPUT
#       Sets globals FB_BOARD_ID and FB_TASK_ID.
#       Exits 2 on failure with a stderr trace of tried strategies.
#
# Accepted INPUT shapes:
#   - Full wp-admin URL:  https://host/wp-admin/admin.php?page=fluent-boards#/boards/35/tasks/80927-slug...
#   - Short URL:          https://host//fbs-80927  or  https://host/fbs-80927
#   - "board/task":       35/80927
#   - Bare task ID:       80927
#
# Resolve order when board_id is unknown:
#   1. Cache (${TMPDIR:-/tmp}/fb-resolve-cache.txt, 1h TTL)
#   2. HTML scrape $SITE/fbs-{task_id} — only works when the BetterLinks short
#      link has been created for that task (it's a site-specific integration,
#      not a Fluentboards feature). 404 is the common case; we fall through.
#   3. Iterate GET /projects and per-board GET /projects/{B}/tasks (slow; last
#      resort; suppressible with FB_RESOLVE_NO_ITERATE=1).
#
# Fast path: pass the full wp-admin URL or the "B/T" form — no network needed.

FB_RESOLVE_CACHE="${TMPDIR:-/tmp}/fb-resolve-cache.txt"
FB_RESOLVE_TTL=3600  # seconds

FB_BOARD_ID=""
FB_TASK_ID=""

# Extract board_id and task_id from a full wp-admin deep link.
# Sets FB_BOARD_ID / FB_TASK_ID on success. Returns 0/1.
_fb_parse_wpadmin_url() {
  local url="$1"
  # Look for #/boards/{B}/tasks/{T} — task_id is bare number (strip -slug)
  if [[ "$url" =~ \#/boards/([0-9]+)/tasks/([0-9]+) ]]; then
    FB_BOARD_ID="${BASH_REMATCH[1]}"
    FB_TASK_ID="${BASH_REMATCH[2]}"
    return 0
  fi
  return 1
}

# Extract task_id from a short URL.  Handles optional double slash.
# Sets FB_TASK_ID on success. FB_BOARD_ID is left empty.
_fb_parse_short_url() {
  local url="$1"
  if [[ "$url" =~ /+fbs-([0-9]+) ]]; then
    FB_TASK_ID="${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

# Lookup a task_id in the resolver cache. Prints board_id if fresh, else nothing.
_fb_cache_get() {
  local task_id="$1" now line cached_id cached_ts
  [ -f "$FB_RESOLVE_CACHE" ] || return 1
  now=$(date +%s)
  while IFS=$'\t' read -r cached_id cached_board cached_ts; do
    if [ "$cached_id" = "$task_id" ]; then
      if [ $(( now - cached_ts )) -lt "$FB_RESOLVE_TTL" ]; then
        printf '%s' "$cached_board"
        return 0
      fi
    fi
  done < "$FB_RESOLVE_CACHE"
  return 1
}

# Add a task_id→board_id mapping to the cache (appends; never grows past 500 lines).
_fb_cache_put() {
  local task_id="$1" board_id="$2" now
  now=$(date +%s)
  {
    # Concurrency-safe append if flock is available.
    if command -v flock >/dev/null 2>&1; then
      flock -x 200
    fi
    printf '%s\t%s\t%s\n' "$task_id" "$board_id" "$now" >> "$FB_RESOLVE_CACHE"
    # Trim to last 500 entries to prevent unbounded growth.
    if [ "$(wc -l < "$FB_RESOLVE_CACHE" 2>/dev/null || echo 0)" -gt 500 ]; then
      tail -500 "$FB_RESOLVE_CACHE" > "${FB_RESOLVE_CACHE}.tmp" && mv "${FB_RESOLVE_CACHE}.tmp" "$FB_RESOLVE_CACHE"
    fi
  } 200>>"$FB_RESOLVE_CACHE"
}

# Fetch the short URL HTML and look for board_id=NNN or "board_id":NNN.
# Returns 0 with board_id on stdout if found.
_fb_resolve_via_html() {
  local task_id="$1" body status curl_exit
  local url="$FB_SITE/fbs-${task_id}"
  fb_vlog "resolve: GET $url (html scrape)"

  body=$(curl --silent --show-error --max-time 15 --location \
    --user "${FB_USER}:${FB_APP_PASSWORD}" \
    --write-out '\n__FB_STATUS__:%{http_code}' \
    "$url" 2>&1)
  curl_exit=$?
  [ $curl_exit -ne 0 ] && return 1
  status="${body##*__FB_STATUS__:}"; status="${status%%[^0-9]*}"
  body="${body%__FB_STATUS__:*}"
  [ "$status" -ge 400 ] 2>/dev/null && return 1

  # Try several shapes — Fluentboards may embed any of these.
  local b
  b=$(printf '%s' "$body" | grep -oE '(board_id|boardId)["'\'':= ]+[0-9]+' | head -1 | grep -oE '[0-9]+$')
  [ -n "$b" ] && { printf '%s' "$b"; return 0; }
  # Try fragment embedded in <a href="...#/boards/B/tasks/T">
  b=$(printf '%s' "$body" | grep -oE '#/boards/[0-9]+/tasks/'"$task_id" | head -1 | grep -oE '/boards/[0-9]+' | grep -oE '[0-9]+$')
  [ -n "$b" ] && { printf '%s' "$b"; return 0; }
  return 1
}

# Iterate boards and per-board task lists looking for task_id.
# Prints board_id on stdout if found. Last resort (N+1 API calls).
_fb_resolve_via_iterate() {
  local task_id="$1" boards_json board_id tasks_json
  fb_vlog "resolve: listing all boards and scanning tasks (slow — last resort)"

  # Memoise the JSON backend once (also guards against unset under `set -u`).
  _fb_json_backend

  # fb_curl prints body to stdout on success; swallow its stderr so we can rethrow our own.
  boards_json=$(fb_curl GET "/projects?per_page=100" 2>/dev/null) || return 1

  # Boards list lives at .boards.data[].id on this install, .data[].id on others —
  # tolerate both. Fall back to python3 or grep as needed.
  local board_ids
  if [ "$FB_JSON_BACKEND" = "jq" ]; then
    board_ids=$(printf '%s' "$boards_json" | jq -r '(.boards.data // .data // [])[]?.id // empty' 2>/dev/null)
  elif command -v python3 >/dev/null 2>&1; then
    board_ids=$(printf '%s' "$boards_json" | python3 -c '
import json, sys
try: d=json.load(sys.stdin)
except: sys.exit(0)
items = (d.get("boards") or {}).get("data") if isinstance(d.get("boards"), dict) else None
if items is None: items = d.get("data") or []
for b in items:
    if isinstance(b, dict) and "id" in b: print(b["id"])
')
  else
    board_ids=$(printf '%s' "$boards_json" | grep -oE '"id"[[:space:]]*:[[:space:]]*[0-9]+' | grep -oE '[0-9]+')
  fi

  for board_id in $board_ids; do
    fb_vlog "resolve: checking board $board_id for task $task_id"
    tasks_json=$(fb_curl GET "/projects/${board_id}/tasks?per_page=200" 2>/dev/null) || continue
    if printf '%s' "$tasks_json" | grep -qE "\"id\"[[:space:]]*:[[:space:]]*${task_id}\\b"; then
      printf '%s' "$board_id"
      return 0
    fi
  done
  return 1
}

# Main entry. Populates FB_BOARD_ID / FB_TASK_ID. Exits 2 on failure.
fb_resolve() {
  local input="$1"
  [ -n "$input" ] || { fb_error "resolve: empty input"; exit 2; }

  FB_BOARD_ID=""
  FB_TASK_ID=""

  # 1) Full wp-admin URL with fragment.
  if _fb_parse_wpadmin_url "$input"; then
    fb_vlog "resolve: parsed full URL → board=$FB_BOARD_ID task=$FB_TASK_ID"
    _fb_cache_put "$FB_TASK_ID" "$FB_BOARD_ID" 2>/dev/null || true
    return 0
  fi

  # 2) "B/T" explicit form.
  if [[ "$input" =~ ^([0-9]+)/([0-9]+)$ ]]; then
    FB_BOARD_ID="${BASH_REMATCH[1]}"
    FB_TASK_ID="${BASH_REMATCH[2]}"
    fb_vlog "resolve: parsed B/T → board=$FB_BOARD_ID task=$FB_TASK_ID"
    _fb_cache_put "$FB_TASK_ID" "$FB_BOARD_ID" 2>/dev/null || true
    return 0
  fi

  # 3) Short URL.
  if _fb_parse_short_url "$input"; then
    fb_vlog "resolve: parsed short URL → task=$FB_TASK_ID (board unknown)"
  # 4) Bare integer.
  elif [[ "$input" =~ ^[0-9]+$ ]]; then
    FB_TASK_ID="$input"
    fb_vlog "resolve: bare integer → task=$FB_TASK_ID (board unknown)"
  else
    fb_error "resolve: could not parse input: $input"
    fb_log   "  expected one of:"
    fb_log   "    - full URL:   https://site/wp-admin/admin.php?page=fluent-boards#/boards/35/tasks/80927-slug"
    fb_log   "    - short URL:  https://site/fbs-80927"
    fb_log   "    - B/T:        35/80927"
    fb_log   "    - bare ID:    80927"
    exit 2
  fi

  # Need board_id. Try strategies in order.
  local b

  # a) Cache.
  if b=$(_fb_cache_get "$FB_TASK_ID"); then
    FB_BOARD_ID="$b"
    fb_vlog "resolve: cache hit — board=$FB_BOARD_ID"
    return 0
  fi

  # b) HTML scrape of the short URL.
  fb_vlog "resolve: trying HTML scrape"
  if b=$(_fb_resolve_via_html "$FB_TASK_ID"); then
    FB_BOARD_ID="$b"
    _fb_cache_put "$FB_TASK_ID" "$FB_BOARD_ID" 2>/dev/null || true
    fb_vlog "resolve: HTML scrape succeeded — board=$FB_BOARD_ID"
    return 0
  fi
  fb_vlog "resolve: HTML scrape did not find board_id"

  # c) Iterate boards (slow). Users can opt out by setting FB_RESOLVE_NO_ITERATE=1.
  if [ "${FB_RESOLVE_NO_ITERATE:-0}" = "1" ]; then
    fb_error "resolve: could not find board_id for task $FB_TASK_ID"
    fb_log   "  (iterate-boards fallback disabled via FB_RESOLVE_NO_ITERATE=1)"
    fb_log   "  hint: pass the full URL with board_id, or use 'B/T' form"
    exit 2
  fi

  fb_log "resolve: board_id unknown — scanning boards (this may take a moment)…"
  if b=$(_fb_resolve_via_iterate "$FB_TASK_ID"); then
    FB_BOARD_ID="$b"
    _fb_cache_put "$FB_TASK_ID" "$FB_BOARD_ID" 2>/dev/null || true
    fb_vlog "resolve: iterate succeeded — board=$FB_BOARD_ID"
    return 0
  fi

  fb_error "resolve: could not find task $FB_TASK_ID on any board"
  fb_log   "  tried: cache, HTML scrape, board iteration"
  fb_log   "  hint: verify the task exists and your user has access (FLUENTBOARDS_USER)"
  exit 2
}
