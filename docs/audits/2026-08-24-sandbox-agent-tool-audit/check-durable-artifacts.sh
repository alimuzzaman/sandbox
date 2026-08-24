#!/usr/bin/env bash
set -euo pipefail

audit_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
status=0

scan_content() {
	local label=$1
	local pattern=$2
	local matches

	matches=$(cd "$audit_dir" && rg --hidden --files-with-matches --pcre2 \
		-g '*.md' -g '*.json' -g '*.jsonl' -g '*.tsv' \
		-- "$pattern" . || true)
	if [[ -n "$matches" ]]; then
		printf 'FAIL %s\n%s\n' "$label" "$matches" >&2
		status=1
	fi
}

raw_uuid='[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
source_filename='roll''out-[0-9]{4}-[0-9]{2}-[0-9]{2}[^[:space:]`)]*\.jsonl'
private_home='/(Users|home)/[^/[:space:]`]+'
raw_source_fields='(?im)^(# Transcript\b|Thread:|Transcript ID:|Session ID:|Rollout file:|Source file:[[:space:]]*rollout-)'
raw_payload_fields='(?i)(raw_(transcript|prompt|command|arguments?|output)|transcript_(body|text|prose)|prompt_text|assistant_output|tool_(input|output)|command_(args|arguments|output))[[:space:]]*[:=]'
private_url='https?://(localhost|127\.[0-9.]+|10\.[0-9.]+|192\.168\.[0-9.]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9.]+|[^/[:space:]]+\.tst)([:/]|$)|https?://[^[:space:]]+[?&](token|key|auth|cookie|signature)='
secret_value="-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|Bearer[[:space:]]+[A-Za-z0-9._~+/-]{12,}|eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}|(?i)(password|passwd|api[_-]?key|private[_-]?key|access[_-]?token|refresh[_-]?token|cookie)[[:space:]]*[:=][[:space:]]*[\"']?[A-Za-z0-9._~+/-]{8,}"

scan_content 'raw UUID source identifier' "$raw_uuid"
scan_content 'raw source filename' "$source_filename"
scan_content 'private home path' "$private_home"
scan_content 'raw transcript source field' "$raw_source_fields"
scan_content 'raw transcript or command payload field' "$raw_payload_fields"
scan_content 'private or credential-bearing URL' "$private_url"
scan_content 'secret-like value' "$secret_value"

name_matches=$(cd "$audit_dir" && find . -type f -print | rg -- "$raw_uuid|$source_filename|$private_home" || true)
if [[ -n "$name_matches" ]]; then
	printf 'FAIL forbidden filename\n%s\n' "$name_matches" >&2
	status=1
fi

if [[ "$status" -ne 0 ]]; then
	exit "$status"
fi

printf 'PASS durable audit forbidden-field scan\n'
