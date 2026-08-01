"""Render fixed nginx/Apache, PHP-FPM, MariaDB and cron service configuration."""

from __future__ import annotations

import hashlib

from sandbox.isolation.models import canonical_digest


class ManagedServiceCompiler:
    def compile(self, policy, *, web_server="nginx", backend_port=8080):
        if web_server not in {"nginx", "apache"}: raise ValueError("managed web server is invalid")
        port = int(backend_port)
        if not 1024 <= port <= 65535: raise ValueError("managed backend port is invalid")
        guest = str(policy.network.get("guest_address", "")).split("/", 1)[0]
        if not guest: raise ValueError("managed guest address is unavailable")
        common_php = (
            "[sandbox]\nuser = www-data\ngroup = www-data\n"
            "listen = /run/php/sandbox.sock\nlisten.owner = www-data\nlisten.group = www-data\n"
            "pm = dynamic\npm.max_children = 16\npm.start_servers = 2\n"
            "pm.min_spare_servers = 1\npm.max_spare_servers = 4\n"
            "php_admin_value[upload_tmp_dir] = /var/lib/sandbox/tmp\n"
            "php_admin_value[session.save_path] = /var/lib/sandbox/sessions\n"
        )
        if web_server == "nginx":
            web_path = "/etc/nginx/sites-enabled/sandbox.conf"
            web = (f"server {{\n    listen {guest}:{port} default_server;\n"
                   "    server_name _;\n    root /workspace;\n    index index.php;\n"
                   "    location / { try_files $uri $uri/ /index.php?$args; }\n"
                   "    location ~ \\.php$ { include fastcgi_params; "
                   "fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name; "
                   "fastcgi_pass unix:/run/php/sandbox.sock; }\n}\n")
            units = ("mariadb.service", "php8.3-fpm.service", "nginx.service", "cron.service")
        else:
            web_path = "/etc/apache2/sites-enabled/000-sandbox.conf"
            web = (f"Listen {guest}:{port}\n<VirtualHost {guest}:{port}>\n"
                   "    DocumentRoot /workspace\n    DirectoryIndex index.php\n"
                   "    <Directory /workspace>\n        AllowOverride All\n        Require all granted\n"
                   "    </Directory>\n"
                   "    <FilesMatch \\.php$>\n        SetHandler \"proxy:unix:/run/php/sandbox.sock|fcgi://localhost/\"\n"
                   "    </FilesMatch>\n</VirtualHost>\n")
            units = ("mariadb.service", "php8.3-fpm.service", "apache2.service", "cron.service")
        database = ("[mysqld]\nbind-address=127.0.0.1\nskip-networking=1\n"
                    "socket=/run/mysqld/mysqld.sock\nmax_connections=128\n")
        files = {web_path: web, "/etc/php/8.3/fpm/pool.d/sandbox.conf": common_php,
                 "/etc/mysql/mariadb.conf.d/90-sandbox.cnf": database,
                 "/etc/cron.d/sandbox-wordpress":
                 "*/5 * * * * www-data /usr/local/bin/wp cron event run --due-now --path=/workspace >/dev/null 2>&1\n"}
        return {"machine_id": policy.machine_id, "policy_digest": policy.digest,
                "web_server": web_server, "backend": {"address": guest, "port": port},
                "files": files, "file_digests": {path: hashlib.sha256(content.encode()).hexdigest()
                                                  for path, content in files.items()},
                "units": units, "digest": canonical_digest(files)}
