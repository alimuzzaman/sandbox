from __future__ import annotations

import json
import subprocess

from sandbox.core import die, info, ok
from sandbox.registry import register
import sandbox.core._hosting as hosting
import sandbox.core._remote as remote
import sandbox.core._cloudflare as cloudflare


def _emit(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data))
        return
    print(f"{data['project']} / {data['environment']}")
    for route in data.get("routes", []):
        suffix = f" -> {route['target']}" if route.get("target") else ""
        print(f"  {route['mode']}: {route['hostname']}{suffix}")


def _zone_for_hostname(client, hostname: str) -> dict:
    """Find the closest Cloudflare zone without assuming a public suffix list."""
    labels = hostname.removeprefix("*.").split(".")
    errors = []
    for offset in range(len(labels) - 1):
        candidate = ".".join(labels[offset:])
        try:
            return client.zone(candidate)
        except cloudflare.CloudflareError as exc:
            errors.append(str(exc))
    raise cloudflare.CloudflareError(errors[-1] if errors else f"no zone found for {hostname}")


def _cloudflare_drift(plan: dict) -> dict:
    """Read only the exact declared records; unrelated DNS is never queried for mutation."""
    if not cloudflare.cloudflare_token():
        return {"configured": False, "records": [], "ssl": None}
    try:
        client = cloudflare.Client()
        zones = {}
        records = []
        for wanted in plan["records"]:
            hostname = wanted["hostname"]
            zone = zones.get(hostname)
            if zone is None:
                zone = _zone_for_hostname(client, hostname)
                zones[hostname] = zone
            record_type = "AAAA" if ":" in wanted["address"] else "A"
            existing = [record for record in client.records(zone["id"], hostname)
                        if record.get("type") == record_type]
            records.append({
                "hostname": hostname,
                "type": record_type,
                "desired_address": wanted["address"],
                "exists": any(record.get("content") == wanted["address"] and
                              record.get("proxied") is True for record in existing),
            })
        ssl = {zone["name"]: client.current_ssl_mode(zone["id"])
               for zone in {entry["id"]: entry for entry in zones.values()}.values()}
        return {"configured": True, "records": records, "ssl": ssl}
    except cloudflare.CloudflareError as exc:
        # Planning remains useful when Cloudflare credentials expire; do not
        # turn this read-only diagnostic into a mutation prerequisite.
        return {"configured": True, "records": [], "ssl": None, "error": str(exc)}


def cmd_host(cfg, args) -> None:
    try:
        validated = hosting.validate_manifest(args.project_dir or ".", args.environment)
    except hosting.HostingError as exc:
        die(str(exc))
    if args.action == "validate":
        _emit({"ok": True, **validated}, args.json)
        return
    if not args.remote:
        die("--remote is required for host plan and host apply")
    entry = remote.get_remote(args.remote)
    if not entry:
        die(f"no remote named '{args.remote}'")
    plan = hosting.desired_plan(validated, entry.get("origin_ipv4"), entry.get("origin_ipv6"))
    state = hosting.load_host_state()
    plan["runtime"] = hosting.desired_runtime(validated, args.remote, state)
    plan["cloudflare"] = _cloudflare_drift(plan)
    if args.action == "plan":
        _emit({"ok": True, **plan}, args.json)
        return
    if not args.confirm:
        die("host apply is protected; review `./sb host plan` then pass --confirm")
    if not entry.get("provisioned"):
        die(f"remote '{args.remote}' is not provisioned")
    if not entry.get("origin_ipv4"):
        die(f"remote '{args.remote}' has no public origin address; run `./sb remote set-origin`")
    branch = remote.current_branch(validated["project_root"])
    policy = validated["deploy"]
    if branch not in policy["allowed_branches"] and "*" not in policy["allowed_branches"]:
        die(f"branch '{branch}' is not allowed for {validated['environment']}")
    if policy["require_clean"]:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=validated["project_root"],
                                capture_output=True, text=True, check=False)
        if status.stdout.strip():
            die(f"{validated['environment']} requires a clean working tree")
    # Runtime and Cloudflare writes intentionally stay behind both --confirm and the
    # explicit remote/origin prerequisites. The deploy command remains the existing
    # code-transfer surface until a user approves a live migration.
    die("host apply is ready to plan but live migration is intentionally disabled in this release")


register({"host": cmd_host})
