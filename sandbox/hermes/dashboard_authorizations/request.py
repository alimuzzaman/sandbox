"""Create a pending authorization from one shipped, non-secret cron template."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from authorization_core import AuthorizationError, ensure_request, read_state, state_digest, view, write_state

PLUGIN_ROOT = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("SANDBOX_AUTHORIZATION_CONFIG", PLUGIN_ROOT / "sandbox-authorization-config.json"))
TEMPLATES = Path(os.environ.get("SANDBOX_AUTHORIZATION_TEMPLATES", PLUGIN_ROOT / "authorization-templates.json"))


def _config() -> dict:
    try:
        config = json.loads(CONFIG.read_text())
    except (OSError, ValueError) as exc:
        raise AuthorizationError("authorization companion is not configured") from exc
    if not isinstance(config, dict):
        raise AuthorizationError("authorization companion is not configured")
    return config


def _catalog(path: Path) -> dict[str, dict]:
    try:
        jobs = json.loads(path.read_text())["jobs"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AuthorizationError("authorization catalog is not configured") from exc
    return {item["name"]: item for item in jobs if isinstance(item, dict) and isinstance(item.get("name"), str)
            and item.get("kind") == "agent" and item.get("enabled") is True
            and isinstance(item.get("prompt"), str) and item["prompt"].strip()}


def _template(template_id: str) -> dict:
    try:
        items = json.loads(TEMPLATES.read_text())["templates"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AuthorizationError("authorization templates are not configured") from exc
    for item in items:
        if isinstance(item, dict) and item.get("id") == template_id:
            required = {"id", "job_name", "scope", "replay_origin", "rationale", "expires_in_minutes"}
            if set(item) == required:
                return item
    raise AuthorizationError("authorization template is not available")


def create_from_template(template_id: str) -> tuple[dict, bool]:
    template = _template(template_id)
    config = _config()
    state_value, catalog_value = config.get("state_path"), config.get("catalog_path")
    if not isinstance(state_value, str) or not state_value or not isinstance(catalog_value, str) or not catalog_value:
        raise AuthorizationError("authorization companion is not configured")
    state_path = Path(os.path.expandvars(state_value)).expanduser()
    catalog_path = Path(os.path.expandvars(catalog_value)).expanduser()
    state = read_state(state_path)
    expected_digest = state_digest(state) if state_path.exists() else None
    item, created = ensure_request(state, _catalog(catalog_path), template["job_name"], template["scope"],
                                   template["replay_origin"], template["rationale"],
                                   template["expires_in_minutes"], f"cron:{template['job_name']}")
    write_state(state_path, state, expected_digest=expected_digest)
    return view(state, item, True), created


def main() -> int:
    parser = argparse.ArgumentParser(description="Create one pending authorization from a shipped template")
    parser.add_argument("--template", required=True)
    args = parser.parse_args()
    try:
        item, created = create_from_template(args.template)
    except AuthorizationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"created": created, "request": item}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
