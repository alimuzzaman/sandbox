"""Render fixed nginx/Apache, PHP-FPM, MariaDB and cron service configuration."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shlex
from collections.abc import Mapping

from sandbox.isolation.models import canonical_digest
from sandbox.isolation.bubblewrap import USERNS_FILTER_FD, userns_filtered_argv
from sandbox.isolation.seccomp import compile_userns_filter
from sandbox.isolation.credential_controller_lifecycle_v2 import (
    DerivedServiceConfigV2,
    derived_config_document,
)
from sandbox.isolation.credential_controller_service_v2 import (
    BoundGuestSubmitCapabilityV2,
)


PERSISTENT_WRITABLE_TARGETS = frozenset({
    "/var/www/html", "/var/lib/sandbox", "/var/log/sandbox", "/run/mysqld", "/run/php",
})

CREDENTIAL_BROKER_STATUS_FIELDS = frozenset({
    "ok", "state", "machine_id", "policy_digest", "egress_digest", "broker_digest",
    "executable_digest", "config_digest", "admission_open", "broker_epoch", "pid",
    "process_start_identity", "service_uid", "unit_identity", "cgroup_identity",
})
CREDENTIAL_BROKER_SERVICE_UID = 991
CREDENTIAL_CONTROLLER_SERVICE_UID_V2 = 992
CREDENTIAL_BROKER_SERVICE_UID_V2 = 993
CONTROLLER_PROTOCOL_V2 = "credential-broker-controller-v2"
_GUEST_BRIDGE_ISSUER = object()


class CredentialV2GuestBridge(tuple):
    """Exact managed adapter bridge to one authenticated v2 broker session.

    It deliberately exposes no legacy ``handle`` method and owns no authority
    decision.  Canonical guest validation and time are fixed dependencies of
    the composed bridge, never values supplied by a public caller.  This is an
    ordinary trusted-process API boundary.  Python reflection or monkeypatching
    in this process is process compromise, not a property this wrapper claims
    to prevent; untrusted guests receive only the socket protocol, never this
    object.
    """

    protocol = CONTROLLER_PROTOCOL_V2
    __slots__ = ()

    def __new__(cls, issuer, capability):
        if (issuer is not _GUEST_BRIDGE_ISSUER
                or type(capability) is not BoundGuestSubmitCapabilityV2):
            raise ValueError("credential v2 guest bridge is invalid")
        return tuple.__new__(cls, (capability,))

    def __setattr__(self, name, value):
        del name, value
        raise AttributeError("credential v2 guest bridge is immutable")

    def __delattr__(self, name):
        del name
        raise AttributeError("credential v2 guest bridge is immutable")

    def __repr__(self):
        return "CredentialV2GuestBridge(protocol='credential-broker-controller-v2')"

    def __getitem__(self, key):
        del key
        raise TypeError("credential v2 guest bridge cells are private")

    def __iter__(self):
        raise TypeError("credential v2 guest bridge cells are private")

    def __len__(self):
        return 0

    def __add__(self, value):
        del value
        raise TypeError("credential v2 guest bridge cells are private")

    def __mul__(self, value):
        del value
        raise TypeError("credential v2 guest bridge cells are private")

    __rmul__ = __mul__

    def __copy__(self):
        raise TypeError("credential v2 guest bridge cannot be copied")

    def __deepcopy__(self, memo):
        del memo
        raise TypeError("credential v2 guest bridge cannot be copied")

    def __reduce__(self):
        raise TypeError("credential v2 guest bridge cannot be serialized")

    def __reduce_ex__(self, protocol):
        del protocol
        raise TypeError("credential v2 guest bridge cannot be serialized")

    def submit_guest_v2(self, request, *, connection_identity):
        try:
            capability = tuple.__getitem__(self, 0)
            if type(capability) is not BoundGuestSubmitCapabilityV2:
                raise ValueError
        except Exception:
            raise RuntimeError("credential v2 guest bridge is unavailable") from None
        return capability(request, connection_identity=connection_identity)


def build_credential_v2_guest_bridge(receipt, connection, *,
                                     canonical_guest_validator, now_ms):
    from sandbox.isolation.credential_controller_service_v2 import (
        AuthenticatedBrokerCompositionReceiptV2,
    )
    if type(receipt) is not AuthenticatedBrokerCompositionReceiptV2:
        raise ValueError("credential v2 composition receipt is required")
    try:
        receipt.require_exact_broker(connection)
        capability = connection.bind_guest_submit_capability_v2(
            receipt, canonical_guest_validator=canonical_guest_validator,
            now_ms=now_ms,
        )
    except Exception:
        raise ValueError("credential v2 composition receipt is invalid") from None
    return CredentialV2GuestBridge(_GUEST_BRIDGE_ISSUER, capability)


def credential_broker_unit_identity(machine_id):
    return f"sandbox-credential-broker@{machine_id}.service"


def credential_broker_cgroup_identity(machine_id):
    return f"/sandbox.slice/credential-broker/{machine_id}"


def compile_credential_service_plans_v2(*, machine_id, service_gid,
                                        controller_executable_digest,
                                        broker_executable_digest,
                                        controller_config_identity,
                                        broker_config_identity,
                                        policy_digest, egress_digest,
                                        broker_digest, proof_digest,
                                        effective_isolation_digest,
                                        evidence_id, controller_config_digest,
                                        broker_config_digest,
                                        controller_endpoint_identity,
                                        lease_endpoint_identity,
                                        guest_endpoint_identity):
    """Derive only immutable secret-free v2 controller/broker config plans."""

    common = {
        "machine_id": machine_id, "service_gid": service_gid,
        "policy_digest": policy_digest, "egress_digest": egress_digest,
        "broker_digest": broker_digest, "proof_digest": proof_digest,
        "effective_isolation_digest": effective_isolation_digest,
        "evidence_id": evidence_id,
        "controller_endpoint_identity": controller_endpoint_identity,
        "lease_endpoint_identity": lease_endpoint_identity,
        "guest_endpoint_identity": guest_endpoint_identity,
    }
    controller = DerivedServiceConfigV2.derive(derived_config_document(
        component="controller", unit_identity=f"sandbox-credential-controller-v2@{machine_id}.service",
        service_uid=CREDENTIAL_CONTROLLER_SERVICE_UID_V2,
        executable_digest=controller_executable_digest,
        config_identity=controller_config_identity,
        peer_executable_digest=broker_executable_digest,
        peer_config_digest=broker_config_digest,
        own_config_digest=controller_config_digest, **common,
    ))
    broker = DerivedServiceConfigV2.derive(derived_config_document(
        component="broker", unit_identity=f"sandbox-credential-broker-v2@{machine_id}.service",
        service_uid=CREDENTIAL_BROKER_SERVICE_UID_V2,
        executable_digest=broker_executable_digest,
        config_identity=broker_config_identity,
        peer_executable_digest=controller_executable_digest,
        peer_config_digest=controller_config_digest,
        own_config_digest=broker_config_digest, **common,
    ))
    return {"controller": controller, "broker": broker}


def _persistent_payload(command, writable_targets):
    argv = ["/usr/bin/bwrap", "--die-with-parent", "--new-session", "--clearenv",
            "--unshare-user", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
            "--ro-bind", "/", "/"]
    for target in sorted(PERSISTENT_WRITABLE_TARGETS | frozenset(writable_targets)):
        argv.extend(("--bind", target, target))
    argv.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                 "--tmpfs", "/run/credentials", "--dir", "/run/credentials/sandbox",
                 "--tmpfs", "/run/sandbox-native-credentials",
                 "--tmpfs", "/run/systemd", "--tmpfs", "/run/dbus",
                 "--tmpfs", "/run/host",
                 "--chdir", "/workspace", "--cap-drop", "ALL", "--uid", "33", "--gid", "33",
                 "--seccomp", str(USERNS_FILTER_FD),
                 "--setenv", "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                 "--setenv", "HOME", "/var/www", "--setenv", "USER", "www-data",
                 "--setenv", "LOGNAME", "www-data", "--", *command))
    return "#!/bin/sh\nset -eu\nexec " + shlex.join(userns_filtered_argv(argv)) + "\n"


def compile_service_files(guest, connections, runtime_seconds, *, web_server, backend_port,
                          writable_targets=(), guest_machine=None,
                          wp_cron_enabled=False):
    if type(wp_cron_enabled) is not bool:
        raise ValueError("managed WordPress cron setting is invalid")
    guest_machine = guest_machine or os.uname().machine
    php_children = max(2, min(32, connections // 4))
    common_php = (
        "[sandbox]\nuser = www-data\ngroup = www-data\n"
        # The socket is created through bubblewrap, which maps the machine's
        # root to 33 inside, so FPM cannot chown it to www-data without
        # CAP_CHOWN -- which the payload deliberately does not have. The
        # mode lets the web server connect instead; the socket lives in the
        # machine's private /run, which only the machine's own processes
        # can reach.
        "listen = /run/php/sandbox.sock\nlisten.mode = 0666\n"
        f"pm = dynamic\npm.max_children = {php_children}\npm.start_servers = 2\n"
        "pm.min_spare_servers = 1\npm.max_spare_servers = 4\nclear_env = yes\n"
        "security.limit_extensions = .php\ncatch_workers_output = yes\n"
        f"request_terminate_timeout = {runtime_seconds}s\n"
        # Keep managed PHP aligned with config/php-sandbox.ini and the web
        # server request ceiling so large WordPress imports are not truncated
        # at a lower layer.
        "php_admin_value[upload_max_filesize] = 1024M\n"
        "php_admin_value[post_max_size] = 1024M\n"
        "php_admin_value[upload_tmp_dir] = /var/lib/sandbox/tmp\n"
        "php_admin_value[session.save_path] = /var/lib/sandbox/sessions\n"
        "php_admin_value[open_basedir] = /var/www/html:/workspace:/var/lib/sandbox:/tmp:/usr/share/php\n"
    )
    if web_server == "nginx":
        web_path = "/etc/nginx/sites-enabled/sandbox.conf"
        nginx = ("user www-data;\nworker_processes 1;\npid /run/nginx.pid;\n"
                 "error_log /var/log/nginx/error.log;\n"
                 f"events {{ worker_connections {connections}; }}\n"
                 "http {\n    include /etc/nginx/mime.types;\n"
                 "    default_type application/octet-stream;\n"
                 "    access_log /var/log/nginx/access.log;\n"
                 "    sendfile on;\n    keepalive_timeout 15;\n"
                 "    include /etc/nginx/sites-enabled/*;\n}\n")
        web = (f"server {{\n    listen {guest}:{backend_port} default_server;\n"
               "    server_name _;\n    root /var/www/html;\n    index index.php;\n"
               "    client_max_body_size 1024m;\n"
               "    location / { try_files $uri $uri/ /index.php?$args; }\n"
               "    location ~ \\.php$ { try_files $uri =404; include fastcgi_params; "
               "fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name; "
               "fastcgi_pass unix:/run/php/sandbox.sock; }\n"
               "    location ~ /\\. { deny all; }\n}\n")
        units = ("mariadb.service", "php8.3-fpm.service", "nginx.service", "cron.service")
    else:
        web_path = "/etc/apache2/sites-enabled/000-sandbox.conf"
        apache_limits = ("KeepAlive Off\n<IfModule mpm_prefork_module>\n"
                         "    StartServers 1\n    MinSpareServers 1\n"
                         f"    MaxSpareServers {min(10, connections)}\n"
                         f"    ServerLimit {connections}\n"
                         f"    MaxRequestWorkers {connections}\n"
                         "    MaxConnectionsPerChild 10000\n</IfModule>\n")
        web = (f"Listen {guest}:{backend_port}\n<VirtualHost {guest}:{backend_port}>\n"
               "    DocumentRoot /var/www/html\n    DirectoryIndex index.php\n"
               "    <Directory /var/www/html>\n        Options FollowSymLinks\n"
               "        AllowOverride All\n        Require all granted\n"
               "    </Directory>\n"
               "    <FilesMatch \\.php$>\n"
               "        SetHandler \"proxy:unix:/run/php/sandbox.sock|fcgi://localhost/\"\n"
               "    </FilesMatch>\n</VirtualHost>\n")
        units = ("mariadb.service", "php8.3-fpm.service", "apache2.service", "cron.service")
    database = ("[mysqld]\nskip-networking=1\nskip-name-resolve=1\nlocal-infile=0\n"
                f"socket=/run/mysqld/mysqld.sock\nmax_connections={connections}\n")
    php_command = ("/usr/sbin/php-fpm8.3", "--nodaemonize", "--force-stderr",
                   "--fpm-config", "/etc/php/8.3/fpm/php-fpm.conf")
    cron_command = ("/usr/bin/timeout", "--signal=TERM", "--kill-after=5s",
                    f"{runtime_seconds}s", "/usr/local/bin/wp", "cron", "event", "run", "--due-now",
                    "--path=/var/www/html")
    # php-fpm opens its global error log before anything else, and the distro
    # default (/var/log/php8.3-fpm.log) is read-only inside the sandbox, so FPM
    # died with "failed to open error_log ... Read-only file system" before it
    # ever served a request. The log goes to the writable directory the payload
    # already declares; --force-stderr sends it to the journal as well.
    fpm_global = ("[global]\n"
                  "error_log = /var/log/sandbox/php-fpm.log\n"
                  "daemonize = no\n"
                  "include = /etc/php/8.3/fpm/pool.d/*.conf\n")
    files = {web_path: web, "/etc/php/8.3/fpm/pool.d/sandbox.conf": common_php,
             "/etc/php/8.3/fpm/php-fpm.conf": fpm_global,
             "/etc/mysql/mariadb.conf.d/90-sandbox.cnf": database,
             "/usr/local/libexec/sandbox-php-fpm":
             _persistent_payload(php_command, writable_targets),
             "/usr/local/libexec/sandbox-wordpress-cron":
             _persistent_payload(cron_command, writable_targets),
        "/etc/systemd/system/php8.3-fpm.service.d/sandbox-isolation.conf":
             "[Service]\nType=simple\nExecStartPre=/usr/bin/install -d -o root -g root -m 0755 /run/php\nExecStart=\nExecStart=/usr/local/libexec/sandbox-php-fpm\n",
             "/etc/cron.d/sandbox-wordpress":
             ("*/5 * * * * root /usr/local/libexec/sandbox-wordpress-cron >/dev/null 2>&1\n"
              if wp_cron_enabled else
              "# WordPress cron disabled by Sandbox; opt in with DISABLE_WP_CRON=false.\n"),
             # Declared writable targets under /run must exist from boot, not
             # from the moment their service happens to start. /run is a tmpfs,
             # so without this the isolation probe -- and any command run before
             # the database is up -- dies in bwrap with "Can't find source path
             # /run/mysqld".
             # The credential source root is masked with a tmpfs on every
             # payload, and bwrap cannot create the mountpoint because the
             # sandbox root is read-only: "Can't mkdir
             # /run/sandbox-native-credentials: Read-only file system".
             # It has to exist whether or not a credential was ever staged,
             # or the mask silently depends on that having happened.
             # The payload's seccomp filter, shipped as a file because bwrap
             # takes it as a file descriptor and the payload launcher has
             # to open it before exec. It is part of the service digest, so
             # a changed filter is a changed configuration.
             "/etc/sandbox-native/userns-filter.bpf":
             compile_userns_filter(guest_machine).hex(),
             "/etc/tmpfiles.d/sandbox-runtime-dirs.conf":
             "d /run/mysqld 0755 mysql mysql -\nd /run/php 0755 root root -\n"
             # Every payload and persistent service reaches the document
             # root through bubblewrap, which maps the machine's root uid to
             # 33 inside the sandbox. Writes are therefore checked against
             # root, not www-data, so a docroot owned by www-data is one the
             # sandbox cannot write: WordPress could not create wp-config.php.
             # Root ownership makes it writable, and the files still read back
             # as www-data inside the sandbox because that is what the mapping
             # says. The web server only reads, which 0755 allows.
             "d /var/www/html 0755 root root -\n"
             "d /run/sandbox-native-credentials 0700 root root -\n"}
    # NoNewPrivileges lives on the guest's own units, not on the machine: on the
    # machine it blocks the AppArmor transition into the tighter `//guest`
    # profile, and the guest init can never exec. Every untrusted execution path
    # inside the guest is one of these services, so the flag still covers them.
    # NoNewPrivileges cannot go on a unit that launches bubblewrap: entering
    # the tighter bwrap profile is an AppArmor domain transition, and the
    # kernel refuses one under NNP -- php-fpm died with "exec /usr/bin/bwrap:
    # Operation not permitted". This is the same carve-out FR-043 already
    # makes for the machine, one layer down, and it costs nothing: bubblewrap
    # sets NNP itself before exec'ing the payload, so the untrusted code still
    # runs under it. Units that never launch bubblewrap keep the flag.
    launchers = {"php8.3-fpm.service", "cron.service"}
    for unit in units:
        files[f"/etc/systemd/system/{unit}.d/sandbox-no-new-privileges.conf"] = (
            "[Service]\n"
            + ("" if unit in launchers else "NoNewPrivileges=yes\n")
            # The guest profile grants sys_admin so PID 1 can mount its API
            # filesystems; no service that runs project code may keep it.
            + "CapabilityBoundingSet=~CAP_SYS_ADMIN CAP_SYS_PTRACE CAP_SYS_MODULE "
            "CAP_SYS_RAWIO CAP_SYS_BOOT CAP_MKNOD\n"
            # A launcher must be able to create the namespaces bubblewrap builds
            # the sandbox from, so it cannot carry a blanket namespace
            # restriction -- php-fpm failed to start with it. The payload gets no
            # namespace privilege from this: bubblewrap drops every capability and
            # the seccomp filter refuses a nested user namespace (FR-046).
            + ("" if unit in launchers else "RestrictNamespaces=yes\n")
            + "ProtectKernelTunables=yes\n"
        )
    if web_server == "nginx":
        files["/etc/nginx/nginx.conf"] = nginx
    else:
        files["/etc/apache2/conf-enabled/sandbox-limits.conf"] = apache_limits
    return files, units


class ManagedServiceCompiler:
    @staticmethod
    def _extension_contract(value):
        """Return a detached, digest-bound extension contract for the guest.

        The package planner is the authority that creates this document.  The
        service compiler accepts only its JSON-compatible representation and
        rejects a caller-supplied package/source/build field before any service
        files are rendered.
        """
        if hasattr(value, "to_dict") and callable(value.to_dict):
            value = value.to_dict()
        if not isinstance(value, Mapping):
            raise ValueError("managed PHP extension contract is invalid")
        allowed = {"php_version", "profile", "requirements", "packages",
                   "catalog_digest", "digest"}
        if set(value) != allowed:
            raise ValueError("managed PHP extension contract is invalid")
        if (not isinstance(value.get("php_version"), str)
                or not isinstance(value.get("catalog_digest"), str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["catalog_digest"])
                or not isinstance(value.get("digest"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", value["digest"])):
            raise ValueError("managed PHP extension contract digest is invalid")
        requirements = value.get("requirements")
        packages = value.get("packages")
        if not isinstance(requirements, list) or not isinstance(packages, list):
            raise ValueError("managed PHP extension contract entries are invalid")
        safe_requirements = []
        for item in requirements:
            if (not isinstance(item, Mapping)
                    or set(item) - {"name", "state", "version"}
                    or not isinstance(item.get("name"), str)
                    or item.get("state") not in {"enabled", "disabled"}):
                raise ValueError("managed PHP extension requirement is invalid")
            safe_requirements.append({key: item[key] for key in ("name", "state", "version")
                                     if key in item})
        safe_packages = []
        for item in packages:
            if (not isinstance(item, Mapping)
                    or set(item) - {"name", "package", "package_version", "state",
                                    "version_constraint", "catalog_digest", "source"}
                    or item.get("state") != "enabled"
                    or item.get("source") != "official-distribution"):
                raise ValueError("managed PHP extension package evidence is invalid")
            safe_packages.append(dict(item))
        return {
            "php_version": value["php_version"], "profile": value.get("profile"),
            "requirements": safe_requirements, "packages": safe_packages,
            "catalog_digest": value["catalog_digest"], "digest": value["digest"],
        }

    def compile(self, policy, *, web_server="nginx", backend_port=8080,
                php_extensions=None, wp_cron_enabled=False):
        if web_server not in {"nginx", "apache"}: raise ValueError("managed web server is invalid")
        if type(wp_cron_enabled) is not bool:
            raise ValueError("managed WordPress cron setting is invalid")
        port = int(backend_port)
        if not 1024 <= port <= 65535: raise ValueError("managed backend port is invalid")
        guest = str(policy.network.get("guest_address", "")).split("/", 1)[0]
        if not guest: raise ValueError("managed guest address is unavailable")
        connections = int(policy.resources.get("connections", 128))
        runtime_seconds = int(policy.resources.get("runtime_seconds", 3600))
        files, units = compile_service_files(guest, connections, runtime_seconds,
                                             web_server=web_server,
                                             backend_port=port,
                                             wp_cron_enabled=wp_cron_enabled,
                                             writable_targets=tuple(item["target"]
                                                                    for item in getattr(
                                                                        policy, "writable_mounts", ())))
        extension_contract = None
        if php_extensions is not None:
            extension_contract = self._extension_contract(php_extensions)
            # This file is inside the managed rootfs and participates in the
            # service digest.  A package/requirement change therefore cannot
            # reuse a previously approved service plan, and the helper sees
            # only a deterministic catalog-derived document.
            files["/etc/sandbox-native/php-extensions.json"] = (
                json.dumps(extension_contract, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
        return {"machine_id": policy.machine_id, "policy_digest": policy.digest,
                "web_server": web_server, "backend": {"address": guest, "port": port},
                "wp_cron_enabled": wp_cron_enabled,
                "files": files, "file_digests": {path: hashlib.sha256(content.encode()).hexdigest()
                                                  for path, content in files.items()},
                "units": units, "digest": canonical_digest(files),
                **({"php_extensions_digest": extension_contract["digest"]}
                   if extension_contract is not None else {})}


class ManagedServiceSupervisor:
    """Run only fixed, digest-bound service lifecycle helper verbs.

    The helper owns execution inside the managed guest.  This control-plane
    interface never executes PHP, a web server, or a database command on the
    host, and deliberately discards helper output to avoid leaking runtime
    details or credentials.
    """

    _MACHINE = re.compile(r"^sb-[a-f0-9]{12,32}$")
    _DIGEST = re.compile(r"^[a-f0-9]{64}$")

    def __init__(self, *, process, helper):
        if not isinstance(helper, str) or not helper.startswith("/"):
            raise ValueError("managed service helper is invalid")
        self.process = process
        self.helper = helper

    @classmethod
    def _validate(cls, plan):
        if not isinstance(plan, Mapping):
            raise ValueError("managed service plan is invalid")
        machine_id = plan.get("machine_id")
        policy_digest = plan.get("policy_digest")
        service_digest = plan.get("digest")
        if not (isinstance(machine_id, str) and cls._MACHINE.fullmatch(machine_id)
                and isinstance(policy_digest, str) and cls._DIGEST.fullmatch(policy_digest)
                and isinstance(service_digest, str) and cls._DIGEST.fullmatch(service_digest)):
            raise ValueError("managed service identity is invalid")
        web_server = plan.get("web_server")
        if web_server not in {"nginx", "apache"}:
            raise ValueError("managed web server is invalid")
        wp_cron_enabled = plan.get("wp_cron_enabled", False)
        if not isinstance(wp_cron_enabled, bool):
            raise ValueError("managed WordPress cron setting is invalid")
        files = plan.get("files")
        file_digests = plan.get("file_digests")
        if not isinstance(files, Mapping) or not isinstance(file_digests, Mapping):
            raise ValueError("managed service files are invalid")
        calculated = {}
        executable_paths = {"/usr/local/libexec/sandbox-php-fpm",
                            "/usr/local/libexec/sandbox-wordpress-cron"}
        for path, content in files.items():
            if (not isinstance(path, str) or
                    not (path.startswith("/etc/") or path in executable_paths) or
                    not isinstance(content, str)):
                raise ValueError("managed service file is invalid")
            calculated[path] = hashlib.sha256(content.encode()).hexdigest()
        if dict(file_digests) != calculated or canonical_digest(dict(files)) != service_digest:
            raise ValueError("managed service plan digest changed")
        backend = plan.get("backend")
        if not isinstance(backend, Mapping) or set(backend) != {"address", "port"}:
            raise ValueError("managed backend is invalid")
        try:
            address = ipaddress.ip_address(backend["address"])
        except ValueError as exc:
            raise ValueError("managed backend address is invalid") from exc
        if address.version != 4 or not address.is_private:
            raise ValueError("managed backend must remain guest-private")
        if isinstance(backend["port"], bool) or not isinstance(backend["port"], int) \
                or not 1024 <= backend["port"] <= 65535:
            raise ValueError("managed backend port is invalid")
        expected_units = ("mariadb.service", "php8.3-fpm.service",
                          "nginx.service" if web_server == "nginx" else "apache2.service",
                          "cron.service")
        if tuple(plan.get("units", ())) != expected_units:
            raise ValueError("managed service units changed")
        cron_file = files.get("/etc/cron.d/sandbox-wordpress", "")
        if wp_cron_enabled and "*/5 * * * *" not in cron_file:
            raise ValueError("managed WordPress cron schedule is missing")
        if not wp_cron_enabled and "*/5 * * * *" in cron_file:
            raise ValueError("managed WordPress cron is unexpectedly enabled")

    def _run(self, verb, plan):
        self._validate(plan)
        result = self.process.run(
            ("sudo", "-n", self.helper, verb, plan["machine_id"],
             plan["policy_digest"], plan["digest"]),
            timeout=120,
        )
        return result.returncode == 0

    def backend_health(self, plan):
        ok = self._run("services-health", plan)
        return {"ok": ok, "state": "ready" if ok else "unhealthy", "mutated": False,
                "backend": dict(plan["backend"])}

    def activate(self, plan):
        if not self._run("services-activate", plan):
            return {"ok": False, "state": "activation_failed", "mutated": False}
        health = self.backend_health(plan)
        if health["ok"]:
            return {"ok": True, "state": "ready", "mutated": True,
                    "backend": health["backend"]}
        stopped = self.stop(plan)
        return {"ok": False, "state": "rollback_complete" if stopped["ok"] else "rollback_incomplete",
                "mutated": True, "health": health}

    def stop(self, plan):
        ok = self._run("services-stop", plan)
        return {"ok": ok, "state": "stopped" if ok else "stop_failed", "mutated": ok}

    def status(self, plan):
        ok = self._run("services-status", plan)
        return {"ok": ok, "state": "ready" if ok else "unhealthy", "mutated": False,
                "backend": dict(plan["backend"])}


class CredentialBrokerPlanCompiler:
    """Compile secret-free, digest-bound broker lifecycle metadata only."""

    PORT = 18443
    LIMITS = {"active_requests": 16, "frame_bytes": 65536,
              "credential_bytes": 65536, "deadline_seconds": 30}

    def __init__(self, *, broker_digest, executable_digest, config_digest):
        for value in (broker_digest, executable_digest, config_digest):
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError("credential broker reviewed digest is invalid")
        self.broker_digest = broker_digest
        self.executable_digest = executable_digest
        self.config_digest = config_digest

    def compile(self, policy, *, egress_digest):
        if (not isinstance(getattr(policy, "machine_id", None), str)
                or not re.fullmatch(r"sb-[a-f0-9]{12,32}", policy.machine_id)
                or not isinstance(getattr(policy, "digest", None), str)
                or not re.fullmatch(r"[a-f0-9]{64}", policy.digest)
                or not isinstance(egress_digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", egress_digest)):
            raise ValueError("credential broker plan identity is invalid")
        network = policy.network
        interface = network.get("veth")
        if not isinstance(interface, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", interface):
            raise ValueError("credential broker private-veth interface is invalid")
        try:
            host = ipaddress.ip_interface(network.get("host_address"))
            guest = ipaddress.ip_interface(network.get("guest_address"))
        except ValueError as exc:
            raise ValueError("credential broker private addresses are invalid") from exc
        usable = frozenset(host.network.hosts())
        if (host.version != 4 or guest.version != 4 or not host.ip.is_private
                or not guest.ip.is_private or host.network != guest.network or host.ip == guest.ip
                or host.ip not in usable or guest.ip not in usable):
            raise ValueError("credential broker private addresses are invalid")
        plan = {
            "machine_id": policy.machine_id,
            "policy_digest": policy.digest,
            "egress_digest": egress_digest,
            "broker_digest": self.broker_digest,
            "executable_digest": self.executable_digest,
            "config_digest": self.config_digest,
            "guest_interface": interface,
            "subnet": str(host.network),
            "host_address": str(host.ip),
            "guest_address": str(guest.ip),
            "guest_port": self.PORT,
            "limits": dict(self.LIMITS),
        }
        plan["digest"] = canonical_digest(plan)
        return plan


class CredentialBrokerSupervisor:
    """Fixed T036 helper lifecycle and ownership-enriched status envelope.

    The T035 broker's ``service_status`` has fewer identity fields. It cannot be
    passed here directly: the privileged helper must add and verify service UID,
    unit, cgroup, executable, and config identity before this parser accepts it.
    """

    _MACHINE = re.compile(r"^sb-[a-f0-9]{12,32}$")
    _DIGEST = re.compile(r"^[a-f0-9]{64}$")
    _IDENTITY = re.compile(r"^[A-Za-z0-9/][A-Za-z0-9._:@/-]{0,255}$")
    SERVICE_UID = CREDENTIAL_BROKER_SERVICE_UID
    _STATUS_KEYS = CREDENTIAL_BROKER_STATUS_FIELDS

    def __init__(self, *, process, helper, broker_digest, executable_digest, config_digest):
        if not isinstance(helper, str) or not helper.startswith("/"):
            raise ValueError("credential broker helper is invalid")
        self.process, self.helper = process, helper
        self.expected = {"broker_digest": broker_digest,
                         "executable_digest": executable_digest,
                         "config_digest": config_digest}
        if any(not isinstance(value, str) or not self._DIGEST.fullmatch(value)
               for value in self.expected.values()):
            raise ValueError("credential broker reviewed digest is invalid")

    @classmethod
    def _validate(cls, plan):
        if not isinstance(plan, Mapping) or set(plan) != {
                "machine_id", "policy_digest", "egress_digest", "broker_digest",
                "executable_digest", "config_digest", "guest_interface", "subnet",
                "host_address", "guest_address", "guest_port", "limits", "digest"}:
            raise ValueError("credential broker plan is invalid")
        if not cls._MACHINE.fullmatch(str(plan["machine_id"])):
            raise ValueError("credential broker machine identity is invalid")
        for name in ("policy_digest", "egress_digest", "broker_digest",
                     "executable_digest", "config_digest", "digest"):
            if not isinstance(plan[name], str) or not cls._DIGEST.fullmatch(plan[name]):
                raise ValueError("credential broker digest is invalid")
        if canonical_digest({key: value for key, value in plan.items() if key != "digest"}) != plan["digest"]:
            raise ValueError("credential broker plan digest changed")
        if (plan["guest_port"] != CredentialBrokerPlanCompiler.PORT
                or plan["limits"] != CredentialBrokerPlanCompiler.LIMITS):
            raise ValueError("credential broker fixed limits changed")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", str(plan["guest_interface"])):
            raise ValueError("credential broker interface changed")
        try:
            subnet = ipaddress.ip_network(plan["subnet"])
            host = ipaddress.ip_address(plan["host_address"])
            guest = ipaddress.ip_address(plan["guest_address"])
        except ValueError as exc:
            raise ValueError("credential broker addresses changed") from exc
        usable = frozenset(subnet.hosts())
        if (subnet.version != 4 or subnet.prefixlen != 30 or not subnet.is_private
                or host not in usable or guest not in usable or host == guest):
            raise ValueError("credential broker addresses changed")

    def _argv(self, verb, plan):
        self._validate(plan)
        if any(plan[name] != value for name, value in self.expected.items()):
            raise ValueError("credential broker reviewed identity changed")
        return ("sudo", "-n", self.helper, verb, plan["machine_id"],
                plan["policy_digest"], plan["egress_digest"], plan["broker_digest"])

    def _run(self, verb, plan):
        return self.process.run(self._argv(verb, plan), timeout=30)

    def start(self, plan):
        result = self._run("credential-broker-start", plan)
        return {"ok": result.returncode == 0, "state": "credential_pending",
                "mutated": result.returncode == 0}

    def stop(self, plan):
        result = self._run("credential-broker-stop", plan)
        return {"ok": result.returncode == 0, "state": "stopped", "mutated": result.returncode == 0}

    def status(self, plan):
        result = self._run("credential-broker-status", plan)
        if result.returncode != 0 or len((result.stdout or "").encode()) > 4096:
            return {"ok": False, "state": "unavailable", "mutated": False}
        try:
            value = json.loads(result.stdout or "")
        except (TypeError, json.JSONDecodeError):
            return {"ok": False, "state": "unavailable", "mutated": False}
        if isinstance(value, dict) and value.get("ok") is False:
            return {"ok": False, "state": "unavailable", "mutated": False}
        expected = {key: plan[key] for key in ("machine_id", "policy_digest", "egress_digest",
                                                "broker_digest", "executable_digest", "config_digest")}
        expected.update({"unit_identity": credential_broker_unit_identity(plan["machine_id"]),
                         "cgroup_identity": credential_broker_cgroup_identity(plan["machine_id"])})
        if (not isinstance(value, dict) or set(value) != self._STATUS_KEYS
                or any(value.get(key) != item for key, item in expected.items())
                or not isinstance(value.get("admission_open"), bool)
                or value.get("state") not in {"credential_pending", "ready", "draining",
                                               "closed", "blocked", "stopped"}
                or (value.get("state") == "ready") is not value.get("admission_open")
                or value.get("ok") is not True
                or not isinstance(value.get("broker_epoch"), str)
                or not self._IDENTITY.fullmatch(value["broker_epoch"])
                or isinstance(value.get("pid"), bool) or not isinstance(value.get("pid"), int)
                or value["pid"] < 1
                or isinstance(value.get("service_uid"), bool)
                or value.get("service_uid") != self.SERVICE_UID
                or not isinstance(value.get("process_start_identity"), str)
                or not re.fullmatch(r"[A-Za-z0-9/][A-Za-z0-9._:@/-]{0,255}",
                                    value["process_start_identity"])
                or not value["process_start_identity"].startswith(f"{value['pid']}:")
                or value["state"] == "stopped" and value["admission_open"] is not False):
            return {"ok": False, "state": "drifted", "mutated": False}
        return {**value, "mutated": False}
