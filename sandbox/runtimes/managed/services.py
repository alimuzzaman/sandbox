"""Render fixed nginx/Apache, PHP-FPM, MariaDB and cron service configuration."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import shlex
from collections.abc import Mapping

from sandbox.isolation.models import canonical_digest
from sandbox.isolation.bubblewrap import USERNS_FILTER_FD, userns_filtered_argv
from sandbox.isolation.seccomp import compile_userns_filter


PERSISTENT_WRITABLE_TARGETS = frozenset({
    "/var/www/html", "/var/lib/sandbox", "/var/log/sandbox", "/run/mysqld", "/run/php",
})


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
                          writable_targets=(), guest_machine=None):
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
               "    client_max_body_size 64m;\n"
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
             "*/5 * * * * root /usr/local/libexec/sandbox-wordpress-cron >/dev/null 2>&1\n",
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
            "RestrictNamespaces=yes\nProtectKernelTunables=yes\n"
        )
    if web_server == "nginx":
        files["/etc/nginx/nginx.conf"] = nginx
    else:
        files["/etc/apache2/conf-enabled/sandbox-limits.conf"] = apache_limits
    return files, units


class ManagedServiceCompiler:
    def compile(self, policy, *, web_server="nginx", backend_port=8080):
        if web_server not in {"nginx", "apache"}: raise ValueError("managed web server is invalid")
        port = int(backend_port)
        if not 1024 <= port <= 65535: raise ValueError("managed backend port is invalid")
        guest = str(policy.network.get("guest_address", "")).split("/", 1)[0]
        if not guest: raise ValueError("managed guest address is unavailable")
        connections = int(policy.resources.get("connections", 128))
        runtime_seconds = int(policy.resources.get("runtime_seconds", 3600))
        files, units = compile_service_files(guest, connections, runtime_seconds,
                                             web_server=web_server,
                                             backend_port=port,
                                             writable_targets=tuple(item["target"]
                                                                    for item in getattr(
                                                                        policy, "writable_mounts", ())))
        return {"machine_id": policy.machine_id, "policy_digest": policy.digest,
                "web_server": web_server, "backend": {"address": guest, "port": port},
                "files": files, "file_digests": {path: hashlib.sha256(content.encode()).hexdigest()
                                                  for path, content in files.items()},
                "units": units, "digest": canonical_digest(files)}


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
