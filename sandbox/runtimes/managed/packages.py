"""Preview and confirm exact configured-source package transactions."""

from __future__ import annotations

from sandbox.runtimes.managed.models import PackageTransactionPlan


HOST_PACKAGES = ("systemd-container", "bubblewrap", "nftables", "debootstrap", "e2fsprogs")
IMAGE_COMMON = ("php8.3-fpm", "php8.3-cli", "mariadb-server", "cron", "ca-certificates")
EXPECTED_PREFIXES = {"php8.3-fpm": "8.3", "php8.3-cli": "8.3",
                     "mariadb-server": "1:10.11", "nginx": "1.24", "apache2": "2.4"}


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
