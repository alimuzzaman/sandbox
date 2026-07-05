#!/bin/bash
# Print the authenticated user's WordPress profile (ID, username, email, name).
#
# Fluentboards has no /me endpoint, and the /fluent-boards-users and
# /search-fluent-boards-users routes 404 on some installs (version / Pro
# gating). WordPress core's /wp/v2/users/me is the reliable way to discover
# the authenticated user's ID — app-password auth covers it automatically.
#
# Usage:
#   whoami.sh
#
# Stdout: JSON response from /wp/v2/users/me?context=edit
# Exit:   0 success, 1 missing creds, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

fb_require_credentials

# Absolute URL — fb_curl's _fb_build_url passes http(s):// through unchanged.
fb_curl GET "$FB_SITE/wp-json/wp/v2/users/me?context=edit"
