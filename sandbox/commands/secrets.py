from __future__ import annotations

import json

from sandbox.core import die, ok
from sandbox.registry import register
from sandbox.core import _secrets
from sandbox.core import _cloudflare


def cmd_secrets(cfg, args) -> None:
    if args.action != "migrate-zshrc":
        die("unknown secrets action")
    try:
        moved = _secrets.migrate_zshrc()
        cloudflare_migrated = _cloudflare.migrate_legacy_token()
    except _secrets.SecretError as exc:
        die(str(exc))
    result = {"ok": True, "file": str(_secrets.secret_file()), "migrated": sorted(moved),
              "cloudflare_migrated": cloudflare_migrated}
    if args.json:
        print(json.dumps(result))
    elif moved:
        ok(f"migrated {len(moved)} secret exports to {result['file']}")
    else:
        ok(f"personal secret file already configured: {result['file']}")


register({"secrets": cmd_secrets})
