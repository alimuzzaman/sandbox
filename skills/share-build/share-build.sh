#!/usr/bin/env bash
# share-build.sh — POST-APPROVAL close-out for a WordPress plugin card.
#
# ⚠ Run ONLY after the user has confirmed the fix/feature is correct. This bundles
# the four close-out actions (it does NOT verify the work — that's the user's gate):
#   1. Move the card to "Done / Fixed" and mark it complete.
#   2. Write the branch name into the board's branch custom field (Free or Pro by label).
#   3. Build the dist zip (npm run dist-archive, or .distignore-aware zip).
#   4. Attach the zip and post a single close-out comment.
#
# Usage:
#   share-build.sh <project> <card-id-or-url> --branch <name> [--pro] [options]
#   share-build.sh <card-id-or-url> --branch <name> [...]      # project inferred from $PWD
#
#   --branch <name>  Branch to record in the custom field (required unless --no-field).
#   --pro            Use the Pro branch field instead of Free.
#   --no-build       Attach the existing dist zip without rebuilding.
#   --zip <path>     Attach this exact file.
#   --no-field       Skip the custom-field write (e.g. board has no field yet).
#   --no-move        Don't move/complete the card (attach + comment + field only).
#   --note "<txt>"   Extra HTML line in the close-out comment.
#   --dry-run        Print what would happen; make no changes.
#
# Requires the sandbox `fluentboards` skill + FLUENTBOARDS_* env vars.
set -euo pipefail

FB="/Applications/Workspace/GitHub/sandbox/skills/fluentboards/scripts"
GH="/Applications/Workspace/GitHub"
die() { echo "share-build: $*" >&2; exit 1; }

# ── Project registry: key|repo|main_php|build_kind ──────────────────────────
#   build_kind: npm-dist-archive | distignore
project_config() {
  case "$1" in
    xspeed)     echo "$GH/xspeed|xspeed.php|npm-dist-archive" ;;
    embedpress) echo "$GH/embedpress|embedpress.php|distignore" ;;
    betterdocs) echo "$GH/betterdocs|betterdocs.php|distignore" ;;
    *) return 1 ;;
  esac
}

# ── Board registry: board_id|done_stage|free_field|pro_field (0 = none) ──────
board_config() {
  case "$1" in
    57) echo "1960|2038|2039" ;;   # xSpeed Development
    17) echo "145|0|0" ;;          # EmbedPress — no branch custom fields yet
    35) echo "0|0|0" ;;            # BetterDocs — unknown; fill when needed
    *) return 1 ;;
  esac
}

infer_project() {
  local root; root="$(cd "$1" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)" || return 1
  basename "$root"
}

# ── Args ────────────────────────────────────────────────────────────────────
[ $# -ge 1 ] || die "usage: share-build.sh <project> <card> --branch <name> [--pro] [opts]"
PROJECT=""; CARD=""
if project_config "$1" >/dev/null 2>&1; then PROJECT="$1"; shift; fi
[ $# -ge 1 ] || die "missing card id/url"
CARD="$1"; shift
if [ -z "$PROJECT" ]; then
  PROJECT="$(infer_project "$PWD")" || die "no project and can't infer from \$PWD"
  project_config "$PROJECT" >/dev/null 2>&1 || die "project '$PROJECT' not in registry"
fi

BRANCH=""; PRO=0; BUILD=1; ZIP=""; NOTE=""; DO_FIELD=1; DO_MOVE=1; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --branch)  BRANCH="${2:-}"; shift ;;
    --pro)     PRO=1 ;;
    --no-build) BUILD=0 ;;
    --zip)     ZIP="${2:-}"; shift ;;
    --no-field) DO_FIELD=0 ;;
    --no-move) DO_MOVE=0 ;;
    --note)    NOTE="${2:-}"; shift ;;
    --dry-run) DRY=1 ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

IFS='|' read -r REPO MAIN_PHP KIND <<<"$(project_config "$PROJECT")"
[ -d "$REPO" ] || die "repo not found: $REPO"
SLUG="$(basename "$REPO")"
VER="$(grep -iE "^\s*\*?\s*Version:" "$REPO/$MAIN_PHP" | head -1 | sed -E 's/.*Version:[[:space:]]*//; s/[[:space:]]+$//')"
[ -n "$VER" ] || die "could not read Version from $MAIN_PHP"

# Resolve board + stage/field ids from the card.
read -r BOARD TASK < <(bash "$FB/resolve.sh" "$CARD" 2>/dev/null | tr '\t' ' ') || true
[ -n "${BOARD:-}" ] && [ -n "${TASK:-}" ] || die "could not resolve card '$CARD' to board/task"
IFS='|' read -r DONE_STAGE FREE_FIELD PRO_FIELD <<<"$(board_config "$BOARD" || echo "0|0|0")"
FIELD_ID=$([ "$PRO" -eq 1 ] && echo "$PRO_FIELD" || echo "$FREE_FIELD")

run() { if [ "$DRY" -eq 1 ]; then echo "DRY: $*" >&2; else eval "$@"; fi; }

echo "▸ project=$SLUG v$VER  board=$BOARD task=$TASK  done=$DONE_STAGE field=$FIELD_ID branch=${BRANCH:-—}" >&2

# Boards with no branch custom field (e.g. EmbedPress, board 17) simply skip the
# field write — no error. The branch still goes in the close-out comment.
if [ "$DO_FIELD" -eq 1 ] && [ -n "$BRANCH" ] && [ "${FIELD_ID:-0}" = "0" ]; then
  echo "ℹ board $BOARD has no branch custom field — skipping field write (branch noted in comment)" >&2
  DO_FIELD=0
fi

# ── 1. Move + complete ──────────────────────────────────────────────────────
# NOTE: the shell move-task.sh sends a {newStageId,...} body that this FB version
# (1.95) rejects with "Invalid Value"/500 — the MCP move-task tool works, but a
# script can't call MCP. So we move via the REST shape the MCP tool uses:
# {board_id, stage_id, ...} on the task move endpoint. If this 500s, fall back to
# moving the task through the MCP tool by hand (see the share-build SKILL.md note).
if [ "$DO_MOVE" -eq 1 ] && [ "${DONE_STAGE:-0}" != "0" ]; then
  MOVE_JSON="$(BID="$BOARD" SID="$DONE_STAGE" python3 -c 'import json,os;print(json.dumps({"board_id":int(os.environ["BID"]),"stage_id":int(os.environ["SID"]),"position":1}))')"
  if [ "$DRY" -eq 1 ]; then echo "DRY: move task $TASK → stage $DONE_STAGE (board $BOARD)" >&2
  else bash "$FB/request.sh" PUT "/projects/$BOARD/tasks/$TASK/move-task" "$MOVE_JSON" >/dev/null 2>&1 \
       || echo "⚠ move-task REST failed — move $TASK to stage $DONE_STAGE via the MCP move-task tool manually" >&2; fi
  run "bash '$FB/update-task.sh' '$TASK' status closed >/dev/null"
fi

# ── 2. Branch → custom field ────────────────────────────────────────────────
if [ "$DO_FIELD" -eq 1 ] && [ -n "$BRANCH" ] && [ "${FIELD_ID:-0}" != "0" ]; then
  FIELD_JSON="$(FIELD_ID="$FIELD_ID" BRANCH="$BRANCH" python3 -c 'import json,os;print(json.dumps({"custom_field_id":int(os.environ["FIELD_ID"]),"value":os.environ["BRANCH"]}))')"
  if [ "$DRY" -eq 1 ]; then echo "DRY: POST /projects/$BOARD/tasks/$TASK/custom-fields  $FIELD_JSON" >&2
  else bash "$FB/request.sh" POST "/projects/$BOARD/tasks/$TASK/custom-fields" "$FIELD_JSON" >/dev/null; fi
fi

# ── 3. Build zip ────────────────────────────────────────────────────────────
build_npm() { ( cd "$REPO" && npm run dist-archive ) >&2 || die "npm run dist-archive failed"; echo "$REPO/dist/$SLUG.$VER.zip"; }
build_distignore() {
  command -v rsync >/dev/null || die "rsync required"; command -v zip >/dev/null || die "zip required"
  local stage out ex; stage="$(mktemp -d)"; out="$REPO/dist/$SLUG.$VER.zip"; mkdir -p "$REPO/dist"; ex="$stage/.ex"
  { echo ".git"; echo ".git/*"; echo "dist"; echo "dist/*"; echo "node_modules/*";
    [ -f "$REPO/.distignore" ] && grep -vE '^\s*(#|$)' "$REPO/.distignore"; } > "$ex"
  rsync -a --exclude-from="$ex" "$REPO/" "$stage/$SLUG/" >&2 || die "rsync failed"
  ( cd "$stage" && zip -rqX "$out" "$SLUG" ) || die "zip failed"; rm -rf "$stage"; echo "$out"
}
if [ -z "$ZIP" ]; then
  if [ "$BUILD" -eq 1 ] && [ "$DRY" -eq 0 ]; then
    case "$KIND" in npm-dist-archive) ZIP="$(build_npm)";; distignore) ZIP="$(build_distignore)";; *) die "bad kind";; esac
  else
    ZIP="$REPO/dist/$SLUG.$VER.zip"
  fi
fi
[ "$DRY" -eq 1 ] || [ -f "$ZIP" ] || die "zip not found: $ZIP"

# ── 4. Attach + close-out comment ───────────────────────────────────────────
SIZE=$([ -f "$ZIP" ] && du -h "$ZIP" | cut -f1 | tr -d ' ' || echo "?")
run "bash '$FB/upload-attachment.sh' '$TASK' '$ZIP' >/dev/null"
BODY="<p><strong>✅ Closed.</strong> Build attached: <code>$(basename "$ZIP")</code> ($SLUG v$VER, $SIZE).</p>"
[ -n "$BRANCH" ] && BODY="$BODY<p>Branch: <code>$BRANCH</code></p>"
[ -n "$NOTE" ]   && BODY="$BODY<p>$NOTE</p>"
run "bash '$FB/post-comment.sh' '$TASK' '$BODY' >/dev/null"

echo "✓ [$SLUG] card $TASK closed — branch=${BRANCH:-—}, zip=$(basename "$ZIP")" >&2
