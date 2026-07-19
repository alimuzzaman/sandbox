#!/usr/bin/env bash
set -euo pipefail

json=false
short_name=""

while (($#)); do
    case "$1" in
        --json) json=true; shift ;;
        --short-name)
            [[ $# -ge 2 ]] || { echo "--short-name requires a value" >&2; exit 2; }
            short_name="$2"; shift 2 ;;
        --) shift; break ;;
        *) break ;;
    esac
done

description="$*"
[[ -n "$short_name" && -n "$description" ]] || {
    echo "Usage: $0 [--json] --short-name <slug> -- <description>" >&2
    exit 2
}

project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd -P)"
core_script="$project_root/.specify/scripts/bash/create-new-feature.sh"
[[ -x "$core_script" ]] || { echo "Missing executable $core_script" >&2; exit 1; }

dry_run="$($core_script --dry-run --json --short-name "$short_name" "$description")"
spec_file="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["SPEC_FILE"])' <<<"$dry_run")"
feature_num="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["FEATURE_NUM"])' <<<"$dry_run")"
feature_dir="$(dirname "$spec_file")"
prd_file="$feature_dir/prd.md"

[[ ! -e "$feature_dir" ]] || { echo "Feature directory already exists: $feature_dir" >&2; exit 1; }
template="$project_root/.specify/extensions/prd/templates/prd-template.md"
[[ -f "$template" ]] || { echo "Resolved PRD template is not a file: $template" >&2; exit 1; }

mkdir -p "$feature_dir"
cp "$template" "$prd_file"
relative_dir="${feature_dir#"$project_root"/}"
python3 - "$project_root/.specify/feature.json" "$relative_dir" <<'PY'
import json
import sys
from pathlib import Path

target = Path(sys.argv[1])
target.write_text(json.dumps({"feature_directory": sys.argv[2]}, indent=2) + "\n")
PY

if $json; then
    python3 - "$prd_file" "$relative_dir" "$feature_num" <<'PY'
import json
import sys

print(json.dumps({"PRD_FILE": sys.argv[1], "FEATURE_DIRECTORY": sys.argv[2], "FEATURE_NUM": sys.argv[3]}))
PY
else
    printf 'PRD_FILE: %s\nFEATURE_DIRECTORY: %s\nFEATURE_NUM: %s\n' "$prd_file" "$relative_dir" "$feature_num"
fi
