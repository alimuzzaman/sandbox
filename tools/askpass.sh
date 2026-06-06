#!/usr/bin/env sh
# SUDO_ASKPASS helper — pops a native macOS password dialog instead of prompting
# in the terminal. sudo calls this (via `sudo -A`) when it needs a password and
# passes the prompt text as $1 (set by the caller via `sudo -p`). The script must
# print the password on stdout; cancelling returns non-empty failure so sudo
# aborts cleanly. macOS only (uses osascript); elsewhere sudo falls back to the
# terminal prompt.
#
# We show $1 as the dialog BODY so the user understands WHY their password is
# needed — never a bare "Password:". The actual field is unlabeled + hidden.

# sudo's default prompt looks like "Password:" or "[sudo] password for user:".
# Treat those as "no real reason given" and substitute a clear explanation.
reason="${1:-}"
case "$reason" in
  ""|"Password:"|*"password for "*)
    reason="Sandbox would like to set up clean local URLs so your sites open at
http://<name>.sb instead of localhost:8188.

macOS asks for your password for this one-time change. It's all local, and you
can undo it anytime with  ./sb uninstall." ;;
esac

# Trailing "Password:" lines from sudo would duplicate the field; strip them.
reason="$(printf '%s' "$reason" | sed -e 's/[[:space:]]*Password:[[:space:]]*$//')"

# `note` icon (friendly info), not `caution` (the alarming yellow ⚠️). This is a
# routine setup step, so it should look routine.
osascript \
  -e "on run(a)" \
  -e "  set msg to item 1 of a" \
  -e "  display dialog msg & return & return & \"Your Mac password:\" default answer \"\" with title \"WPDeveloper Sandbox\" with icon note with hidden answer buttons {\"Not now\", \"Continue\"} default button \"Continue\"" \
  -e "  return text returned of result" \
  -e "end run" \
  -- "$reason" 2>/dev/null
