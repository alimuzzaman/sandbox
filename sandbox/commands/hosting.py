from __future__ import annotations

import json
import subprocess
import base64
import shlex
import time
import urllib.request
from getpass import getpass

from sandbox.core import die, info, ok
from sandbox.registry import register
import sandbox.core._hosting as hosting
import sandbox.core._remote as remote
import sandbox.core._cloudflare as cloudflare
import sandbox.core._secrets as personal_secrets


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


def _secret_status(validated: dict) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    missing: list[str] = []
    mappings = {**validated["secrets"]["required"], **validated["secrets"]["generated"]}
    for source_key in sorted(set(mappings.values())):
        value = personal_secrets.resolve_secret(source_key)
        if value:
            values[source_key] = value
        else:
            missing.append(source_key)
    return values, missing


def _cmd_host_secrets(validated: dict, args) -> None:
    values, missing = _secret_status(validated)
    generated = set(validated["secrets"]["generated"].values())
    set_key = getattr(args, "set_secret", None)
    if set_key:
        declared = set(validated["secrets"]["required"].values()) | generated
        if set_key not in declared:
            die(f"'{set_key}' is not declared by this hosting environment")
        value = getpass(f"Value for {set_key}: ")
        if not value:
            die("secret value cannot be empty")
        personal_secrets.write_secret(set_key, value)
        values[set_key] = value
        missing = [key for key in missing if key != set_key]
    if getattr(args, "generate_secrets", False):
        for key in sorted(generated & set(missing)):
            personal_secrets.write_secret(key, personal_secrets.generate_secret())
            values[key] = "[generated]"
        missing = [key for key in missing if key not in generated]
    result = {
        "ok": not missing,
        "project": validated["project"],
        "environment": validated["environment"],
        "required": sorted(set(validated["secrets"]["required"].values()) | generated),
        "present": sorted(values),
        "missing": missing,
    }
    if args.json:
        print(json.dumps(result))
    else:
        print(f"{result['project']} / {result['environment']}")
        print("  present: " + (", ".join(result["present"]) or "none"))
        print("  missing: " + (", ".join(result["missing"]) or "none"))


def _remote_checked(entry: dict, command: str, timeout: int = 180) -> str:
    result = remote.ssh_run(entry, command, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "remote command failed").strip()[:2000])
    return result.stdout or ""


def _write_remote_text(entry: dict, path: str, text: str, mode: str = "0600") -> None:
    payload = base64.b64encode(text.encode()).decode()
    parent = str(__import__("posixpath").dirname(path))
    command = (
        f"mkdir -p {shlex.quote(parent)} && "
        f"printf %s {shlex.quote(payload)} | base64 -d > {shlex.quote(path)} && "
        f"chmod {mode} {shlex.quote(path)}"
    )
    _remote_checked(entry, command)


def _compose_prefix(validated: dict, source_dir: str, override_path: str, env_path: str) -> str:
    parts = [f"SANDBOX_HOST_ENV_FILE={env_path}", "docker", "compose", "--env-file", env_path,
             "-p", hosting.compose_project_name(validated)]
    for file_name in validated["compose"]["files"]:
        parts.extend(["-f", str(__import__("pathlib").Path(source_dir) / file_name)])
    parts.extend(["-f", override_path])
    return " ".join(shlex.quote(part) for part in parts)


def _configure_host_caddy(entry: dict, name: str, content: str, previous: str | None = None) -> None:
    path = f"/etc/caddy/conf.d/{name}.caddy"
    temporary = f"/tmp/{name}.caddy"
    _write_remote_text(entry, temporary, content, "0644")
    command = (
        "set -e; if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; "
        "$SUDO install -d -m 0755 /etc/caddy/conf.d; "
        "if [ ! -f /etc/caddy/Caddyfile ]; then "
        "printf '%s\\n' 'import /etc/caddy/conf.d/*.caddy' | $SUDO tee /etc/caddy/Caddyfile >/dev/null; "
        "elif ! $SUDO grep -q 'import /etc/caddy/conf.d/\\*.caddy' /etc/caddy/Caddyfile; then "
        "printf '\\n%s\\n' 'import /etc/caddy/conf.d/*.caddy' | $SUDO tee -a /etc/caddy/Caddyfile >/dev/null; fi; "
        f"$SUDO install -m 0644 {shlex.quote(temporary)} {shlex.quote(path)}; "
        "$SUDO caddy validate --config /etc/caddy/Caddyfile; $SUDO systemctl reload caddy"
    )
    _remote_checked(entry, command)


def _restore_host_caddy(entry: dict, name: str, previous: str | None) -> None:
    path = f"/etc/caddy/conf.d/{name}.caddy"
    if previous is None:
        command = (
            "set -e; if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; "
            f"$SUDO rm -f {shlex.quote(path)}; $SUDO caddy validate --config /etc/caddy/Caddyfile; "
            "$SUDO systemctl reload caddy"
        )
        _remote_checked(entry, command)
        return
    _configure_host_caddy(entry, name, previous)


def _read_remote_optional(entry: dict, path: str) -> str | None:
    result = remote.ssh_run(entry, f"test -f {shlex.quote(path)} && cat {shlex.quote(path)}", timeout=30)
    return result.stdout if result.returncode == 0 else None


def _origin_certificate(entry: dict, validated: dict, runtime: dict, state_entry: dict,
                        client: cloudflare.Client, home: str) -> tuple[str, str, dict]:
    base = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}/certs"
    cert_path, key_path, csr_path = f"{base}/origin.pem", f"{base}/origin.key", f"{base}/origin.csr"
    certificate = state_entry.get("certificate") if isinstance(state_entry, dict) else None
    present = remote.ssh_run(entry, f"test -s {shlex.quote(cert_path)} -a -s {shlex.quote(key_path)}", timeout=30)
    if certificate and present.returncode == 0:
        return cert_path, key_path, certificate
    primary = next(route["hostname"] for route in validated["routes"] if route.get("primary"))
    command = (
        f"mkdir -p {shlex.quote(base)}; chmod 0700 {shlex.quote(base)}; "
        f"if [ ! -s {shlex.quote(key_path)} ]; then openssl ecparam -name prime256v1 -genkey -noout -out {shlex.quote(key_path)}; chmod 0600 {shlex.quote(key_path)}; fi; "
        f"openssl req -new -key {shlex.quote(key_path)} -subj {shlex.quote('/CN=' + primary)} -out {shlex.quote(csr_path)}; "
        f"cat {shlex.quote(csr_path)}"
    )
    csr = _remote_checked(entry, command, timeout=60)
    issued = client.create_origin_certificate(csr, runtime["certificate_hostnames"])
    certificate_text = issued.get("certificate")
    if not isinstance(certificate_text, str) or not certificate_text.strip():
        raise cloudflare.CloudflareError("Cloudflare did not return an Origin CA certificate")
    _write_remote_text(entry, cert_path, certificate_text, "0644")
    return cert_path, key_path, {"id": issued.get("id"), "hostnames": runtime["certificate_hostnames"]}


def _run_compose(entry: dict, validated: dict, source_dir: str, runtime_dir: str,
                 runtime: dict) -> None:
    override = f"{runtime_dir}/compose.override.yml"
    env_file = f"{runtime_dir}/environment.env"
    _write_remote_text(entry, override, runtime["compose_override"], "0600")
    _write_remote_text(entry, env_file, runtime["environment"], "0600")
    prefix = _compose_prefix(validated, source_dir, override, env_file)
    service = shlex.quote(validated["compose"]["service"])
    _remote_checked(entry, f"{prefix} up -d --build --remove-orphans {service}", timeout=900)
    for init_service in validated["compose"].get("init_services", []):
        _remote_checked(entry, f"{prefix} --profile jobs run --rm {shlex.quote(init_service)}", timeout=900)
    _remote_checked(entry, f"{prefix} up -d {service}", timeout=300)


def _verify_remote_health(entry: dict, runtime: dict) -> None:
    port = runtime["loopback_port"]
    path = runtime["healthcheck"]["path"]
    minimum, maximum = min(runtime["healthcheck"]["statuses"]), max(runtime["healthcheck"]["statuses"])
    output = _remote_checked(entry, f"curl -fsS --max-time 15 -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{port}{shlex.quote(path)}", timeout=30)
    try:
        code = int(output.strip())
    except ValueError as exc:
        raise RuntimeError("remote healthcheck returned a non-status response") from exc
    if not minimum <= code <= maximum:
        raise RuntimeError(f"remote healthcheck returned {code}, expected {minimum}-{maximum}")


def _verify_edge(routes: list[dict]) -> None:
    for route in routes:
        if route["hostname"].startswith("*."):
            continue
        request = urllib.request.Request(f"https://{route['hostname']}/", method="GET")
        last_error = None
        for _ in range(5):
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    if 200 <= response.status < 400:
                        last_error = None
                        break
            except Exception as exc:  # Edge propagation is external and transient.
                last_error = exc
                time.sleep(2)
        if last_error:
            raise RuntimeError(f"edge verification failed for {route['hostname']}: {last_error}")


def _apply_host(validated: dict, entry: dict, remote_name: str, runtime: dict,
                state: dict, allow_zone_ssl_change: bool) -> None:
    secret_values, missing = _secret_status(validated)
    if missing:
        raise hosting.HostingError("missing hosting secrets: " + ", ".join(missing))
    runtime["environment"] = hosting.render_env_file(validated, secret_values)
    client = cloudflare.Client()
    home = remote.resolve_sandbox_home(entry)
    target = remote.ensure_deploy_repo(entry, validated["project_root"])
    branch = remote.current_branch(validated["project_root"])
    sha = remote.push_commits(entry, validated["project_root"], target, branch)
    remote.reset_target_to(entry, target, sha)
    diff, untracked = remote.capture_uncommitted(validated["project_root"])
    remote.apply_uncommitted(entry, target, validated["project_root"], diff, untracked)
    runtime_dir = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}"
    key = runtime["key"]
    previous_entry = dict(state["hosts"].get(key) or {})
    caddy_name = f"sandbox-host-{validated['project']}-{validated['environment']}"
    caddy_path = f"/etc/caddy/conf.d/{caddy_name}.caddy"
    previous_caddy = _read_remote_optional(entry, caddy_path)
    changes: list[dict] = []
    ssl_previous: dict[str, str | None] = {}

    def rollback() -> None:
        for change in reversed(changes):
            client.restore_record(change["zone_id"], change["previous"], change["created_id"])
        for zone_id, mode in ssl_previous.items():
            if mode and mode != "strict":
                client.ssl_mode(zone_id, mode)
        _restore_host_caddy(entry, caddy_name, previous_caddy)

    def apply() -> None:
        _run_compose(entry, validated, target, runtime_dir, runtime)
        _verify_remote_health(entry, runtime)
        cert_path, key_path, certificate = _origin_certificate(entry, validated, runtime, previous_entry, client, home)
        runtime["caddyfile"] = hosting.caddyfile(validated, runtime["loopback_port"], cert_path, key_path)
        _configure_host_caddy(entry, caddy_name, runtime["caddyfile"])
        zones: dict[str, dict] = {}
        for wanted in runtime["records"]:
            hostname = wanted["hostname"]
            zone = zones.get(hostname)
            if zone is None:
                zone = _zone_for_hostname(client, hostname)
                zones[hostname] = zone
            current = client.current_ssl_mode(zone["id"])
            if current != "strict":
                if not allow_zone_ssl_change:
                    raise RuntimeError(f"zone {zone['name']} is {current or 'unset'}; pass --allow-zone-ssl-change")
                ssl_previous[zone["id"]] = current
                client.ssl_mode(zone["id"], "strict")
            kind = "AAAA" if ":" in wanted["address"] else "A"
            previous = next((record for record in client.records(zone["id"], hostname) if record.get("type") == kind), None)
            created = client.upsert_address(zone["id"], hostname, wanted["address"], proxied=True)
            changes.append({"zone_id": zone["id"], "previous": previous, "created_id": created.get("id")})
        _verify_edge(validated["routes"])
        state["hosts"][key] = {"loopback_port": runtime["loopback_port"], "compose_project": runtime["compose_project"],
                               "certificate": certificate, "records": changes, "commit": sha,
                               "caddy_name": caddy_name}
        hosting.save_host_state(state)

    hosting.apply_with_rollback(apply, rollback)


def cmd_host(cfg, args) -> None:
    try:
        validated = hosting.validate_manifest(args.project_dir or ".", args.environment)
    except hosting.HostingError as exc:
        die(str(exc))
    if args.action == "secrets":
        _cmd_host_secrets(validated, args)
        return
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
    plan["runtime"]["records"] = plan["records"]
    _, missing = _secret_status(validated)
    plan["secrets"] = {"missing": missing, "required": sorted(set(validated["secrets"]["required"].values()) | set(validated["secrets"]["generated"].values()))}
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
    ssl = plan["cloudflare"].get("ssl") if isinstance(plan.get("cloudflare"), dict) else None
    if ssl and not getattr(args, "allow_zone_ssl_change", False):
        non_strict = [zone for zone, mode in ssl.items() if mode != "strict"]
        if non_strict:
            die("these zones require --allow-zone-ssl-change: " + ", ".join(non_strict))
    try:
        _apply_host(validated, entry, args.remote, plan["runtime"], state,
                    bool(getattr(args, "allow_zone_ssl_change", False)))
    except (hosting.HostingError, cloudflare.CloudflareError, RuntimeError,
            subprocess.SubprocessError, OSError) as exc:
        die(str(exc))
    ok(f"applied {validated['project']} / {validated['environment']} to {args.remote}")


register({"host": cmd_host})
