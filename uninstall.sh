#!/usr/bin/env sh
# Sandbox uninstaller — removes everything this sandbox set up:
#   • containers + DB volumes for every instance
#   • the HTTPS proxy + *.sb domain config (mkcert CA, dnsmasq, loopback alias)
#   • the MCP servers registered with Claude
#   • optionally, this install directory
#
# Run from the sandbox folder:  ./uninstall.sh   (add --yes to skip the prompt)
set -eu
cd "$(dirname "$0")"
[ -f sb ] || { echo "run this from the sandbox folder (no ./sb here)." >&2; exit 1; }
exec ./sb uninstall "$@"
