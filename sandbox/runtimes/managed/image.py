"""Plan bounded ext4 images and delegate fixed lifecycle verbs to the root helper."""

from __future__ import annotations

from pathlib import Path


class ManagedImage:
    def __init__(self, *, process, helper, native_root="/var/lib/sandbox/native",
                 observer=None):
        self.process = process; self.helper = helper
        self.native_root = Path(native_root)
        self.observer = observer

    def plan(self, policy):
        expected = self.native_root / "instances" / policy.machine_id / "root.img"
        spec = dict(policy.root_image)
        if Path(spec.get("path", "")) != expected:
            raise ValueError("managed image path is outside its fixed owned root")
        for key, low, high in (("bytes", 1024**3, 1024**4),
                               ("inodes", 10000, 10000000)):
            value = spec.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"managed image {key} is invalid")
        return {"machine_id": policy.machine_id, "policy_digest": policy.digest,
                "path": str(expected), "bytes": spec["bytes"], "inodes": spec["inodes"],
                "mount_options": ("loop", "nodev", "nosuid", "noatime")}

    def _run(self, verb, plan):
        return self.process.run(("sudo", "-n", self.helper, verb, plan["machine_id"],
                                 plan["policy_digest"]), timeout=120)

    def create(self, plan): return self._run("image-create", plan)
    def mount(self, plan): return self._run("image-mount", plan)

    def unmount(self, plan):
        if self.observer is not None:
            observed = dict(self.observer(plan["machine_id"]))
            if observed.get("policy_digest") != plan["policy_digest"]:
                return {"ok": False, "mutated": False, "reason": "image policy drifted"}
        result = self._run("image-unmount", plan)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0}

    def remove(self, plan):
        if self.observer is not None:
            observed = dict(self.observer(plan["machine_id"]))
            if observed.get("mounted"):
                return {"ok": False, "mutated": False, "reason": "image remains mounted"}
            if observed.get("policy_digest") != plan["policy_digest"]:
                return {"ok": False, "mutated": False, "reason": "image policy drifted"}
        result = self._run("image-remove", plan)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0}


class ManagedRootfs:
    """Bootstrap exact image packages through the fixed helper."""

    def __init__(self, *, process, helper, stager):
        self.process = process; self.helper = helper; self.stager = stager

    def configure(self, plan):
        package_plan = plan["package_plan"]
        path = self.stager.stage(package_plan)
        try:
            result = self.process.run(("sudo", "-n", self.helper, "image-bootstrap",
                                       plan["machine_id"], plan["policy_digest"], str(path),
                                       package_plan.simulation_digest, plan["web_server"]),
                                      timeout=1900)
            if result.returncode == 0:
                services = plan["services"]
                result = self.process.run(("sudo", "-n", self.helper, "image-configure",
                                           plan["machine_id"], plan["policy_digest"],
                                           plan["web_server"], services["digest"]), timeout=300)
        finally:
            path.unlink(missing_ok=True)
        if result.returncode != 0: raise RuntimeError("managed Noble rootfs configuration failed")
        return {"ok": True, "mutated": True}
