"""Preview and confirm exact configured-source package transactions."""

from __future__ import annotations

from sandbox.runtimes.managed.models import PackageTransactionPlan
import hashlib
import json
import os
import re
from pathlib import Path
import stat


HOST_PACKAGES = ("systemd-container", "bubblewrap", "nftables", "debootstrap", "e2fsprogs")
IMAGE_COMMON = (
    "php8.3-fpm", "php8.3-cli", "php8.3-mysql", "php8.3-curl", "php8.3-gd",
    "php8.3-mbstring", "php8.3-xml", "php8.3-zip", "php8.3-intl", "php8.3-opcache",
    "mariadb-server", "mariadb-client", "cron", "ca-certificates", "curl", "unzip",
    "git", "composer",
    "bubblewrap", "iproute2", "util-linux",
)
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
        incompatible.extend(name for name in image_names if name.startswith("php8.3-")
                            and not by_name[name].startswith("8.3"))
        if incompatible: raise ValueError(f"managed package versions unsupported: {incompatible}")
        return PackageTransactionPlan(
            "ubuntu-24.04-systemd-255", host, image, source_rows,
            ({"scope": "image", "policy_rc_d": "deny-service-start"},),
            ("/var/lib/sandbox/native", "/etc/sandbox/native"),
            ("policy-install", "image-create", "image-bootstrap"),
        )


class ManagedPackageService:
    def __init__(self, *, replanner, apply_transaction, baseline_observer,
                 confirmation, prepare_transaction=None):
        self.replanner = replanner
        self.apply_transaction = apply_transaction
        self.baseline_observer = baseline_observer
        self.confirmation = confirmation
        self.prepare_transaction = prepare_transaction

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
        prepared = {"ok": True, "mutated": False}
        if self.prepare_transaction is not None:
            prepared = self.prepare_transaction()
            if not isinstance(prepared, dict) or not prepared.get("ok"):
                return {"ok": False, "state": "failed",
                        "mutated": bool(isinstance(prepared, dict) and
                                        prepared.get("mutated")),
                        "reason": {"code": "native_helper_install_failed"}}
        before = self.baseline_observer()
        result = self.apply_transaction(current)
        after = self.baseline_observer()
        if before != after:
            return {"ok": False, "state": "blocked", "mutated": bool(result.get("mutated")),
                    "reason": {"code": "host_service_baseline_changed"}}
        return {**result, "mutated": bool(prepared.get("mutated") or result.get("mutated")),
                "host_service_baseline_digest": before.get("digest")
                if isinstance(before, dict) else None}


class PackagePlanStager:
    """Write one user-owned, digest-named plan for the fixed root helper."""

    def __init__(self, root="/var/lib/sandbox/native/staging"):
        self.root = Path(root)

    def stage(self, plan):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"install-{os.getuid()}-{plan.simulation_digest}.json"
        payload = (json.dumps(plan.to_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload); output.flush(); os.fsync(output.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path


class NativeHostPackageApplier:
    def __init__(self, *, process, repository_helper, installed_helper, stager=None):
        self.process = process
        self.repository_helper = repository_helper
        self.installed_helper = installed_helper
        self.stager = stager or PackagePlanStager()

    def prepare(self):
        installed = self.process.run(("sudo", self.repository_helper, "install"), timeout=120)
        if installed.returncode != 0:
            return {"ok": False, "state": "failed", "mutated": False,
                    "reason": {"code": "native_helper_install_failed"}}
        return {"ok": True, "state": "ready", "mutated": True,
                "reason": {"code": "ready"}}

    def apply(self, plan):
        path = self.stager.stage(plan)
        try:
            result = self.process.run(("sudo", "-n", self.installed_helper,
                                       "host-packages-apply", str(path),
                                       plan.simulation_digest), timeout=900)
        finally:
            path.unlink(missing_ok=True)
        detail = (getattr(result, "stderr", "") or "").strip()[:300]
        return {"ok": result.returncode == 0,
                "state": "ready" if result.returncode == 0 else "failed",
                # The helper installation already changed a root-owned path even
                # if the subsequent exact APT transaction fails.
                "mutated": True,
                "reason": {"code": "ready" if result.returncode == 0 else
                           "host_package_apply_failed",
                           **({"message": detail} if detail and result.returncode != 0 else {})},
                "simulation_digest": plan.simulation_digest}


class PrivilegedHostServiceBaseline:
    """Read the fixed, content-free host baseline through the installed helper."""

    def __init__(self, *, process, helper="/usr/local/libexec/sandbox-native-helper"):
        self.process = process
        self.helper = helper

    def observe(self):
        result = self.process.run(
            ("sudo", "-n", self.helper, "host-baseline-observe"), timeout=120,
        )
        output = result.stdout or ""
        if result.returncode != 0 or len(output.encode()) > 1024 * 1024:
            raise RuntimeError("native host service baseline is unavailable")
        try: value = json.loads(output)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("native host service baseline is invalid") from exc
        if (not isinstance(value, dict) or set(value) != {"ok", "digest", "baseline"}
                or value.get("ok") is not True
                or not isinstance(value.get("digest"), str)
                or len(value["digest"]) != 64
                or not isinstance(value.get("baseline"), dict)):
            raise RuntimeError("native host service baseline is invalid")
        return value


class HostServiceBaseline:
    """Digest foreign service state/config/data without exposing their contents."""

    UNITS = ("nginx.service", "apache2.service", "mariadb.service",
             "mysql.service", "php8.3-fpm.service")
    CONFIG_ROOTS = ("/etc/nginx", "/etc/apache2", "/etc/mysql", "/etc/php/8.3/fpm")
    DATA_ROOTS = ("/var/lib/mysql",)

    def __init__(self, *, process, config_roots=None, data_roots=None):
        self.process = process
        self.config_roots = tuple(Path(value) for value in (config_roots or self.CONFIG_ROOTS))
        self.data_roots = tuple(Path(value) for value in (data_roots or self.DATA_ROOTS))

    @staticmethod
    def _tree_digest(root, *, content):
        digest = hashlib.sha256()
        if not root.exists() and not root.is_symlink(): return "absent"
        paths = (root, *sorted(root.rglob("*"))) if root.is_dir() and not root.is_symlink() else (root,)
        for path in paths:
            details = path.lstat(); relative = str(path.relative_to(root.parent))
            digest.update(relative.encode()); digest.update(str(stat.S_IFMT(details.st_mode)).encode())
            digest.update(str(details.st_mode & 0o7777).encode())
            digest.update(str(details.st_size).encode()); digest.update(str(details.st_mtime_ns).encode())
            if path.is_symlink(): digest.update(os.readlink(path).encode())
            elif content and path.is_file():
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(65536), b""): digest.update(chunk)
        return digest.hexdigest()

    def observe(self):
        units = {}
        for unit in self.UNITS:
            result = self.process.run(("systemctl", "show", unit, "--no-pager",
                                       "--property=LoadState,ActiveState,UnitFileState,FragmentPath"),
                                      timeout=3)
            units[unit] = {line.split("=", 1)[0]: line.split("=", 1)[1]
                           for line in (result.stdout or "").splitlines() if "=" in line}
        return {"units": units,
                "config": {str(root): self._tree_digest(root, content=True)
                           for root in self.config_roots},
                "data": {str(root): self._tree_digest(root, content=True)
                         for root in self.data_roots}}
