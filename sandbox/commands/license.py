"""`./sb license` — manage Pro plugin license keys + sharing (spec 013).

Registry-wide (not instance-scoped): ONE key per family is stored once for all
instances in the gitignored secret store. License keys are SECRETS — this command
never echoes a key value (status shows presence + a masked hint only).

    sb license set wpdeveloper <key>   # one key for all WPDeveloper pro plugins
    sb license set elementor <key>     # the Elementor Pro key
    sb license status                  # which keys are set (masked) + EL primary
    sb license clear [family]          # remove one family's key (or all)
"""
from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register


def cmd_license(cfg, args) -> None:
    action = getattr(args, "action", "status") or "status"

    if action == "set":
        family = getattr(args, "family", None)
        key = getattr(args, "key", None)
        try:
            set_license(family, key)
        except ValueError as e:
            die(str(e))
        ok(f"{family} license stored (value not shown). "
           f"Re-provision instances to apply: `./sb apply` / `ensure_instance`.")
        return

    if action == "clear":
        family = getattr(args, "family", None)
        try:
            cleared = clear_license(family)
        except ValueError as e:
            die(str(e))
        if cleared:
            ok(f"Cleared license(s): {', '.join(cleared)}. "
               f"Re-provision instances to revert to local-source/manual.")
        else:
            info("Nothing to clear (no matching key was set).")
        return

    # default: status — presence + masked hint only, never the raw key.
    st = license_status()
    ok("Pro license status (values masked — keys are secret):")
    info(f"  elementor   : {st['elementor']}")
    info("  wpdeveloper : keyless (pro plugins force-activated in-instance)")
    prim = st["elementor_primary"]
    if prim.get("instance") or prim.get("url"):
        info(f"  EL primary  : {prim.get('instance') or '?'} ({prim.get('url') or '?'})")
    else:
        info("  EL primary  : (none yet — set on first activation)")


register({"license": cmd_license})
