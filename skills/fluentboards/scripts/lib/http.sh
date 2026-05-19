#!/bin/bash
# HTTP wrapper + JSON helpers for the fluentboards skill.
# Sourced by action scripts. Assumes lib/auth.sh is already sourced and
# fb_require_credentials has populated FB_USER / FB_APP_PASSWORD / FB_API_BASE.
#
# Public functions:
#   fb_curl METHOD PATH [BODY_JSON | @file]
#       Authenticated JSON request. Prints response body to stdout.
#       Sets FB_HTTP_STATUS. Returns 3 on HTTP ≥ 400 (with stderr message).
#       PATH may be absolute ("/projects") or include query ("?per_page=5").
#       BODY omitted for GET/DELETE. "@file" reads body from a file.
#
#   fb_curl_multipart METHOD PATH FIELD FILE [FILE ...]
#       Multipart upload. FIELD is the form field name, e.g. "file[]".
#
#   fb_json_get JSON_STRING DOTTED_PATH
#       Extract a scalar from JSON. Uses jq → python3 → grep/sed fallback.
#       Prints the value (or nothing if missing).
#
#   fb_json_escape STRING
#       Escape a string for embedding in a JSON string literal.

FB_HTTP_STATUS=""

# Prefer jq, else python3, else a minimal grep-based parser.
# Sets FB_JSON_BACKEND once per process.
_fb_json_backend() {
  if [ -n "${FB_JSON_BACKEND:-}" ]; then return 0; fi
  if command -v jq >/dev/null 2>&1; then
    FB_JSON_BACKEND=jq
  elif command -v python3 >/dev/null 2>&1; then
    FB_JSON_BACKEND=python3
    fb_vlog "using python3 for JSON parsing (install jq for nicer output)"
  else
    FB_JSON_BACKEND=grep
    fb_warn "neither jq nor python3 found — falling back to grep-based JSON parsing (fragile)"
  fi
  export FB_JSON_BACKEND
}

# Extract a value at DOTTED_PATH from a JSON document on stdin or $1.
# Examples of DOTTED_PATH: "data.id", "message", "task.board_id"
fb_json_get() {
  local json="$1" path="$2"
  _fb_json_backend
  case "$FB_JSON_BACKEND" in
    jq)
      printf '%s' "$json" | jq -r --arg p "$path" '
        ($p|split(".")) as $parts
        | reduce $parts[] as $k (.; if type=="object" then .[$k] // empty else empty end)
        | if . == null then empty else . end
      ' 2>/dev/null
      ;;
    python3)
      printf '%s' "$json" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
path = sys.argv[1].split(".")
cur = data
for k in path:
    if isinstance(cur, dict) and k in cur:
        cur = cur[k]
    else:
        sys.exit(0)
if cur is None:
    sys.exit(0)
if isinstance(cur, (dict, list)):
    print(json.dumps(cur))
else:
    print(cur)
' "$path" 2>/dev/null
      ;;
    grep)
      # Only handles top-level scalar keys; good enough for "message" and "id".
      local key="${path##*.}"
      printf '%s' "$json" | grep -o "\"${key}\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | head -1 | sed 's/.*:[[:space:]]*"\(.*\)"$/\1/'
      ;;
  esac
}

# JSON-escape a string for embedding between quotes in a JSON literal.
# Input comes via printf '%s' (no trailing newline), so jq -Rs . produces the
# exact quoted literal we want — no slicing required.
fb_json_escape() {
  local s="$1"
  _fb_json_backend
  case "$FB_JSON_BACKEND" in
    jq)
      printf '%s' "$s" | jq -Rs .
      ;;
    python3)
      printf '%s' "$s" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
      ;;
    *)
      # Last-resort escape: backslashes, double quotes, control chars.
      s=${s//\\/\\\\}
      s=${s//\"/\\\"}
      s=${s//$'\n'/\\n}
      s=${s//$'\r'/\\r}
      s=${s//$'\t'/\\t}
      printf '"%s"' "$s"
      ;;
  esac
}

# Explain a status code in plain English for the stderr message.
_fb_status_hint() {
  case "$1" in
    400) echo "bad request — check the JSON body and required fields" ;;
    401) echo "unauthorized — check FLUENTBOARDS_USER and FLUENTBOARDS_APP_PASSWORD" ;;
    403) echo "forbidden — the user lacks permission for this board/action (or the feature is Pro-only)" ;;
    404) echo "not found — the board/task/resource id is wrong or was deleted" ;;
    409) echo "conflict — resource state prevents this action" ;;
    422) echo "validation failed — read the server's 'message' field above for specifics" ;;
    429) echo "rate-limited — retried once already; try again in a moment" ;;
    5*)  echo "server error — retried once already; Fluentboards/WordPress may be down or misconfigured" ;;
    *)   echo "unexpected status" ;;
  esac
}

# Curl exit-code explainer (selected codes that actually happen in practice).
_fb_curl_hint() {
  case "$1" in
    6)  echo "could not resolve host — check FLUENTBOARDS_SITE and DNS" ;;
    7)  echo "could not connect — check the site URL and network" ;;
    28) echo "timed out after 30s — site may be slow or hung" ;;
    35) echo "TLS handshake failed — site may have a cert problem" ;;
    *)  echo "curl error $1" ;;
  esac
}

# Build a URL from FB_API_BASE + PATH.
# - Absolute URLs pass through unchanged.
# - Paths starting with /wp-json/... are anchored at FB_SITE (supports WP core
#   and other plugin namespaces, not just Fluentboards).
# - Other leading-slash paths are anchored at FB_API_BASE (the v2 namespace).
# - Bare paths are joined onto FB_API_BASE with a separator.
_fb_build_url() {
  local p="$1"
  case "$p" in
    http://*|https://*) printf '%s' "$p" ;;
    /wp-json/*)         printf '%s%s' "$FB_SITE" "$p" ;;
    /*)                 printf '%s%s' "$FB_API_BASE" "$p" ;;
    *)                  printf '%s/%s' "$FB_API_BASE" "$p" ;;
  esac
}

# Core JSON request. Retries once on 429/5xx with 1s backoff.
fb_curl() {
  local method="$1" path="$2" body="${3:-}"
  local url; url=$(_fb_build_url "$path")
  local body_file="" is_temp=0

  if [ -n "$body" ]; then
    if [ "${body:0:1}" = "@" ]; then
      body_file="${body:1}"
      [ -f "$body_file" ] || { fb_error "body file not found: $body_file"; return 1; }
    else
      body_file=$(mktemp -t fb-body.XXXXXX)
      is_temp=1
      printf '%s' "$body" > "$body_file"
    fi
  fi

  local attempt=0 max_attempts=2 response headers_file status curl_exit
  while : ; do
    attempt=$((attempt + 1))
    fb_vlog "$method $url (attempt $attempt)"
    headers_file=$(mktemp -t fb-hdr.XXXXXX)

    local curl_args=(
      --silent --show-error --max-time 30
      --user "${FB_USER}:${FB_APP_PASSWORD}"
      --request "$method"
      --header "Accept: application/json"
      --dump-header "$headers_file"
      --write-out '\n__FB_STATUS__:%{http_code}'
      "$url"
    )
    if [ -n "$body_file" ]; then
      curl_args+=(--header "Content-Type: application/json" --data-binary "@$body_file")
    fi

    response=$(curl "${curl_args[@]}" 2>&1)
    curl_exit=$?

    if [ $curl_exit -ne 0 ]; then
      rm -f "$headers_file"
      [ $is_temp -eq 1 ] && rm -f "$body_file"
      fb_error "network: $(_fb_curl_hint "$curl_exit")"
      fb_error "  $method $url"
      return 3
    fi

    status="${response##*__FB_STATUS__:}"
    status="${status%%[^0-9]*}"
    response="${response%__FB_STATUS__:*}"
    # Trim the trailing newline we inserted before __FB_STATUS__.
    response="${response%$'\n'}"
    FB_HTTP_STATUS="$status"
    rm -f "$headers_file"

    # Retry once on 429 or 5xx.
    if [ "$attempt" -lt "$max_attempts" ]; then
      case "$status" in
        429|5*) fb_warn "got HTTP $status, retrying in 1s…"; sleep 1; continue ;;
      esac
    fi
    break
  done

  [ $is_temp -eq 1 ] && rm -f "$body_file"

  # Always print body to stdout so callers can inspect it even on error.
  printf '%s\n' "$response"

  if [ "${status:-0}" -ge 400 ] 2>/dev/null; then
    local server_msg; server_msg=$(fb_json_get "$response" "message")
    fb_error "HTTP $status $(_fb_status_hint "$status")"
    fb_error "  $method $url"
    [ -n "$server_msg" ] && fb_error "  server says: $server_msg"
    return 3
  fi
  return 0
}

# Multipart upload. Each extra positional arg is appended as FIELD=@FILE.
# Example: fb_curl_multipart POST /projects/35/tasks/80927/add-task-attachment-file "file[]" /tmp/a.png /tmp/b.png
fb_curl_multipart() {
  local method="$1" path="$2" field="$3"; shift 3
  local url; url=$(_fb_build_url "$path")
  local -a form_args=()
  local f
  for f in "$@"; do
    [ -f "$f" ] || { fb_error "file not found: $f"; return 1; }
    form_args+=(--form "${field}=@${f}")
  done

  fb_vlog "$method $url (multipart, ${#form_args[@]} parts in field '$field')"

  local response status curl_exit
  response=$(curl --silent --show-error --max-time 120 \
    --user "${FB_USER}:${FB_APP_PASSWORD}" \
    --request "$method" \
    --header "Accept: application/json" \
    --write-out '\n__FB_STATUS__:%{http_code}' \
    "${form_args[@]}" \
    "$url" 2>&1)
  curl_exit=$?

  if [ $curl_exit -ne 0 ]; then
    fb_error "network: $(_fb_curl_hint "$curl_exit")"
    fb_error "  $method $url"
    return 3
  fi

  status="${response##*__FB_STATUS__:}"
  status="${status%%[^0-9]*}"
  response="${response%__FB_STATUS__:*}"
  response="${response%$'\n'}"
  FB_HTTP_STATUS="$status"

  printf '%s\n' "$response"

  if [ "${status:-0}" -ge 400 ] 2>/dev/null; then
    local server_msg; server_msg=$(fb_json_get "$response" "message")
    fb_error "HTTP $status $(_fb_status_hint "$status")"
    fb_error "  $method $url"
    [ -n "$server_msg" ] && fb_error "  server says: $server_msg"
    return 3
  fi
  return 0
}
