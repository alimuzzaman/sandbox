#!/usr/bin/env sh
# Sandbox installer — run this once after cloning the alim-dev branch:
#
#   git clone https://github.com/templately/sandbox.git
#   cd sandbox && ./install.sh
#
# It walks you through setup step by step: makes sure python3 is present (the
# only thing the CLI needs to start), then hands off to `./sb setup`, which
# checks Docker, boots WordPress, builds the MCP server, wires Claude, and
# offers to install anything else that's missing. Nothing is installed without
# your "y", and the base install needs no sudo password.
set -eu

# --- pretty output (degrade gracefully if no color) -----------------------
if [ -t 1 ]; then
    B="$(printf '\033[1m')"; DIM="$(printf '\033[2m')"; G="$(printf '\033[32m')"
    Y="$(printf '\033[33m')"; R="$(printf '\033[31m')"; N="$(printf '\033[0m')"
else
    B=""; DIM=""; G=""; Y=""; R=""; N=""
fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s▸ %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '\n  %s✗ %s%s\n' "$R" "$*" "$N" >&2; exit 1; }

# --- run from the repo root (where this script lives) ----------------------
cd "$(dirname "$0")"
[ -f sb ] || die "run this from the sandbox clone (no ./sb found here)."

say ""
say "${B}WPDeveloper Sandbox — installer${N}"
say "${DIM}A real local WordPress for Claude. This sets it up step by step.${N}"

# --- detect package manager (for the python3 offer) -----------------------
PM=""; PM_CMD=""
if command -v brew >/dev/null 2>&1; then
    PM="Homebrew"; PM_CMD="brew install python"
elif command -v apt-get >/dev/null 2>&1; then
    PM="apt"; PM_CMD="sudo apt-get install -y python3 python3-venv"
elif command -v dnf >/dev/null 2>&1; then
    PM="dnf"; PM_CMD="sudo dnf install -y python3 python3-virtualenv"
fi

# --- Step 1: python3 (the CLI's only hard prerequisite to start) ----------
step "Step 1/2  Checking python3"
if command -v python3 >/dev/null 2>&1; then
    ok "python3 found ($(python3 --version 2>&1))"
else
    warn "python3 not found — the sandbox CLI needs it to run."
    if [ -n "$PM_CMD" ] && [ -t 0 ]; then
        printf '  Install python3 via %s now? [y/N] ' "$PM"
        read ans
        case "$ans" in
            y|Y|yes|YES)
                say "  running: $PM_CMD"
                if sh -c "$PM_CMD"; then
                    command -v python3 >/dev/null 2>&1 \
                        && ok "python3 installed" \
                        || die "installed, but python3 isn't on PATH yet — open a new shell and re-run ./install.sh"
                else
                    die "install failed — run it manually, then re-run ./install.sh"
                fi ;;
            *) die "python3 is required. Install it, then re-run ./install.sh:
       $PM_CMD" ;;
        esac
    else
        die "python3 is required. Install it, then re-run ./install.sh:
       ${PM_CMD:-see https://www.python.org/downloads/}"
    fi
fi

# --- Step 2: hand off to ./sb setup (does Docker, WP, MCP, the rest) -------
step "Step 2/2  Running ./sb setup"
say "${DIM}  Checks Docker, boots WordPress, builds the MCP server, wires Claude.${N}"
say "${DIM}  It will offer to install anything else that's missing (Docker, etc.).${N}"
say ""
./sb setup

say ""
ok "Sandbox is ready."
say ""
say "  Next:"
say "    ./sb instances          # see your WordPress instance(s) + URLs"
say "    ./sb web                # open the browser dashboard"
say "    claude                  # start Claude — it can now drive your WordPress"
say ""
say "  ${DIM}Tip: ./sb domains setup  → trusted https://<name>.tst URLs (optional).${N}"
say ""
