"""Deterministic, secret-free composition for one managed WordPress guest."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from collections.abc import Mapping
from pathlib import Path

from sandbox.isolation.models import (
    EGRESS_GRANT_AUTHORITY, EgressGrant, EgressGrantSet, ManagedIsolationPolicy,
    canonical_digest,
)
from sandbox.runtimes.managed.models import (
    ManagedPhpExtensionPlan,
    PackageTransactionPlan,
    PhpExtensionPackage,
)


def _extension_mapping(requirements):
    """Extract only normalized requirement data from a config seam.

    Runtime adapters intentionally accept a plain mapping/interface instead
    of importing the config provider's model.  The extraction is strict: only
    ``profile`` and ``extensions`` are consumed, so package names, URLs,
    source declarations, and build instructions can never cross this seam.
    """
    from sandbox.php_extensions.catalog import normalize_requirements

    if hasattr(requirements, "to_dict") and callable(requirements.to_dict):
        requirements = requirements.to_dict()
    if hasattr(requirements, "requirements") and not isinstance(requirements, Mapping):
        values = getattr(requirements, "requirements")
        requirements = {
            "profile": getattr(requirements, "profile", None),
            "extensions": {
                getattr(value, "name"): {
                    "state": getattr(value, "state", "enabled"),
                    **({"version": getattr(value, "version")}
                       if getattr(value, "version", None) is not None else {}),
                }
                for value in values
            },
        }
    if not isinstance(requirements, Mapping):
        raise ValueError("managed PHP extension requirements are invalid")
    unknown = set(requirements) - {"profile", "extensions", "required", "capabilities"}
    if unknown:
        raise ValueError(
            "managed PHP extension requirements contain unknown keys: "
            + ", ".join(sorted(map(str, unknown)))
        )
    profile = requirements.get("profile")
    raw = requirements.get("extensions", {})
    if not isinstance(raw, Mapping):
        raise ValueError("managed PHP extension requirements.extensions is invalid")
    # ``catalog.normalize_requirements`` is the canonical validation boundary;
    # it accepts booleans, version strings, and {state,version} objects while
    # refusing arbitrary package metadata.
    canonical_raw = {
        name: ({"state": "enabled", "version": value}
               if isinstance(value, str) else value)
        for name, value in raw.items()
    }
    normalized = list(normalize_requirements({"extensions": canonical_raw}))
    if profile is not None and not isinstance(profile, str):
        raise ValueError("managed PHP extension profile is invalid")
    return profile, normalized


class ManagedExtensionPackagePlanner:
    """Decorate the signed-APT package plan with catalog-bound extensions.

    ``base_planner`` is the existing :class:`ManagedPackagePlanner`; this
    adapter never adds a repository, invokes PECL, compiles source, or accepts
    an arbitrary package.  It asks the same configured APT simulator for every
    newly required package, then records the extension requirement and catalog
    provenance in the ordinary image package row.  The existing fixed helper
    therefore sees its unchanged top-level plan schema and its digest changes
    whenever extension intent, resolved version, or catalog revision changes.
    """

    def __init__(self, base_planner, *, php_version="8.3", catalog=None):
        self.base_planner = base_planner
        self.php_version = php_version
        if catalog is None:
            from sandbox.php_extensions.catalog import DEFAULT_CATALOG
            catalog = DEFAULT_CATALOG
        self.catalog = catalog
        self.last_extension_plan = None

    def _resolve(self, requirements):
        profile, normalized = _extension_mapping(requirements)
        if profile is not None:
            try:
                profile_requirements = self.catalog.profile_requirements(profile)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            by_name = {item["name"]: item for item in normalized}
            for item in profile_requirements:
                by_name.setdefault(item["name"], item)
            # A bare ``wordpress@1`` scaffold does not select an image
            # capability.  Keep that omission usable across adapters by
            # choosing the deterministic, allow-listed GD capability.  This
            # is intentionally only a missing-name fallback: an explicit
            # ``gd``/``imagick`` request (including a disabled one) remains
            # authoritative and is validated below rather than silently
            # overridden.
            if "gd" not in by_name and "imagick" not in by_name:
                by_name["gd"] = {"name": "gd", "state": "enabled", "version": None}
            normalized = [by_name[name] for name in sorted(by_name)]
        validation = self.catalog.validate(
            {"extensions": normalized}, profile=profile,
        )
        if not validation.ok:
            issue = validation.issues[0]
            raise ValueError(issue.message)
        # The catalog is also used by read-only probes, so its validation API
        # intentionally does not promise that every recipe is installable.
        # Managed-native has a narrower contract: every selected capability
        # must resolve to the reviewed official APT package template.  Keep
        # that provisioning gate here, before any host/network mutation.
        for item in normalized:
            recipe = self.catalog.recipe(item["name"])
            if (getattr(recipe, "provisionable", True) is False
                    or not recipe.package_template):
                raise ValueError(
                    f"managed PHP extension {item['name']} is not provisionable"
                )
        return profile, normalized

    def _simulated_package(self, package, sources):
        rows = []
        simulate = getattr(self.base_planner, "simulate", None)
        if not callable(simulate):
            raise ValueError(
                "managed PHP extension planning requires the configured signed-APT simulator"
            )
        try:
            rows = [dict(row) for row in simulate("image", (package,))]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"APT simulation failed for managed PHP extension package {package}") from exc
        candidates = [row for row in rows if row.get("name") == package]
        if not candidates:
            raise ValueError(f"managed PHP extension package {package} is unavailable")
        row = candidates[0]
        if row.get("scope") != "image" or not isinstance(row.get("version"), str) \
                or not row.get("version"):
            raise ValueError(f"managed PHP extension package {package} has no exact version")
        if not row.get("origin"):
            # Test doubles may omit origin, but the real simulator always
            # supplies it after checking official signed sources.  Preserve a
            # source identity in the row so provenance remains explicit.
            official = next((item.get("uri") for item in sources
                             if isinstance(item, Mapping) and item.get("uri")), None)
            if official is None:
                raise ValueError(f"managed PHP extension package {package} has no source provenance")
            row["origin"] = official
        return row

    def _check_version(self, requirement, row, package):
        """Reject an exact or wildcard extension version mismatch early.

        The managed image has no fallback repository or alternate artifact
        path.  A package candidate that cannot satisfy the declared version is
        therefore a planning error, before the ordinary TTY approval gate.
        ``php`` is the explicit shorthand for the active PHP minor used in the
        allowlisted package name.
        """
        constraint = requirement.get("version")
        if constraint is None:
            return
        observed = str(row.get("version", ""))
        if constraint == "php":
            if not package.startswith(f"php{self.php_version}-"):
                raise ValueError(
                    f"managed PHP extension {requirement['name']} is not mapped to PHP {self.php_version}"
                )
            return
        if constraint.endswith(".*"):
            if not observed.startswith(constraint[:-1]):
                raise ValueError(
                    f"managed PHP extension {requirement['name']} package version does not satisfy {constraint}"
                )
            return
        if observed != constraint:
            raise ValueError(
                f"managed PHP extension {requirement['name']} package version {observed} "
                f"does not satisfy {constraint}"
            )

    def resolve(self, *, web_server="nginx", requirements):
        base = self.base_planner.plan(web_server=web_server)
        if not isinstance(base, PackageTransactionPlan):
            raise ValueError("managed package planner returned an invalid transaction plan")
        profile, normalized = self._resolve(requirements)
        sources = tuple(dict(item) for item in base.sources)
        rows = [dict(item) for item in base.image_packages]
        by_package = {item.get("name"): item for item in rows}
        evidence = []
        added = []
        for requirement in normalized:
            name = requirement["name"]
            recipe = self.catalog.recipe(name)
            if recipe.package_template is None:
                # Core extensions without a discrete package are still part of
                # the assertion, but the base image's PHP package remains the
                # only apt artifact.  They are recorded on that package below.
                package = None
            else:
                package = recipe.package_template.format(php_minor=self.php_version)
            if package is None:
                package = f"php{self.php_version}-common"
            row = by_package.get(package)
            if row is None:
                row = self._simulated_package(package, sources)
                by_package[package] = row
                added.append(row)
            self._check_version(requirement, row, package)
            details = {
                "name": name,
                "state": requirement["state"],
                "version": requirement.get("version"),
                "package": package,
                "package_version": row["version"],
                "catalog_digest": self.catalog.digest,
                "source": "official-distribution",
            }
            evidence.append(PhpExtensionPackage(
                extension=name, package=package,
                package_version=row["version"], state=requirement["state"],
                version_constraint=requirement.get("version"),
                catalog_digest=self.catalog.digest,
            ))
            existing = list(row.get("php_extensions", ()))
            existing.append(details)
            row["php_extensions"] = sorted(existing, key=lambda value: value["name"])
            row["extension_catalog"] = self.catalog.digest
            row["extension_provenance"] = "official-distribution"
        rows = [item for item in rows if item.get("name") not in {None}]
        # Newly simulated rows are added once, in deterministic package order.
        existing_names = {item.get("name") for item in rows}
        rows.extend(item for item in sorted(added, key=lambda value: value["name"])
                    if item.get("name") not in existing_names)
        extension_plan = ManagedPhpExtensionPlan(
            php_version=self.php_version, profile=profile,
            requirements=tuple(normalized), packages=tuple(evidence),
            catalog_digest=self.catalog.digest,
        )
        package_plan = PackageTransactionPlan(
            base.matrix_id, base.host_packages, tuple(rows), base.sources,
            base.service_effects, base.owned_roots, base.privilege_actions,
            confirmation=base.confirmation,
        )
        # Keep the fixed helper's package allowlist as the final control-plane
        # seam before this plan can reach a TTY confirmation/stager.
        from sandbox.runtimes.managed.helper import validate_extension_package_allowlist
        validate_extension_package_allowlist(package_plan, catalog=self.catalog)
        self.last_extension_plan = extension_plan
        return package_plan, extension_plan

    def plan(self, *, web_server="nginx", requirements=None, php_extensions=None):
        requirements = php_extensions if php_extensions is not None else requirements
        if requirements is None:
            self.last_extension_plan = None
            return self.base_planner.plan(web_server=web_server)
        return self.resolve(web_server=web_server, requirements=requirements)[0]


class ManagedPolicyStore:
    """Stage and install only the canonical policy consumed by the fixed helper."""

    def __init__(self, *, process, helper, staging_root="/var/lib/sandbox/native/staging"):
        self.process = process
        self.helper = helper
        self.staging_root = Path(staging_root)

    def _stage(self, policy):
        path = self.staging_root / f"{policy.machine_id}.json"
        self.staging_root.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(policy.to_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    def install(self, policy):
        if not isinstance(policy, ManagedIsolationPolicy):
            raise ValueError("managed policy is invalid")
        path = self._stage(policy)
        try:
            result = self.process.run(("sudo", "-n", self.helper, "policy-install",
                                       policy.machine_id, str(path)), timeout=120)
        finally:
            path.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError("managed policy installation failed")
        return {"ok": True, "mutated": True}

    def status(self, plan):
        result = self.process.run(("sudo", "-n", self.helper, "policy-status",
                                   plan["machine_id"]), timeout=30)
        return {"ok": result.returncode == 0, "mutated": False,
                "stdout": result.stdout or ""}

    def remove(self, plan):
        result = self.process.run(("sudo", "-n", self.helper, "policy-remove",
                                   plan["machine_id"], plan["policy_digest"]), timeout=120)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0}


class ManagedPlanBuilder:
    """Build one canonical plan without reading secrets or mutating the host.

    Network reservation is the sole local mutation and is locked by the native
    repository.  The private UID base is derived from that unique /30 slot, so
    concurrent instances cannot receive overlapping 65536-ID ranges.
    """

    def __init__(self, *, repository, packages, resources, network, image,
                 apparmor, machine, database, services, web_server="nginx",
                 descriptor_resolver=None, paths=None, extension_planner=None):
        self.repository = repository
        self.packages = packages
        self.resources = resources
        self.network = network
        self.image = image
        self.apparmor = apparmor
        self.machine = machine
        self.database = database
        self.services = services
        self.web_server = web_server
        self.descriptor_resolver = descriptor_resolver
        self.paths = paths
        self.extension_planner = extension_planner

    def _identity(self, request):
        root = Path(request.project_root).expanduser().resolve(strict=True)
        if self.paths is not None:
            root = Path(self.paths.require_allowed(root))
        if not root.is_dir():
            raise ValueError("managed project root is unavailable")
        owner = {"project_root": str(root), "label": request.label}
        machine_id = "sb-" + hashlib.sha256(
            f"{owner['project_root']}\0{owner['label']}".encode()).hexdigest()[:16]
        return root, owner, machine_id

    @staticmethod
    def _uid_base(allocation):
        pool = ipaddress.ip_network("10.203.0.0/16")
        subnet = ipaddress.ip_network(allocation["subnet"])
        if not subnet.subnet_of(pool) or subnet.prefixlen != 30:
            raise ValueError("managed network reservation is invalid")
        slot = (int(subnet.network_address) - int(pool.network_address)) // 4
        return (1000 + slot) * 65536

    def __call__(self, request):
        root, owner, machine_id = self._identity(request)
        descriptor = self.descriptor_resolver(str(root), label=request.label) \
            if self.descriptor_resolver is not None else {}
        runtime = descriptor.get("wordpressRuntime", {}) if isinstance(descriptor, dict) else {}
        php_extensions = request.arguments.get(
            "phpExtensions",
            request.arguments.get("php_extensions", (
                descriptor.get("phpExtensions") if isinstance(descriptor, dict) else None
            )),
        )
        # ``None`` means the field was omitted and retains exact legacy
        # behavior.  An explicitly empty mapping is still a declaration and is
        # therefore represented by a digest-bound no-op extension plan.
        if php_extensions is None and isinstance(runtime, dict):
            php_extensions = runtime.get("phpExtensions")
        values = request.arguments.get("resources", runtime.get("resources", {}))
        resources = self.resources.compile(values)
        requested_egress = (request.arguments["egress"] if "egress" in request.arguments
                            else runtime.get("egress", ()))
        if not isinstance(requested_egress, (tuple, list)):
            raise ValueError("managed egress grant set is invalid")
        grants = []
        for raw in requested_egress:
            if not isinstance(raw, dict): raise ValueError("managed egress grant is invalid")
            grants.append(EgressGrant(
                raw.get("grant_id"), raw.get("owner", machine_id), raw.get("kind"),
                tuple(raw.get("destinations", ())), tuple(raw.get("ports", ())),
                raw.get("expires_at"), revoked=raw.get("revoked", False),
            ))
        web_server = request.arguments.get("web_server", runtime.get("webServer") or self.web_server)

        # WordPress traffic must not spawn scheduled work by default.  The
        # legacy WordPress descriptor already owns the opt-in constant, so use
        # its explicit false value as the single source of truth for the
        # managed guest's five-minute scheduler as well.
        wp_cron = descriptor.get("wpCron", {"enabled": False}) \
            if isinstance(descriptor, dict) else {"enabled": False}
        if not isinstance(wp_cron, dict) or not isinstance(wp_cron.get("enabled"), bool):
            raise ValueError("WordPress cron policy is invalid")
        wp_cron_enabled = wp_cron["enabled"]

        # Preflight every descriptor-derived, resource, extension, and package
        # value before reserving a network.  ``reserve_network`` persists an
        # ownership record, so a malformed PHP extension request must fail
        # while the repository is still byte-for-byte unchanged.
        extension_plan = None
        if php_extensions is None:
            package_plan = self.packages.plan(web_server=web_server)
        else:
            extension_planner = self.extension_planner or ManagedExtensionPackagePlanner(
                self.packages, php_version=str(request.arguments.get(
                    "php_version", runtime.get("php") or "8.3")),
            )
            package_plan, extension_plan = extension_planner.resolve(
                web_server=web_server, requirements=php_extensions,
            )

        # Only the validated preflight may reach the repository mutation.
        allocation = self.repository.reserve_network(machine_id, owner=owner)
        network_values = {
            "egress": "deny", "veth": allocation["veth"],
            "host_address": allocation["host_address"],
            "guest_address": allocation["guest_address"],
            "default_route": False, "ingress_port": 8080,
            "grant_authority": EGRESS_GRANT_AUTHORITY,
        }
        database = self.database.plan(
            owner=f"{owner['project_root']}::{owner['label']}", machine_id=machine_id)
        policy = ManagedIsolationPolicy(
            policy_version=1,
            machine_id=machine_id,
            uid_map={"base": self._uid_base(allocation), "count": 65536},
            root_image={
                "path": f"/var/lib/sandbox/native/instances/{machine_id}/root.img",
                "bytes": resources["disk_bytes"], "inodes": resources["inodes"],
            },
            read_only_mounts=({"source": str(root), "target": "/workspace"},),
            writable_mounts=(),
            network=network_values,
            syscalls={"no_new_privileges": True, "seccomp": "managed-v1"},
            devices=frozenset(),
            resources={key: resources[key] for key in (
                "cpu_percent", "memory_bytes", "pids", "runtime_seconds",
                "disk_bytes", "inodes", "fds", "connections", "io_weight")},
            credentials=database["credential_refs"],
        )
        grant_set = EgressGrantSet(machine_id, policy.digest, tuple(grants))
        if extension_plan is None:
            service_plan = self.services.compile(
                policy, web_server=web_server, wp_cron_enabled=wp_cron_enabled,
            )
        else:
            service_plan = self.services.compile(
                policy, web_server=web_server, php_extensions=extension_plan,
                wp_cron_enabled=wp_cron_enabled,
            )
        database_plan = {**database, "policy_digest": policy.digest}
        record = {
            "owner": owner, "mode": "managed_native", "adapter": "ubuntu-nspawn",
            "backend": service_plan["backend"],
            "machine": {"id": machine_id, "policy_digest": policy.digest},
            "php": {"web": "8.3", "cli": "8.3",
                    **({"extensions": extension_plan.to_dict()}
                       if extension_plan is not None else {})},
            "database": {"production": database["production"], "tests": database["tests"],
                         "user": database["user"], "network_exposed": False},
            "files": {"image": canonical_digest(dict(policy.root_image))},
            "health": "ready", "policy_digest": policy.digest,
            "grant_digest": grant_set.digest,
        }
        record["last_applied"] = canonical_digest(record)
        plan = {
            "machine_id": machine_id, "policy_digest": policy.digest,
            "policy": policy, "web_server": web_server,
            "grant_set": grant_set,
            "package_plan": package_plan,
            "php_extensions": extension_plan,
            "apparmor": self.apparmor.plan(policy),
            "image": self.image.plan(policy),
            "machine": self.machine.plan(policy),
            "network": self.network.plan(policy),
            "database": database_plan, "services": service_plan,
            "wordpress": {"machine_id": machine_id, "policy_digest": policy.digest},
            "record": record,
        }
        cleanup_plans = {
            "services": service_plan, "database": database_plan,
            "machine": plan["machine"], "network": plan["network"],
            "mount": plan["image"], "image": plan["image"],
            "policy": {"machine_id": machine_id, "policy_digest": policy.digest},
        }
        plan["cleanup"] = {
            name: {
                "expected": {
                    "machine_id": machine_id, "policy_digest": policy.digest,
                    "resource": name,
                    "resource_digest": (service_plan["digest"]
                                        if name == "services" else policy.digest),
                },
                "plan": value,
            }
            for name, value in cleanup_plans.items()
        }
        return plan
