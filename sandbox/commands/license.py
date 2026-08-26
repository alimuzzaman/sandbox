"""`./sb license` — manage Pro plugin license keys + sharing (spec 013).

Registry-wide (not instance-scoped): ONE key per family is stored once for all
instances in the gitignored secret store. License keys are SECRETS — this command
never echoes a key value (status shows presence + a masked hint only).

    sb license set wpdeveloper <key>   # one key for all WPDeveloper pro plugins
    sb license set elementor <key>     # the Elementor Pro key
    sb license status                  # which keys are set (masked) + EL primary
    sb license clear [family]          # remove one family's key (or all)
"""
import json

from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register


def cmd_license(cfg, args) -> None:
    action = getattr(args, "action", "status") or "status"
    json_output = bool(getattr(args, "json", False))

    if json_output and action != "status":
        die("--json is only supported with `license status`; no license mutation was run.")

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

    if action == "elementor-sync" or action == "sync":
        _elementor_sync(cfg, getattr(args, "from_instance", None))
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
    if json_output:
        print(json.dumps({
            "ok": True,
            "command": "license",
            "action": "status",
            "status": st,
        }, sort_keys=True))
        return
    ok("Pro license status (values masked — keys are secret):")
    info(f"  elementor   : {st['elementor']}")
    info("  wpdeveloper : keyless (pro plugins force-activated in-instance)")
    prim = st["elementor_primary"]
    if prim.get("instance") or prim.get("url"):
        info(f"  EL primary  : {prim.get('instance') or '?'} ({prim.get('url') or '?'})")
    else:
        info("  EL primary  : (none yet — set on first activation)")


def _el_opt(instance: str, args: list) -> str:
    """Read a wp option (or eval) from an instance via wp-cli; '' on any failure."""
    try:
        res = wpcli(args, instance, check=False, capture=True)
    except Exception:
        return ""
    return (getattr(res, "stdout", "") or "").strip()


def _elementor_sync(cfg, from_instance) -> None:
    """Capture a manually-connected instance's Elementor Pro activation and
    propagate it so every other instance rides the one seat. Connect Elementor Pro
    on ONE instance by hand first (it's OAuth/seat-limited)."""
    import json as _json
    instances = list(resolve_instances(cfg).keys())
    if not instances:
        die("no registered instances.")

    primary = from_instance
    if primary and primary not in instances:
        die(f"unknown instance '{primary}'. Known: {', '.join(sorted(instances))}.")
    if not primary:
        for name in instances:
            try:
                if not _instance_running(name):
                    continue   # don't hang on stopped instances
            except Exception:
                continue
            if _el_opt(name, ["option", "get", "elementor_pro_license_key"]):
                primary = name
                break
    if not primary:
        die("No instance has Elementor Pro connected. Activate + connect Elementor Pro "
            "on ONE instance (wp-admin → Elementor → License → Connect & Activate), "
            "then re-run `./sb license elementor-sync`.")

    key = _el_opt(primary, ["option", "get", "elementor_pro_license_key"])
    if not key:
        die(f"instance '{primary}' has no Elementor Pro license key — is it connected?")
    url = _el_opt(primary, ["eval", "echo home_url();"])
    raw = _el_opt(primary, ["option", "get", "_elementor_pro_license_v2_data", "--format=json"])
    try:
        data = _json.loads(raw) if raw else None
    except Exception:
        data = None

    capture_elementor(primary, url, key, data)
    for name in instances:
        _write_licensing_state(name)
    ok(f"Captured Elementor Pro activation from '{primary}' ({url}); propagated to "
       f"{len(instances)} instance(s). Secondaries now share the one seat — re-apply/boot "
       f"them if they were already running (value not shown).")


register({"license": cmd_license})
