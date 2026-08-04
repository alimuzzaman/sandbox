import unittest


class Process:
    def __init__(self, *, returncode=0):
        self.returncode = returncode
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        return type("Result", (), {"returncode": self.returncode, "stdout": "secret=never"})()


class Policy:
    machine_id = "sb-0123456789ab"; digest = "a" * 64
    network = {"guest_address": "10.203.0.2/30"}
    resources = {"connections": 128, "runtime_seconds": 900}


class TestManagedServices(unittest.TestCase):
    def test_nginx_stack_is_wholly_inside_guest_and_database_has_no_network(self):
        from sandbox.runtimes.managed.services import ManagedServiceCompiler
        result = ManagedServiceCompiler().compile(Policy(), web_server="nginx")
        web = result["files"]["/etc/nginx/sites-enabled/sandbox.conf"]
        database = result["files"]["/etc/mysql/mariadb.conf.d/90-sandbox.cnf"]
        self.assertIn("listen 10.203.0.2:8080", web)
        self.assertIn("root /var/www/html", web)
        self.assertNotIn("root /workspace", web)
        self.assertIn("fastcgi_pass unix:/run/php/sandbox.sock", web)
        nginx = result["files"]["/etc/nginx/nginx.conf"]
        self.assertIn("worker_processes 1", nginx)
        self.assertIn("worker_connections 128", nginx)
        self.assertIn("skip-networking=1", database)
        self.assertIn("mariadb.service", result["units"])
        php = result["files"]["/usr/local/libexec/sandbox-php-fpm"]
        cron = result["files"]["/usr/local/libexec/sandbox-wordpress-cron"]
        for launcher in (php, cron):
            self.assertIn("/usr/bin/bwrap", launcher)
            # `--disable-userns` and `--unshare-pid` are deliberately absent: both
            # fail inside a machine, the first because it writes read-only
            # /proc/sys and the second because a fresh procfs needs a fully
            # visible /proc that nspawn masks (FR-045, FR-046).
            self.assertIn("--unshare-user --unshare-ipc --unshare-uts --unshare-cgroup",
                          launcher)
            self.assertNotIn("--disable-userns", launcher)
            self.assertNotIn("--unshare-pid", launcher)
            self.assertIn("--tmpfs /run/systemd", launcher)
            self.assertIn("--tmpfs /run/sandbox-native-credentials", launcher)
            self.assertIn("--cap-drop ALL --uid 33 --gid 33", launcher)
        self.assertIn("/usr/sbin/php-fpm8.3", php)
        self.assertIn("request_terminate_timeout = 900s",
                      result["files"]["/etc/php/8.3/fpm/pool.d/sandbox.conf"])
        self.assertIn("/usr/bin/timeout --signal=TERM --kill-after=5s 900s", cron)
        self.assertIn("/usr/local/bin/wp cron event run", cron)
        self.assertIn("root /usr/local/libexec/sandbox-wordpress-cron",
                      result["files"]["/etc/cron.d/sandbox-wordpress"])
        self.assertEqual(
            result["files"]["/etc/systemd/system/php8.3-fpm.service.d/sandbox-isolation.conf"],
            "[Service]\nType=simple\nNoNewPrivileges=yes\nExecStartPre=/usr/bin/install -d -o www-data -g www-data -m 0770 /run/php\nExecStart=\nExecStart=/usr/local/libexec/sandbox-php-fpm\n",
        )

    def test_apache_variant_uses_same_php_database_and_backend_contract(self):
        from sandbox.runtimes.managed.services import ManagedServiceCompiler
        result = ManagedServiceCompiler().compile(Policy(), web_server="apache")
        web = result["files"]["/etc/apache2/sites-enabled/000-sandbox.conf"]
        self.assertIn("VirtualHost 10.203.0.2:8080", web)
        self.assertIn("DocumentRoot /var/www/html", web)
        self.assertIn("proxy:unix:/run/php/sandbox.sock", web)
        limits = result["files"]["/etc/apache2/conf-enabled/sandbox-limits.conf"]
        self.assertIn("KeepAlive Off", limits)
        self.assertIn("ServerLimit 128", limits)
        self.assertIn("MaxRequestWorkers 128", limits)
        self.assertIn("apache2.service", result["units"])
        self.assertNotIn("nginx.service", result["units"])

    def test_supervisor_uses_only_digest_bound_helper_verbs_and_redacts_output(self):
        from sandbox.runtimes.managed.services import ManagedServiceCompiler, ManagedServiceSupervisor
        process = Process()
        plan = ManagedServiceCompiler().compile(Policy())
        supervisor = ManagedServiceSupervisor(process=process, helper="/fixed/native-helper")
        activated = supervisor.activate(plan)
        status = supervisor.status(plan)
        health = supervisor.backend_health(plan)
        stopped = supervisor.stop(plan)
        self.assertTrue(activated["ok"]); self.assertTrue(status["ok"])
        self.assertTrue(health["ok"]); self.assertTrue(stopped["ok"])
        self.assertEqual([call[0][3] for call in process.calls], [
            "services-activate", "services-health", "services-status",
            "services-health", "services-stop",
        ])
        for argv, kwargs in process.calls:
            self.assertEqual(argv[:3], ("sudo", "-n", "/fixed/native-helper"))
            self.assertEqual(argv[-3:], (Policy.machine_id, Policy.digest, plan["digest"]))
            self.assertNotIn("secret=never", repr(argv))
            self.assertNotIn("php", argv)
            self.assertEqual(kwargs["timeout"], 120)

    def test_supervisor_rejects_tampered_service_file_before_helper_invocation(self):
        from sandbox.runtimes.managed.services import ManagedServiceCompiler, ManagedServiceSupervisor
        process = Process(); plan = ManagedServiceCompiler().compile(Policy())
        plan["files"]["/etc/cron.d/sandbox-wordpress"] += "# tampered\n"
        with self.assertRaises(ValueError):
            ManagedServiceSupervisor(process=process, helper="/fixed/native-helper").activate(plan)
        self.assertEqual(process.calls, [])


if __name__ == "__main__": unittest.main()
