#!/usr/bin/env sh
# SUDO_ASKPASS helper — pops a native macOS password dialog instead of prompting
# in the terminal. sudo calls this (via `sudo -A`) when it needs a password; the
# script must print the password on stdout. The prompt text comes from $1 (or
# sudo's default). Cancelling the dialog returns non-empty failure so sudo aborts
# cleanly. macOS only (uses osascript); on other platforms sudo falls back to the
# terminal prompt.
prompt="${1:-Sandbox needs your administrator password:}"
osascript -e "display dialog \"$prompt\" default answer \"\" with title \"Sandbox\" with icon caution with hidden answer" \
          -e "text returned of result" 2>/dev/null
