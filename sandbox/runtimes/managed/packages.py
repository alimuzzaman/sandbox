"""Preview and confirm exact configured-source package transactions."""

from __future__ import annotations

from sandbox.runtimes.managed.models import PackageTransactionPlan
import re
from pathlib import Path


HOST_PACKAGES = ("systemd-container", "bubblewrap", "nftables", "debootstrap", "e2fsprogs")
IMAGE_COMMON = ("php8.3-fpm", "php8.3-cli", "mariadb-server", "cron", "ca-certificates")
EXPECTED_PREFIXES = {"php8.3-fpm": "8.3", "php8.3-cli": "8.3",
                     "mariadb-server": "1:10.11", "nginx": "1.24", "apache2": "2.4"}
OFFICIAL_URIS = ("http://archive.ubuntu.com/ubuntu", "http://security.ubuntu.com/ubuntu",
                 "https://archive.ubuntu.com/ubuntu", "https://security.ubuntu.com/ubuntu")


class AptPackageSimulator:
    """Read exact candidates/closure from configured Ubuntu APT metadata."""
    INSTALL = re.compile(r"^Inst\s+(\S+)\s+(?:\[[^]]+\]\s+)?\((\S+)")

    def __init__(self, *, process, sources_path="/etc/apt/sources.list.d/ubuntu.sources"):
        self.process = process; self.sources_path = Path(sources_path)

    def sources(self):
        text = self.sources_path.read_text()
        rows = []
        for stanza in re.split(r"\n\s*\n", text):
            fields = {}
            for line in stanza.splitlines():
                if line.startswith("#") or ":" not in line: continue
                key, value = line.split(":", 1); fields[key.strip()] = value.strip()
            if "deb" not in fields.get("Types", "").split(): continue
            signed_by = fields.get("Signed-By")
            if not signed_by or not Path(signed_by).is_file():
                raise ValueError("Ubuntu APT source lacks an installed signing key")
            for uri in fields.get("URIs", "").split():
                if uri not in OFFICIAL_URIS:
                    continue
                rows.append({"uri": uri, "suite": fields.get("Suites", ""),
                             "components": fields.get("Components", ""),
                             "signed_by": signed_by, "signed": True, "kind": "archive"})
        if not rows: raise ValueError("configured signed Ubuntu Noble sources are unavailable")
        return tuple(rows)

    def _candidate_origin(self, package, version):
        result = self.process.run(("apt-cache", "policy", package), timeout=10)
        if result.returncode != 0: raise ValueError(f"APT policy unavailable for {package}")
        lines = (result.stdout or "").splitlines(); active = False
        for line in lines:
            stripped = line.strip()
            if version in stripped.split() and not any(token in OFFICIAL_URIS
                                                        for token in stripped.split()):
                active = True; continue
            if active:
                origin = next((token for token in stripped.split()
                               if token in OFFICIAL_URIS), None)
                if origin: return origin
            if active and stripped and not line.startswith(" "):
                break
        raise ValueError(f"package {package}={version} is not from configured Ubuntu archives")

    def simulate(self, scope, packages):
        state = ("-o", "Dir::State::status=/dev/null") if scope == "image" else ()
        result = self.process.run(("apt-get", "--simulate", *state,
                                   "--no-install-recommends", "install", *packages), timeout=30)
        if result.returncode != 0:
            raise ValueError("APT simulation failed")
        rows = []
        for line in (result.stdout or "").splitlines():
            match = self.INSTALL.match(line)
            if not match: continue
            name, version = match.groups(); origin = self._candidate_origin(name, version)
            rows.append({"name": name, "version": version, "action": "install",
                         "scope": scope, "origin": origin})
        present = {row["name"] for row in rows}
        for package in packages:
            if package in present: continue
            query = self.process.run(("dpkg-query", "-W", "-f=${Version}", package), timeout=5) \
                if scope == "host" else None
            if query is None or query.returncode != 0 or not (query.stdout or "").strip():
                policy = self.process.run(("apt-cache", "policy", package), timeout=10)
                candidate = next((line.split(":", 1)[1].strip()
                                  for line in (policy.stdout or "").splitlines()
                                  if line.strip().startswith("Candidate:")), "")
                if not candidate or candidate == "(none)": continue
                origin = self._candidate_origin(package, candidate)
                rows.append({"name": package, "version": candidate, "action": "install",
                             "scope": scope, "origin": origin})
            else:
                version = query.stdout.strip(); origin = self._candidate_origin(package, version)
                rows.append({"name": package, "version": version, "action": "keep",
                             "scope": scope, "origin": origin})
        return tuple(rows)


class ManagedPackagePlanner:
    def __init__(self, *, simulate, sources):
        self.simulate = simulate
        self.sources = sources

    def plan(self, *, web_server="nginx"):
        if web_server not in {"nginx", "apache"}: raise ValueError("managed web server is invalid")
        source_rows = tuple(dict(item) for item in self.sources())
        if not source_rows or any(not item.get("signed") or not item.get("uri")
                                  for item in source_rows):
            raise ValueError("managed packages require configured signed APT sources")
        if any(item.get("kind") in {"ppa", "remote_script", "source_build"}
               for item in source_rows):
            raise ValueError("untrusted package source kind is forbidden")
        image_names = (*IMAGE_COMMON, "nginx" if web_server == "nginx" else "apache2")
        host = tuple(dict(item) for item in self.simulate("host", HOST_PACKAGES))
        image = tuple(dict(item) for item in self.simulate("image", image_names))
        by_name = {item.get("name"): str(item.get("version", "")) for item in image}
        missing = [name for name in image_names if not by_name.get(name)]
        if missing: raise ValueError(f"managed package versions unavailable: {missing}")
        incompatible = [name for name, prefix in EXPECTED_PREFIXES.items()
                        if name in image_names and not by_name[name].startswith(prefix)]
        if incompatible: raise ValueError(f"managed package versions unsupported: {incompatible}")
        return PackageTransactionPlan(
            "ubuntu-24.04-systemd-255", host, image, source_rows,
            ({"scope": "image", "policy_rc_d": "deny-service-start"},),
            ("/var/lib/sandbox/native", "/etc/sandbox/native"),
            ("policy-install", "image-create", "image-bootstrap"),
        )


class ManagedPackageService:
    def __init__(self, *, replanner, apply_transaction, baseline_observer,
                 confirmation):
        self.replanner = replanner
        self.apply_transaction = apply_transaction
        self.baseline_observer = baseline_observer
        self.confirmation = confirmation

    def apply(self, plan, *, interactive=False):
        if not interactive:
            return {"ok": False, "state": "pending_confirmation", "mutated": False,
                    "reason": {"code": "pending_install_confirmation"},
                    "simulation_digest": plan.simulation_digest}
        if not self.confirmation(plan):
            return {"ok": False, "state": "declined", "mutated": False,
                    "reason": {"code": "install_declined"}}
        current = self.replanner()
        if current.simulation_digest != plan.simulation_digest:
            return {"ok": False, "state": "drifted", "mutated": False,
                    "reason": {"code": "package_plan_drift"},
                    "simulation_digest": current.simulation_digest}
        before = self.baseline_observer()
        result = self.apply_transaction(current)
        after = self.baseline_observer()
        if before != after:
            return {"ok": False, "state": "blocked", "mutated": bool(result.get("mutated")),
                    "reason": {"code": "host_service_baseline_changed"}}
        return result
