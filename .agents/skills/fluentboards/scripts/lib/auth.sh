#!/bin/bash
# Shared credentials loader + global-flag parser for the fluentboards skill.
# Sourced by every action script. Not meant to be run standalone.
#
# After sourcing, callers typically do:
#     fb_strip_globals "$@"; set -- "${FB_ARGV[@]}"
#     fb_require_credentials
#
# Exposed globals after fb_require_credentials:
#   FB_SITE            - normalised site URL (no trailing slash)
#   FB_USER            - WP username
#   FB_APP_PASSWORD    - raw app password (may contain spaces)
#   FB_API_BASE        - "$FB_SITE/wp-json/fluent-boards/v2"
#   FB_VERBOSE         - "1" if --verbose was passed
#
# Exit codes the caller should preserve:
#   1  - missing env vars or bad usage
#   2  - resolve failure
#   3  - HTTP / network error

# Skill root = parent of scripts/lib/
if [ -z "${FB_SKILL_ROOT:-}" ]; then
  FB_SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  export FB_SKILL_ROOT
fi

FB_VERBOSE="${FB_VERBOSE:-0}"
FB_SITE_OVERRIDE=""
FB_USER_OVERRIDE=""

# Parse global flags out of "$@". Populates FB_ARGV with remaining args.
# Recognised flags: --verbose, --site=URL, --user=NAME
fb_strip_globals() {
  FB_ARGV=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --verbose) FB_VERBOSE=1 ;;
      --site=*)  FB_SITE_OVERRIDE="${1#--site=}" ;;
      --user=*)  FB_USER_OVERRIDE="${1#--user=}" ;;
      --) shift; FB_ARGV+=("$@"); return 0 ;;
      *)  FB_ARGV+=("$1") ;;
    esac
    shift
  done
}

# Log helpers — all diagnostics go to stderr so stdout stays machine-readable.
fb_log()   { printf '%s\n' "$*" >&2; }
fb_warn()  { printf 'warn: %s\n' "$*" >&2; }
fb_error() { printf 'error: %s\n' "$*" >&2; }
fb_vlog()  { [ "$FB_VERBOSE" = "1" ] && printf 'debug: %s\n' "$*" >&2; return 0; }

# Read a KEY=VALUE line from a file, strip surrounding quotes and leading 'export '.
# Usage: _fb_read_env_line FILE KEY
_fb_read_env_line() {
  local file="$1" key="$2" line
  [ -f "$file" ] || return 1
  line=$(grep -m1 "^[[:space:]]*\(export[[:space:]][[:space:]]*\)\?${key}=" "$file" 2>/dev/null | grep -v '^[[:space:]]*#' | head -1) || return 1
  [ -n "$line" ] || return 1
  line="${line#"${line%%[![:space:]]*}"}"      # ltrim
  line="${line#export }"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line#${key}=}"
  # Strip balanced surrounding quotes
  case "$line" in
    \"*\") line="${line#\"}"; line="${line%\"}" ;;
    \'*\') line="${line#\'}"; line="${line%\'}" ;;
  esac
  printf '%s\n' "$line"
}

# Resolve a single env var from: environment → rc files → .env files.
# Usage: _fb_resolve_var VARNAME
_fb_resolve_var() {
  local name="$1" val
  eval "val=\${$name:-}"
  if [ -n "$val" ]; then printf '%s\n' "$val"; return 0; fi

  local rc
  for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.profile"; do
    if val=$(_fb_read_env_line "$rc" "$name"); then
      [ -n "$val" ] && { printf '%s\n' "$val"; return 0; }
    fi
  done

  local env_file
  for env_file in "$PWD/.env" "$HOME/.env" "$HOME/.fluentboards"; do
    if val=$(_fb_read_env_line "$env_file" "$name"); then
      [ -n "$val" ] && { printf '%s\n' "$val"; return 0; }
    fi
  done

  return 1
}

# Ensure all three credentials are resolved. On failure, print a helpful
# message pointing at the README and exit 1.
fb_require_credentials() {
  FB_SITE="$FB_SITE_OVERRIDE"
  FB_USER="$FB_USER_OVERRIDE"
  FB_APP_PASSWORD=""

  [ -n "$FB_SITE" ] || FB_SITE=$(_fb_resolve_var FLUENTBOARDS_SITE || true)
  [ -n "$FB_USER" ] || FB_USER=$(_fb_resolve_var FLUENTBOARDS_USER || true)
  FB_APP_PASSWORD=$(_fb_resolve_var FLUENTBOARDS_APP_PASSWORD || true)

  local missing=()
  [ -z "$FB_SITE" ]         && missing+=("FLUENTBOARDS_SITE")
  [ -z "$FB_USER" ]         && missing+=("FLUENTBOARDS_USER")
  [ -z "$FB_APP_PASSWORD" ] && missing+=("FLUENTBOARDS_APP_PASSWORD")

  if [ ${#missing[@]} -gt 0 ]; then
    fb_error "Missing Fluentboards credentials: ${missing[*]}"
    fb_log   ""
    fb_log   "Set them in your shell rc or a .env file. The skill ships with a copy-pasteable"
    fb_log   "snippet — see: ${FB_SKILL_ROOT}/README.md"
    fb_log   ""
    fb_log   "Quick fix (zsh):"
    fb_log   "    export FLUENTBOARDS_SITE=\"https://projects.startise.com\""
    fb_log   "    export FLUENTBOARDS_USER=\"your-wp-username\""
    fb_log   "    export FLUENTBOARDS_APP_PASSWORD=\"abcd efgh ijkl mnop qrst uvwx\""
    exit 1
  fi

  # Normalise: strip trailing slashes from site URL
  while [ "${FB_SITE: -1}" = "/" ]; do FB_SITE="${FB_SITE%/}"; done
  case "$FB_SITE" in
    http://*|https://*) ;;
    *) fb_error "FLUENTBOARDS_SITE must start with http:// or https:// (got: $FB_SITE)"; exit 1 ;;
  esac

  FB_API_BASE="$FB_SITE/wp-json/fluent-boards/v2"
  export FB_SITE FB_USER FB_APP_PASSWORD FB_API_BASE FB_VERBOSE
  fb_vlog "credentials loaded for $FB_USER @ $FB_SITE"
}

# Print "board_id\ttask_id" — not part of auth, but exposed here so scripts
# that only need credentials don't have to also source resolve.sh.
# (The real implementation lives in lib/resolve.sh.)
