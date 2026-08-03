from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sandbox.runtimes.base import OperationRequest


@dataclass(frozen=True)
class ApplicationDependencies:
    """Concrete dependencies are assembled at transport composition roots."""

    registry: Any
    process: Any
    http: Any
    ports: Any
    paths: Any
    proxy: Any
    clock: Any | None = None


def runtime_neutral_dependencies(
    *, registry: Any, allowed_roots, proxy: Any, process: Any | None = None,
    http: Any | None = None, ports: Any | None = None, paths: Any | None = None,
    clock: Any | None = None,
) -> ApplicationDependencies:
    """Build generic mechanisms; callers retain runtime-specific proxy policy."""
    from sandbox.services import (
        AllowedRootPathPolicy, BoundedProcessRunner, SocketPortAllocator,
        UrlHttpProbe,
    )

    return ApplicationDependencies(
        registry=registry,
        process=process or BoundedProcessRunner(),
        http=http or UrlHttpProbe(),
        ports=ports or SocketPortAllocator(),
        paths=paths or AllowedRootPathPolicy(allowed_roots),
        proxy=proxy,
        clock=clock,
    )


def domain_service(cfg, **overrides):
    """Compose scoped naming policy without importing a compatibility facade."""
    import platform as host_platform
    import stat
    from pathlib import Path
    import sandbox_core as sc

    from sandbox.application.domain_service import DomainService
    from sandbox.application.instance_service import persist_hostname_intent
    from sandbox.network.adapters.resolved import ResolvedAdapter
    from sandbox.network.authority import DnsmasqAuthority
    from sandbox.network.detection import ResolverDetector
    from sandbox.network.manifest import built_in_resolver_registry
    from sandbox.network.repository import DomainRepository
    from sandbox.network.verification import DomainVerifier
    from sandbox.services import (
        BoundedProcessRunner, SocketDnsEndpointAllocator, UrlHttpProbe,
    )

    network_root = sc.sandbox_base() / "runtime" / "network"
    state_path = network_root / "resolver-state.json"
    process = overrides.pop("process", BoundedProcessRunner())
    http = overrides.pop("http", UrlHttpProbe())
    platform = overrides.pop(
        "platform", "darwin" if host_platform.system() == "Darwin" else "linux",
    )
    observer = overrides.pop(
        "observer", ResolverDetector(process=process, platform=platform).observe,
    )
    repository_helper = Path(__file__).resolve().parents[2] / "tools" / "resolver-helper.sh"
    installed_helper = "/usr/local/libexec/sandbox-resolver-helper"
    implementations = {}
    if platform == "linux":
        implementations["systemd-resolved"] = ResolvedAdapter(
            process=process, helper=installed_helper,
            repository_helper=str(repository_helper), network_root=network_root,
        )
    dnsmasq_path = Path("/usr/sbin/dnsmasq")
    dnsmasq = None
    try:
        details = dnsmasq_path.lstat()
        if (stat.S_ISREG(details.st_mode) and details.st_uid == 0
                and details.st_nlink == 1 and not details.st_mode & 0o022):
            dnsmasq = str(dnsmasq_path)
    except OSError:
        pass
    authority = overrides.pop("authority", None)
    if authority is None and dnsmasq:
        authority = DnsmasqAuthority(
            network_root / "authority", process=process, binary=dnsmasq,
        )

    def compatibility_offer(root, label):
        record = sc.registry_get(root, label=label) or {}
        port = record.get("wordpress_port") or record.get("http_port")
        fallback = record.get("url") or (
            f"http://localhost:{port}" if port else "http://localhost"
        )
        return {
            "accepted_addresses": ("127.0.0.77",),
            "fallback_url": fallback,
            "capabilities": {"wildcard": True, "tls": True},
        }

    verifier = overrides.pop(
        "verifier", DomainVerifier(process=process, http=http, platform=platform).verify,
    )

    def interactive_consent(owner_id):
        answer = input(
            "Allow Sandbox to install its fixed resolver helper and add a scoped "
            f"local DNS route through {owner_id}? [y/N] ",
        ).strip().lower()
        return answer in {"y", "yes"}

    return DomainService(
        config_loader=overrides.pop("config_loader", sc.load_project_config),
        project_registry=overrides.pop("project_registry", sc),
        adapters=overrides.pop(
            "adapters",
            built_in_resolver_registry(
                implementations,
                proof_attestation=overrides.pop("proof_attestation", None),
            ),
        ),
        repository=overrides.pop("repository", DomainRepository(state_path)),
        process=process,
        http=http,
        endpoints=overrides.pop("endpoints", SocketDnsEndpointAllocator()),
        observer=observer,
        ingress_offer=overrides.pop("ingress_offer", compatibility_offer),
        clock=overrides.pop("clock", None),
        authority=authority,
        verifier=verifier,
        consent_decider=overrides.pop("consent_decider", interactive_consent),
        platform=platform,
        identity_persister=overrides.pop(
            "identity_persister",
            lambda root, label, hostname, source: persist_hostname_intent(
                sc, root, label, hostname, source,
            ),
        ),
        binding_observer=overrides.pop(
            "binding_observer",
            lambda binding, adapter: adapter.observe(binding)
            if hasattr(adapter, "observe") else None,
        ),
        authority_observer=overrides.pop(
            "authority_observer",
            authority.status if authority is not None else None,
        ),
        **overrides,
    )


def ingress_service(cfg, **overrides):
    """Compose listener truth, owned state, consent, and transaction services."""
    import platform as host_platform
    import sandbox_core as sc
    from sandbox.application.ingress_service import IngressService
    from sandbox.ingress.adapters.caddy import CaddyAdapter
    from sandbox.ingress.detection import IngressDetector
    from sandbox.ingress.listeners import ListenerObserver, SocketBindProbe
    from sandbox.ingress.manifest import built_in_ingress_registry
    from sandbox.ingress.repository import IngressRepository
    from sandbox.ingress.transaction import IngressTransactionRunner
    from sandbox.ingress.verification import IngressVerifier
    from sandbox.services import BoundedProcessRunner, UrlHttpProbe

    platform = overrides.pop(
        "platform", "darwin" if host_platform.system() == "Darwin" else "linux",
    )
    process = overrides.pop("process", BoundedProcessRunner())
    http = overrides.pop("http", UrlHttpProbe())
    network_root = sc.sandbox_base() / "runtime" / "network"
    observer = overrides.pop(
        "listener_observer", ListenerObserver(platform=platform, process=process),
    )
    detector = overrides.pop("detector", IngressDetector(listener_observer=observer))
    implementations = {}
    if platform == "linux":
        implementations["system-caddy"] = CaddyAdapter(
            helper=overrides.pop(
                "ingress_helper", "/usr/local/libexec/sandbox-ingress-helper",
            ),
            process=process, network_root=network_root,
        )
    registry = overrides.pop("registry", built_in_ingress_registry(
        implementations,
        proof_attestation=overrides.pop("proof_attestation", None),
    ))
    repository = overrides.pop(
        "repository", IngressRepository(network_root / "ingress-state.json"),
    )
    verifier = overrides.pop("verifier", IngressVerifier(
        http=http, baseline_urls=lambda plan: tuple(plan.get("_baseline_urls", ())),
    ))
    transaction_runner = overrides.pop(
        "transaction_runner",
        IngressTransactionRunner(
            baseline_probe=verifier.baseline, route_probe=verifier.route,
        ),
    )

    def interactive_consent(identity):
        answer = input(
            f"Allow Sandbox to add an attributable route to {identity}? [y/N] ",
        ).strip().lower()
        return answer in {"y", "yes"}

    bind_address = overrides.pop("bind_address", "127.0.0.77")

    def sandbox_owns(endpoint) -> bool:
        """True when Sandbox's own proxy already serves this endpoint.

        Docker publishes the proxy's ports, so the listening process belongs to
        the container runtime and looks foreign to listener evidence alone. Ask
        the default provider whether the endpoint is its own before calling it a
        conflict (037 US1 scenario 3).
        """
        from sandbox.core._domains import proxy_endpoint_owned

        try:
            return proxy_endpoint_owned(endpoint.address, endpoint.port)
        except Exception:
            return False

    return IngressService(
        detector=detector, registry=registry, repository=repository,
        transaction_runner=transaction_runner,
        bind_address=bind_address,
        bind_probe=overrides.pop("bind_probe", SocketBindProbe()),
        consent_decider=overrides.pop("consent_decider", interactive_consent),
        sandbox_owner=overrides.pop("sandbox_owner", sandbox_owns),
        clock=overrides.pop("clock", None), **overrides,
    )


def clean_url_service(cfg, **overrides):
    """Compose the A→B→A ingress/DNS handshake without transport globals."""
    from sandbox.application.clean_url_service import CleanUrlService
    return CleanUrlService(
        ingress=overrides.pop("ingress", ingress_service(cfg)),
        domains=overrides.pop("domains", domain_service(cfg)),
        **overrides,
    )


def _native_helper_effective_probe(process, helper, gate):
    import json

    probes = {"private_network": "private-network", "nftables": "nftables",
              "cgroup_delegation": "cgroup-delegation", "seccomp": "seccomp"}
    probe = probes.get(gate)
    if probe is None:
        return None
    try:
        result = process.run(("sudo", "-n", helper, "preflight-probe", probe), timeout=20)
        observed = json.loads(result.stdout or "")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return bool(result.returncode == 0 and isinstance(observed, dict) and
                set(observed) == {"ok", "probe", "state"} and
                observed.get("ok") is True and observed.get("probe") == probe and
                observed.get("state") == "ready")


def native_isolation_preflight(cfg, **overrides):
    """Compose read-only effective managed-native prerequisite probes."""
    from pathlib import Path
    import shutil

    from sandbox.isolation.preflight import IsolationPreflight
    from sandbox.services import BoundedProcessRunner
    process = overrides.pop("process", BoundedProcessRunner())
    helper = overrides.pop("helper", "/usr/local/libexec/sandbox-native-helper")

    def facts():
        values = {}
        try:
            for line in Path("/etc/os-release").read_text().splitlines():
                if "=" in line:
                    key, value = line.split("=", 1); values[key] = value.strip().strip('"')
        except OSError: pass
        version = 0
        try:
            output = (process.run(("systemd", "--version"), timeout=2).stdout or "").split()
            version = int(output[1]) if len(output) > 1 else 0
        except (OSError, ValueError, IndexError): pass
        return {"os_id": values.get("ID"), "version_id": values.get("VERSION_ID"),
                "systemd_version": version}

    def effective(gate):
        helper_result = _native_helper_effective_probe(process, helper, gate)
        if helper_result is not None:
            return helper_result
        if gate == "pid1_systemd":
            try: return Path("/proc/1/comm").read_text().strip() == "systemd"
            except OSError: return False
        if gate == "cgroup_v2": return Path("/sys/fs/cgroup/cgroup.controllers").is_file()
        if gate == "user_namespaces":
            try: return int(Path("/proc/sys/user/max_user_namespaces").read_text()) > 0
            except (OSError, ValueError): return False
        if gate == "apparmor_enforcing":
            try: return "Y" in Path("/sys/module/apparmor/parameters/enabled").read_text()
            except OSError: return False
        if gate == "seccomp": return False
        return False

    return IsolationPreflight(
        facts=overrides.pop("facts", facts),
        command_probe=overrides.pop("command_probe", lambda command: shutil.which(command) is not None),
        effective_probe=overrides.pop("effective_probe", effective),
    )


def managed_package_planner(cfg, **overrides):
    """Compose read-only configured-source APT planning; never installs."""
    from sandbox.runtimes.managed.packages import AptPackageSimulator, ManagedPackagePlanner
    from sandbox.services import BoundedProcessRunner
    process = overrides.pop("process", BoundedProcessRunner())
    apt = overrides.pop("apt", AptPackageSimulator(process=process))
    return ManagedPackagePlanner(simulate=apt.simulate, sources=apt.sources)


def managed_host_service_baseline(cfg, **overrides):
    """Compose the fixed privileged foreign-service snapshot observer."""
    from sandbox.runtimes.managed.packages import PrivilegedHostServiceBaseline
    from sandbox.services import BoundedProcessRunner

    return PrivilegedHostServiceBaseline(
        process=overrides.pop("process", BoundedProcessRunner()),
        helper=overrides.pop("helper", "/usr/local/libexec/sandbox-native-helper"),
    )


def managed_package_service(cfg, *, web_server="nginx", **overrides):
    """Compose the explicit TTY-only host prerequisite transaction."""
    from pathlib import Path
    from sandbox.runtimes.managed.packages import (
        ManagedPackageService, NativeHostPackageApplier,
        PrivilegedHostServiceBaseline,
    )
    from sandbox.services import BoundedProcessRunner

    process = overrides.pop("process", BoundedProcessRunner())
    planner = overrides.pop("planner", managed_package_planner(cfg, process=process))
    repository_helper = Path(__file__).resolve().parents[2] / "tools/native-helper/native-helper.py"
    applier = overrides.pop("applier", NativeHostPackageApplier(
        process=process, repository_helper=str(repository_helper),
        installed_helper="/usr/local/libexec/sandbox-native-helper",
    ))
    baseline = overrides.pop(
        "baseline",
        PrivilegedHostServiceBaseline(
            process=process, helper="/usr/local/libexec/sandbox-native-helper",
        ),
    )

    def confirmation(plan):
        packages = ", ".join(f"{item['name']}={item['version']}"
                             for item in plan.host_packages
                             if item.get("name") in {"systemd-container", "bubblewrap", "nftables",
                                                     "debootstrap", "e2fsprogs"})
        print(f"Host prerequisites: {packages}")
        print("nginx/Apache, PHP and MariaDB will be installed only inside instance images.")
        answer = input(f"Apply native install plan {plan.simulation_digest}? [y/N] ").strip().lower()
        return answer in {"y", "yes"}

    return ManagedPackageService(
        replanner=overrides.pop("replanner", lambda: planner.plan(web_server=web_server)),
        apply_transaction=overrides.pop("apply_transaction", applier.apply),
        baseline_observer=overrides.pop("baseline_observer", baseline.observe),
        confirmation=overrides.pop("confirmation", confirmation),
        prepare_transaction=overrides.pop("prepare_transaction", applier.prepare),
    )

def wordpress_proxy_facade(cfg, *, core=None):
    """Read-only compatibility facade for retired aggregate proxy state.

    New route authority belongs to the composed ingress/resolver services.  The
    historical aggregate proxy has no root-owned applied receipt, so this
    facade validates declarations but deliberately preserves its state.
    """
    if core is None:
        import sandbox.core as core

    from sandbox.services import CallbackProxyManager

    def declared(config, hostname, port=None):
        for instance in core.resolve_instances(config).values():
            if instance.get("domain") != hostname:
                continue
            if port is None or int(instance.get("wordpress_port", 0)) == int(port):
                return True
        return False

    def validate(plan):
        if not declared(cfg, str(plan["hostname"]), int(plan["port"])):
            raise ValueError("proxy plan does not match a declared WordPress route")

    def apply_route(_hostname, _port):
        return None

    def remove_route(hostname):
        if declared(cfg, hostname):
            raise ValueError(f"proxy route {hostname!r} is still declared")
        return None

    return CallbackProxyManager(
        apply_route=apply_route,
        remove_route=remove_route,
        validate_plan=validate,
    )


def wordpress_runtime_dependencies(cfg, *, core=None, registry=None, **overrides):
    """Compose bounded mechanisms with the existing WordPress proxy facade."""
    if core is None:
        import sandbox.core as core
    if registry is None:
        import sandbox_core as sc
        registry = sc
    # Projects live in the developer's own directories, so the runtime must
    # accept the same roots the rest of the product does (home plus any
    # SANDBOX_PROJECT_ROOTS entries) alongside the checkout and the state base.
    # Limiting it to the latter two rejected every real project outright.
    default_roots = [core.ROOT, core.BASE]
    try:
        default_roots.extend(registry._allowed_roots())
    except (AttributeError, OSError, RuntimeError):
        pass
    allowed_roots = overrides.pop("allowed_roots", tuple(dict.fromkeys(default_roots)))
    return runtime_neutral_dependencies(
        registry=registry,
        allowed_roots=allowed_roots,
        proxy=overrides.pop("proxy", wordpress_proxy_facade(cfg, core=core)),
        **overrides,
    )


def managed_native_dependencies(cfg, *, registry, allowed_roots,
                                native_repository=None, **overrides):
    """Compose managed-native mechanisms without selecting a host fallback.

    Construction is deliberately inert: helper-backed mechanisms are injected
    for later capability-gated operations, not invoked while a runtime service
    is being composed.
    """
    from sandbox.isolation.apparmor import ManagedAppArmor
    from sandbox.isolation.bubblewrap import BubblewrapCompiler
    from sandbox.isolation.credentials import (
        CredentialInjector, HelperCredentialInstaller, NativeCredentialStore,
    )
    from sandbox.isolation.launcher import IsolationLauncher
    from sandbox.isolation.network import EgressGrantReconciler, ManagedNetwork
    from sandbox.isolation.resources import ResourcePolicyCompiler
    from sandbox.isolation.verification import IsolationVerifier
    from sandbox.runtimes.managed.adapter import (
        ManagedNativeCleanup, ManagedProvisioner, ManagedRuntimeDependencies,
    )
    from sandbox.runtimes.managed.database import ManagedDatabase
    from sandbox.runtimes.managed.helper import (
        ManagedCleanupObserver, ManagedIsolationObserver, ManagedMachineExecutor,
    )
    from sandbox.runtimes.managed.image import ManagedImage, ManagedRootfs
    from sandbox.runtimes.managed.machine import ManagedMachine
    from sandbox.runtimes.managed.packages import PackagePlanStager
    from sandbox.runtimes.managed.plan import ManagedPlanBuilder, ManagedPolicyStore
    from sandbox.runtimes.managed.services import ManagedServiceCompiler, ManagedServiceSupervisor
    from sandbox.runtimes.managed.wordpress import ManagedWordPressBootstrap
    from sandbox.services import AllowedRootPathPolicy, BoundedProcessRunner, UrlHttpProbe

    process = overrides.pop("process", BoundedProcessRunner())
    helper = overrides.pop("helper", "/usr/local/libexec/sandbox-native-helper")
    isolation = overrides.pop("isolation", None)
    if isolation is None:
        isolation = native_isolation_preflight(cfg, process=process)
    packages = overrides.pop("packages", None)
    if packages is None:
        packages = managed_package_planner(cfg, process=process)
    grants = overrides.pop("grants", EgressGrantReconciler(process=process, helper=helper))
    network = overrides.pop("network", ManagedNetwork(process=process, helper=helper))
    database = overrides.pop("database", ManagedDatabase(process=process, helper=helper))
    services = overrides.pop(
        "services", ManagedServiceSupervisor(process=process, helper=helper),
    )
    credential_installer = overrides.pop(
        "credential_installer", HelperCredentialInstaller(process=process, helper=helper),
    )
    native_root = registry.sandbox_base() / "runtime" / "native"
    credential_store = overrides.pop(
        "credential_store", NativeCredentialStore(native_root / "credentials"),
    )
    credentials = overrides.pop("credentials", CredentialInjector(
        secret_provider=credential_store, installer=credential_installer,
    ))
    observer = overrides.pop(
        "observer", ManagedIsolationObserver(process=process, helper=helper),
    )
    verifier = overrides.pop("verifier", IsolationVerifier(observe=observer))
    image = overrides.pop("image", ManagedImage(process=process, helper=helper))
    apparmor = overrides.pop("apparmor", ManagedAppArmor(process=process, helper=helper))
    machine = overrides.pop("machine", ManagedMachine(process=process, helper=helper))
    policy = overrides.pop("policy", ManagedPolicyStore(process=process, helper=helper))
    service_compiler = overrides.pop("service_compiler", ManagedServiceCompiler())
    paths = overrides.pop("paths", AllowedRootPathPolicy(allowed_roots))
    plan_builder = overrides.pop("plan_builder", None)
    if plan_builder is None and native_repository is not None:
        plan_builder = ManagedPlanBuilder(
            repository=native_repository, packages=packages,
            resources=ResourcePolicyCompiler(), network=network, image=image,
            apparmor=apparmor, machine=machine, database=database,
            services=service_compiler,
            descriptor_resolver=registry.load_project_config,
            paths=paths,
        )
    wordpress = overrides.pop(
        "wordpress", ManagedWordPressBootstrap(process=process, helper=helper),
    )
    rootfs = overrides.pop(
        "rootfs", ManagedRootfs(process=process, helper=helper, stager=PackagePlanStager()),
    )
    provisioner = overrides.pop("provisioner", None)
    if provisioner is None and native_repository is not None:
        provisioner = ManagedProvisioner(
            policy=policy, apparmor=apparmor, image=image, rootfs=rootfs,
            machine=machine, network=network, verifier=verifier,
            credentials=credentials, database=database, wordpress=wordpress,
            services=services, health=lambda plan: services.backend_health(plan["services"]),
            repository=native_repository, grants=grants,
        )
    launcher = overrides.pop("launcher", IsolationLauncher(
        verifier=verifier, bubblewrap=BubblewrapCompiler("/usr/bin/bwrap"),
        machine_exec=ManagedMachineExecutor(process=process, helper=helper),
    ))
    cleanup = overrides.pop("cleanup", None)
    if cleanup is None and native_repository is not None:
        cleanup = ManagedNativeCleanup(
            repository=native_repository, services=services, database=database,
            network=network, machine=machine, image=image, policy=policy,
            observe=ManagedCleanupObserver(process=process, helper=helper),
        )
    return ManagedRuntimeDependencies(
        process=process,
        http=overrides.pop("http", UrlHttpProbe()),
        paths=paths,
        registry=registry,
        isolation=isolation,
        packages=packages,
        network=network, database=database, services=services, credentials=credentials,
        grants=grants,
        verifier=verifier, launcher=launcher, plan_builder=plan_builder,
        provisioner=provisioner, cleanup=cleanup,
        **overrides,
    )


def runtime_service(cfg):
    """Compose WordPress compatibility and the framework-neutral Compose adapter."""
    import sandbox.core as core
    import os
    import sandbox_core as sc
    from pathlib import Path

    from sandbox.application.runtime_service import RuntimeService
    from sandbox.runtimes.compose import ComposeAdapter
    from sandbox.runtimes import builtin_adapter_registry
    from sandbox.runtimes.registry import RuntimeBackendRegistry
    from sandbox.runtimes.managed.adapter import (
        ManagedNativeAdapter, _proof_candidate_authority,
    )
    from sandbox.runtimes.manifest import RUNTIME_DECLARATIONS
    from sandbox.runtimes.managed.repository import NativeRepository

    def resolve_descriptor(root, label=None):
        return sc.load_project_config(root, label=label)

    def ensure(request: OperationRequest):
        return core.ensure_instance(
            cfg,
            request.project_root,
            label=request.label,
            create=bool(request.arguments.get("create", False)),
        )

    def apply(request: OperationRequest):
        return core.apply_config(cfg, request.project_root, label=request.label)

    def status(request: OperationRequest):
        entry = sc.registry_get(request.project_root, label=request.label)
        return {"ok": entry is not None, **(entry or {})}

    dependencies = runtime_neutral_dependencies(
        registry=sc, allowed_roots=(core.ROOT, core.BASE),
        proxy=wordpress_proxy_facade(cfg, core=core),
    )
    compose = ComposeAdapter(dependencies, sc)
    compose.capabilities = frozenset({
        *compose.capabilities,
        "compose.exec",
        "compose.remote-deploy",
    })
    adapters = builtin_adapter_registry(
        {"ensure": ensure, "apply": apply, "status": status}, compose=compose,
    )
    adapters.for_kind("wordpress").adapter.capabilities = frozenset({
            *adapters.for_kind("wordpress").adapter.capabilities,
            "compose.exec",
            "wordpress.cli", "wordpress.exec", "wordpress.rest",
            "wordpress.snapshot", "wordpress.restore", "wordpress.reset",
            "wordpress.database", "wordpress.files", "wordpress.mail",
            "wordpress.abilities",
            "wordpress.remote-deploy", "wordpress.remote-preview",
        })
    backends = RuntimeBackendRegistry()
    wordpress_adapter = adapters.for_kind("wordpress").adapter
    backends.register(
        "compose", wordpress_adapter, project_kinds=("wordpress",), modes=("compose",),
        owner="sandbox.runtimes.wordpress", order=10,
    )
    native_repository = NativeRepository(
        sc.sandbox_base() / "runtime" / "native" / "state.json",
    )
    managed_dependencies = managed_native_dependencies(
        cfg, registry=sc, allowed_roots=(core.ROOT, core.BASE),
        native_repository=native_repository,
    )
    managed_declaration = next(
        value for value in RUNTIME_DECLARATIONS
        if value["adapter_id"] == "ubuntu-nspawn"
    )
    managed_evidence = (
        managed_declaration.get("evidence_id")
        if managed_declaration.get("adoptable") is True
        and managed_declaration.get("support_tier") == "adoptable"
        else None
    )
    managed = ManagedNativeAdapter(
        preflight=managed_dependencies.isolation, repository=native_repository,
        dependencies=managed_dependencies, evidence_id=managed_evidence,
        proof_candidate_authority=_proof_candidate_authority(
            os.environ.get("SANDBOX_NATIVE_PROOF_CANDIDATE"),
        ),
    )
    backends.register(
        "ubuntu-nspawn", managed, project_kinds=("wordpress",),
        modes=("managed_native",), owner="sandbox.runtimes.managed.adapter", order=20,
    )
    def persisted_selection(root, label):
        owner = f"{Path(root).expanduser().resolve()}::{label}"
        state = native_repository.snapshot()
        selected = state["selections"].get(owner)
        if selected: return selected
        backend = next((value for value in state["backends"].values()
                        if value.get("owner") == owner), None)
        return {"mode": backend.get("mode"), "adapter": backend.get("adapter"),
                "populated": True} if backend else None

    return RuntimeService(resolve_descriptor=resolve_descriptor, adapters=adapters,
                          backends=backends, resolve_persisted=persisted_selection)


def wordpress_runtime_service(cfg):
    """Backward-compatible name for the shared runtime composition root."""
    return runtime_service(cfg)


def preflight_instance_capability(cfg, instance: str, capability: str):
    import sandbox_core as sc

    from sandbox.runtimes.base import OperationError

    owner = sc.registry_find_instance(instance)
    if not owner or not owner.get("root"):
        return OperationError(
            code="unknown_instance_owner",
            message=f"instance {instance!r} has no registered project owner",
            requested_capability=capability,
        )
    return runtime_service(cfg).check(
        owner["root"], capability, label=owner.get("label", "default")
    )


def preflight_project_capability(cfg, project_root: str, capability: str, *, label: str = "default"):
    """Validate a project-scoped operation before any remote or local mutation."""
    return runtime_service(cfg).check(project_root, capability, label=label)


def execute_project(cfg, execution):
    """Run one validated project payload through its selected runtime adapter."""
    from sandbox.runtimes.base import (
        ExecutionRequest, ExecutionResult, OperationError, OperationRequest,
    )
    if not isinstance(execution, ExecutionRequest):
        raise ValueError("project execution request is invalid")
    operation = {
        "wordpress_cli": "wordpress_cli", "wp_eval": "wordpress_cli",
        "exec": "exec", "composer": "exec", "plugin_activation": "exec",
        "phpunit": "test", "durable_job": "exec",
    }[execution.entry_path]
    result = runtime_service(cfg).invoke(OperationRequest(
        execution.project_root, operation, label=execution.label,
        arguments={"execution": execution},
    ))
    if isinstance(result, OperationError):
        return ExecutionResult(False, 126, "blocked", {
            "stdout": "", "stderr": result.message,
            "reason": {"code": result.code, "message": result.message},
        })
    data = dict(result.data)
    return ExecutionResult(result.ok, data.get("exit_code", 0 if result.ok else 126),
                           data.get("state", "ready" if result.ok else "blocked"), {
        "stdout": data.get("stdout", ""), "stderr": data.get("stderr", ""),
        "reason": data.get("reason", {"code": "ready" if result.ok else
                                        "isolated_payload_failed"}),
    })


def managed_native_project_selected(project_root: str, *, label: str = "default") -> bool:
    """Read the selected runtime through the application boundary.

    Legacy execution callers use this solely to fail closed before dispatching
    project-controlled argv to Compose, Herd, or a host subprocess.
    """
    import sandbox_core as sc

    config = sc.load_project_config(project_root, label=label)
    runtime = config.get("wordpressRuntime", {}) if isinstance(config, dict) else {}
    return runtime.get("mode", "compose") == "managed_native"


def managed_native_instance_selected(instance: str) -> tuple[str, str] | None:
    """Return a managed instance owner, or ``None`` for legacy runtimes."""
    import sandbox_core as sc

    owner = sc.registry_find_instance(instance) or {}
    root, label = owner.get("root"), owner.get("label", "default")
    if not root or not managed_native_project_selected(str(root), label=label):
        return None
    return str(root), label


def _remote_workspace_control(resolved_target, action):
    """Delegate one confirmed workspace lifecycle request to the remote CLI."""
    import json
    import shlex
    from sandbox.core import _remote

    remote = _remote.get_remote(resolved_target.remote_name)
    deployed = None
    if action == "create":
        deployed = _remote.deploy_exact_working_tree(
            remote, resolved_target.project_root)
        workspace_path = _remote.prepare_remote_workspace(
            remote, resolved_target.project_root,
            resolved_target.workspace_label,
            deployed_path=deployed["target_path"])
    else:
        workspace_path = _remote.remote_workspace_path(
            remote, resolved_target.project_root,
            resolved_target.workspace_label)
    sb = _remote.remote_sb_path(remote)
    if action in {"reset", "destroy"}:
        busy_command = shlex.join([
            sb, "job-list", "--project-dir", workspace_path,
            "--workspace", resolved_target.workspace_label,
            "--active-only", "--json",
        ])
        busy_result = _remote.ssh_run(remote, busy_command, timeout=25)
        busy_payload = next((
            json.loads(line)
            for line in reversed((busy_result.stdout or "").splitlines())
            if line.startswith("{")
        ), None)
        if busy_result.returncode != 0 or not busy_payload:
            raise RuntimeError("remote workspace activity check failed")
        if busy_payload.get("jobs"):
            raise RuntimeError(
                f"workspace {resolved_target.workspace_label!r} "
                "is busy with active remote jobs")
    command_args = [
        sb, "workspace", action, "--local", "--project-dir",
        workspace_path, "--workspace", resolved_target.workspace_label,
    ]
    if action in {"reset", "destroy"}:
        command_args.append("--confirm")
    command_args.append("--json")
    result = _remote.ssh_run(
        remote, shlex.join(command_args), timeout=60)
    payload = next((
        json.loads(line)
        for line in reversed((result.stdout or "").splitlines())
        if line.startswith("{")
    ), None)
    if result.returncode != 0 or not payload:
        detail = (result.stderr or result.stdout or "").strip().replace(
            "\n", " ")[:500]
        raise RuntimeError(
            f"remote workspace control failed"
            f"{': ' + detail if detail else ''}")
    return {**payload, "source": deployed}


def durable_job_dependencies():
    """Compose host-local durable-job services for CLI and MCP adapters."""
    import time
    import sandbox_core as sc

    from sandbox.application.job_service import JobService
    from sandbox.application.target_service import TargetService
    from sandbox.application.workspace_service import WorkspaceService
    from sandbox.config.runtime import BUILTIN_EXECUTION_PROFILES, BUILTIN_OUTPUT_PROFILES
    from sandbox.core._paths import RUNTIME_DIR
    from sandbox.core._remote import get_remote
    from sandbox.jobs.manifest import builtin_job_component_registry
    from sandbox.jobs.process import capture_process_identity
    from sandbox.jobs.registry import JobRepository
    from sandbox.jobs.storage import JobStorage
    from sandbox.jobs.scheduler import JobScheduler

    repository = JobRepository(RUNTIME_DIR / "jobs" / "registry.sqlite3")
    storage = JobStorage(RUNTIME_DIR)
    profiles = {
        "execution": BUILTIN_EXECUTION_PROFILES,
        "output": BUILTIN_OUTPUT_PROFILES,
    }
    components = builtin_job_component_registry(
        repository=repository, storage=storage,
        process_identity=capture_process_identity, clock=time, profiles=profiles,
    )
    target = TargetService(
        config_loader=sc.load_project_config, remote_lookup=get_remote)
    scheduler = JobScheduler(repository)
    workspace = WorkspaceService(
        target, storage, _remote_workspace_control, scheduler)
    job = JobService(
        repository, storage, components, scheduler=scheduler,
        runtime_selector=managed_native_project_selected,
    )
    # A controller process may start after its host or a prior supervisor was
    # interrupted. Reconcile bounded active state before exposing services so
    # callers never inherit a stale running/lease claim as healthy work.
    job.reconcile_startup()
    return {
        "job_service": job,
        "target_service": target,
        "workspace_service": workspace,
    }
