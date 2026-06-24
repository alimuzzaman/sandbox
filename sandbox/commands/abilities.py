from __future__ import annotations

from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register


def cmd_abilities(cfg, args) -> None:
    """Toggle the in-instance WP Abilities layer (spec 003).

    The layer is a provisioned mu-plugin that registers `sandbox/*` abilities on
    the WP 6.9+ Abilities API. Registration is gated on the per-instance option
    `sandbox_abilities_enabled` (default on). on/off flips that option; status
    reports the current state + endpoint + the dev/staging-only reminder.
    """
    inst = args.resolved_instance
    state = args.state
    if state not in ("on", "off", "status", "connect"):
        die("usage: ./sb abilities on|off|status|connect")

    if state == "connect":
        try:
            url = site_url(resolve_instances(cfg)[inst])
        except Exception:
            url = "(unknown)"
        endpoint = f"{url}/wp-json/sandbox/mcp"
        print(f"Sandbox MCP endpoint for '{inst}':\n  {endpoint}\n")
        print("Auth: HTTP Basic with an admin Application Password.")
        print(f"  user:     admin")
        print(f"  password: see `instances.{inst}.app_password` in sandbox.local.yml")
        print("            (gitignored — not printed here per the secrets rule)\n")
        print("Paste-ready MCP client config (fill the password from sandbox.local.yml):")
        print(f'''  {{
    "mcpServers": {{
      "sandbox-{inst}": {{
        "command": "npx",
        "args": ["-y", "mcp-remote", "{endpoint}",
                 "--header", "Authorization: Basic <base64(admin:APP_PASSWORD)>"]
      }}
    }}
  }}''')
        print("\nTip: `./sb abilities status` shows whether the layer is enabled.")
        return

    if state == "status":
        r = wpcli(["option", "get", "sandbox_abilities_enabled"],
                  instance=inst, check=False, capture=True)
        val = (getattr(r, "stdout", "") or "").strip()
        enabled = val in ("", "1")  # absent option => default on
        try:
            url = site_url(resolve_instances(cfg)[inst])
        except Exception:
            url = "(unknown)"
        print(f"abilities: {'on' if enabled else 'off'}  (instance: {inst})")
        print(f"endpoint:  {url}/wp-json/sandbox/mcp  "
              "(MCP server — HTTP Basic + admin Application Password)")
        print("note:      dev/staging only — never enable on a production site")
        return

    wpcli(["option", "update", "sandbox_abilities_enabled",
           "1" if state == "on" else "0"], instance=inst)
    ok(f"Abilities {state} for instance '{inst}'.")


register({'abilities': cmd_abilities})
