import json
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
    def test_v2_lifecycle_executor_pins_plans_and_fixed_secret_free_argv(self):
        from sandbox.isolation.credential_controller_lifecycle_v2 import (
            DerivedServiceConfigV2, LifecycleV2Error,
        )
        from sandbox.runtimes.managed.services import NativeCredentialLifecycleExecutorV2
        from tests.test_credential_controller_lifecycle_v2 import document

        controller = DerivedServiceConfigV2.derive(document("controller"))
        broker = DerivedServiceConfigV2.derive(document("broker"))

        class LifecycleProcess:
            def __init__(self): self.calls = []
            def run(self, argv, **kwargs):
                self.calls.append((argv, kwargs))
                return type("Result", (), {
                    "returncode": 0,
                    "stdout": json.dumps({"ok": True, "code": "completed"}),
                })()

        process = LifecycleProcess()
        executor = NativeCredentialLifecycleExecutorV2(
            process=process, helper="/fixed/native-helper",
            controller=controller, broker=broker,
        )
        self.assertEqual(executor.execute(
            "credential-controller-configure-v2", controller),
            {"ok": True, "code": "completed"})
        argv, kwargs = process.calls[0]
        self.assertEqual(argv, (
            "sudo", "-n", "/fixed/native-helper",
            "credential-controller-configure-v2", controller.machine_id,
            controller.config_digest, executor.plan_identity,
        ))
        self.assertEqual(kwargs, {"timeout": 30})
        self.assertNotIn(controller.canonical_bytes.decode(), repr(argv))
        with self.assertRaisesRegex(LifecycleV2Error, "lifecycle_action_invalid"):
            executor.execute("credential-broker-start-v2", controller)
        forged = DerivedServiceConfigV2.derive(document("controller"))
        with self.assertRaisesRegex(LifecycleV2Error, "lifecycle_plan_invalid"):
            executor.execute("credential-controller-start-v2", forged)
        self.assertEqual(len(process.calls), 1)

    def test_v2_lifecycle_executor_requires_exact_ownership_absence_status(self):
        from sandbox.isolation.credential_controller_lifecycle_v2 import (
            DerivedServiceConfigV2, LifecycleV2Error,
        )
        from sandbox.runtimes.managed.services import NativeCredentialLifecycleExecutorV2
        from tests.test_credential_controller_lifecycle_v2 import document

        controller = DerivedServiceConfigV2.derive(document("controller"))
        broker = DerivedServiceConfigV2.derive(document("broker"))

        class LifecycleProcess:
            value = None
            def run(self, _argv, **_kwargs):
                return type("Result", (), {
                    "returncode": 0, "stdout": json.dumps(self.value),
                })()

        process = LifecycleProcess()
        executor = NativeCredentialLifecycleExecutorV2(
            process=process, helper="/fixed/native-helper",
            controller=controller, broker=broker,
        )
        process.value = {
            "ok": True, "code": "completed", "machine_id": broker.machine_id,
            "component": "broker", "config_digest": broker.config_digest,
            "plan_identity": executor.plan_identity,
            "unit_identity": broker.document["unit_identity"],
            "unit_digest": broker.document["unit_digest"],
            "service_uid": broker.document["service_uid"],
            "service_gid": broker.document["service_gid"],
            "executable_digest": broker.document["executable_digest"],
            "process_identity_authority": broker.document["process_identity_authority"],
            "observed": True, "owned": True, "unit_absent": True,
            "process_absent": True, "socket_absent": True,
            "cgroup_absent": True, "descriptor_absent": True,
        }
        self.assertTrue(all(executor.observe_absence(broker).values()))
        process.value["config_digest"] = "0" * 64
        with self.assertRaisesRegex(LifecycleV2Error, "observation_unavailable"):
            executor.observe_absence(broker)

    def test_v2_lifecycle_executor_maps_helper_refusal_to_bounded_closed_result(self):
        from sandbox.isolation.credential_controller_lifecycle_v2 import DerivedServiceConfigV2
        from sandbox.runtimes.managed.services import NativeCredentialLifecycleExecutorV2
        from tests.test_credential_controller_lifecycle_v2 import document

        controller = DerivedServiceConfigV2.derive(document("controller"))
        broker = DerivedServiceConfigV2.derive(document("broker"))

        class RefusingProcess:
            def run(self, _argv, **_kwargs):
                return type("Result", (), {"returncode": 69, "stdout": "secret=no"})()

        executor = NativeCredentialLifecycleExecutorV2(
            process=RefusingProcess(), helper="/fixed/native-helper",
            controller=controller, broker=broker,
        )
        self.assertEqual(executor.execute(
            "credential-controller-start-v2", controller),
            {"ok": False, "code": "lifecycle_unavailable"})

    def test_v2_lifecycle_executor_rejects_duplicate_secret_and_surrogate_output(self):
        from sandbox.isolation.credential_controller_lifecycle_v2 import DerivedServiceConfigV2
        from sandbox.runtimes.managed.services import NativeCredentialLifecycleExecutorV2
        from tests.test_credential_controller_lifecycle_v2 import document

        controller = DerivedServiceConfigV2.derive(document("controller"))
        broker = DerivedServiceConfigV2.derive(document("broker"))

        class OutputProcess:
            stdout = ""
            def run(self, _argv, **_kwargs):
                return type("Result", (), {"returncode": 0, "stdout": self.stdout})()

        process = OutputProcess()
        executor = NativeCredentialLifecycleExecutorV2(
            process=process, helper="/fixed/native-helper",
            controller=controller, broker=broker,
        )
        for value in (
            '{"ok":true,"code":"completed","ok":false}',
            '{"ok":true,"code":"completed","source_ref":"opaque"}',
            '[{"ok":true,"code":"completed"}]',
            '{"ok":true,"code":"completed"',
            '\ud800',
        ):
            with self.subTest(stdout=repr(value)):
                process.stdout = value
                self.assertEqual(executor.execute(
                    "credential-controller-start-v2", controller),
                    {"ok": False, "code": "lifecycle_unavailable"})

    def test_v2_lifecycle_executor_bounds_hostile_result_properties(self):
        from sandbox.isolation.credential_controller_lifecycle_v2 import DerivedServiceConfigV2
        from sandbox.runtimes.managed.services import NativeCredentialLifecycleExecutorV2
        from tests.test_credential_controller_lifecycle_v2 import document

        controller = DerivedServiceConfigV2.derive(document("controller"))
        broker = DerivedServiceConfigV2.derive(document("broker"))

        class HostileResult:
            def __init__(self, selected): self.selected = selected
            @property
            def returncode(self):
                if self.selected == "returncode": raise RuntimeError("raw secret")
                return 0
            @property
            def stdout(self): raise UnicodeError("raw secret")

        class Process:
            selected = "returncode"
            def run(self, *_args, **_kwargs): return HostileResult(self.selected)

        process = Process()
        executor = NativeCredentialLifecycleExecutorV2(
            process=process, helper="/fixed/native-helper",
            controller=controller, broker=broker)
        for selected in ("returncode", "stdout"):
            process.selected = selected
            self.assertEqual(executor.execute(
                "credential-controller-start-v2", controller),
                {"ok": False, "code": "lifecycle_unavailable"})

    def test_v2_lifecycle_executor_rejects_contradictory_ownership_status(self):
        from sandbox.isolation.credential_controller_lifecycle_v2 import (
            DerivedServiceConfigV2, LifecycleV2Error,
        )
        from sandbox.runtimes.managed.services import NativeCredentialLifecycleExecutorV2
        from tests.test_credential_controller_lifecycle_v2 import document

        controller = DerivedServiceConfigV2.derive(document("controller"))
        broker = DerivedServiceConfigV2.derive(document("broker"))

        class OutputProcess:
            value = None
            def run(self, _argv, **_kwargs):
                return type("Result", (), {
                    "returncode": 0, "stdout": json.dumps(self.value),
                })()

        process = OutputProcess()
        executor = NativeCredentialLifecycleExecutorV2(
            process=process, helper="/fixed/native-helper",
            controller=controller, broker=broker,
        )
        base = {
            "ok": True, "code": "completed", "machine_id": broker.machine_id,
            "component": "broker", "config_digest": broker.config_digest,
            "plan_identity": executor.plan_identity,
            "unit_identity": broker.document["unit_identity"],
            "unit_digest": broker.document["unit_digest"],
            "service_uid": broker.document["service_uid"],
            "service_gid": broker.document["service_gid"],
            "executable_digest": broker.document["executable_digest"],
            "process_identity_authority": broker.document["process_identity_authority"],
            "observed": True, "owned": True, "unit_absent": True,
            "process_absent": True, "socket_absent": True,
            "cgroup_absent": True, "descriptor_absent": True,
        }
        for mutation in ({"observed": False}, {"owned": False}):
            with self.subTest(mutation=mutation):
                process.value = {**base, **mutation}
                with self.assertRaisesRegex(
                        LifecycleV2Error, "observation_unavailable"):
                    executor.observe_absence(broker)

    def test_extension_contract_is_rendered_inside_digest_bound_service_files(self):
        from sandbox.php_extensions.catalog import DEFAULT_CATALOG
        from sandbox.runtimes.managed.models import ManagedPhpExtensionPlan, PhpExtensionPackage
        from sandbox.runtimes.managed.services import ManagedServiceCompiler

        extension = PhpExtensionPackage(
            "gd", "php8.3-gd", "8.3.6", catalog_digest=DEFAULT_CATALOG.digest,
        )
        plan = ManagedPhpExtensionPlan(
            "8.3", None, ({"name": "gd", "state": "enabled", "version": None},),
            (extension,), DEFAULT_CATALOG.digest,
        )
        result = ManagedServiceCompiler().compile(Policy(), php_extensions=plan)
        document = result["files"]["/etc/sandbox-native/php-extensions.json"]
        self.assertIn(plan.digest, document)
        self.assertEqual(result["php_extensions_digest"], plan.digest)
        self.assertEqual(result["file_digests"]["/etc/sandbox-native/php-extensions.json"],
                         __import__("hashlib").sha256(document.encode()).hexdigest())

    def test_nginx_stack_is_wholly_inside_guest_and_database_has_no_network(self):
        from sandbox.runtimes.managed.services import ManagedServiceCompiler
        result = ManagedServiceCompiler().compile(Policy(), web_server="nginx")
        web = result["files"]["/etc/nginx/sites-enabled/sandbox.conf"]
        database = result["files"]["/etc/mysql/mariadb.conf.d/90-sandbox.cnf"]
        self.assertIn("listen 10.203.0.2:8080", web)
        self.assertIn("root /var/www/html", web)
        self.assertNotIn("root /workspace", web)
        self.assertIn("fastcgi_pass unix:/run/php/sandbox.sock", web)
        self.assertIn("client_max_body_size 1024m;", web)
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
        php_pool = result["files"]["/etc/php/8.3/fpm/pool.d/sandbox.conf"]
        self.assertIn("request_terminate_timeout = 900s", php_pool)
        self.assertIn("php_admin_value[upload_max_filesize] = 1024M", php_pool)
        self.assertIn("php_admin_value[post_max_size] = 1024M", php_pool)
        self.assertIn("/usr/bin/timeout --signal=TERM --kill-after=5s 900s", cron)
        self.assertIn("/usr/local/bin/wp cron event run", cron)
        self.assertIn("WordPress cron disabled by Sandbox",
                      result["files"]["/etc/cron.d/sandbox-wordpress"])
        self.assertEqual(
            result["files"]["/etc/systemd/system/php8.3-fpm.service.d/sandbox-isolation.conf"],
            "[Service]\nType=simple\nExecStartPre=/usr/bin/install -d -o root -g root -m 0755 /run/php\nExecStart=\nExecStart=/usr/local/libexec/sandbox-php-fpm\n",
        )

    def test_wordpress_cron_requires_explicit_opt_in(self):
        from sandbox.runtimes.managed.services import ManagedServiceCompiler
        result = ManagedServiceCompiler().compile(Policy(), wp_cron_enabled=True)
        self.assertIn("root /usr/local/libexec/sandbox-wordpress-cron",
                      result["files"]["/etc/cron.d/sandbox-wordpress"])
        self.assertTrue(result["wp_cron_enabled"])

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
