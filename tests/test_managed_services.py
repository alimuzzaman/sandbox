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


class BrokerPolicy(Policy):
    network = {"guest_address": "10.203.0.2/30", "host_address": "10.203.0.1/30",
               "veth": "sbv0123456789"}


class BrokerProcess:
    """A helper stub that answers with a bounded, non-secret status document."""

    def __init__(self, *, returncode=0, stdout=None):
        self.returncode = returncode
        self.stdout = stdout
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        return type("Result", (), {"returncode": self.returncode,
                                   "stdout": self.stdout or ""})()


def broker_plan(**overrides):
    from sandbox.runtimes.managed.services import CredentialBrokerPlanCompiler
    plan = CredentialBrokerPlanCompiler().compile(
        BrokerPolicy(), egress_digest="b" * 64, broker_digest="c" * 64,
        service_uid=4321, guest_port=18443, broker_epoch="epoch-1",
    )
    plan.update(overrides)
    return plan


def broker_status_document(plan, **overrides):
    document = {"machine_id": plan["machine_id"], "policy_digest": plan["policy_digest"],
                "egress_digest": plan["egress_digest"],
                "broker_digest": plan["broker_digest"], "state": "absent",
                "admission_open": False, "unit": plan["unit"]}
    document.update(overrides)
    return __import__("json").dumps(document)


class TestManagedServices(unittest.TestCase):
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


class TestCredentialBrokerService(unittest.TestCase):
    """Spec 045 T036: local plan/verb contracts only, never live proof."""

    def test_plan_is_digest_bound_closed_and_carries_no_secret_field(self):
        plan = broker_plan()
        self.assertEqual(plan["unit"], "sandbox-credential-broker@sb-0123456789ab.service")
        self.assertEqual(plan["executable"],
                         "/usr/libexec/sandbox/native-credential-broker")
        self.assertEqual(plan["state"], "credential_pending")
        self.assertFalse(plan["admission_open"])
        self.assertEqual(plan["host_address"], "10.203.0.1")
        self.assertEqual(plan["guest_address"], "10.203.0.2")
        # Only fixed identities may appear; nothing that could carry a value.
        for name in ("source_reference", "secret", "token", "authorization",
                     "password", "api_key", "lease"):
            self.assertNotIn(name, repr(plan).lower())
        # The digest covers the identity document, not the closed state fields.
        from sandbox.isolation.models import canonical_digest
        document = {key: value for key, value in plan.items()
                    if key not in {"state", "admission_open", "digest"}}
        self.assertEqual(plan["digest"], canonical_digest(document))

    def test_plan_refuses_public_transport_and_invalid_identity(self):
        from sandbox.runtimes.managed.services import CredentialBrokerPlanCompiler

        class PublicPolicy(BrokerPolicy):
            network = {"guest_address": "8.8.8.8/30", "host_address": "8.8.4.4/30",
                       "veth": "sbv0123456789"}

        compiler = CredentialBrokerPlanCompiler()
        with self.assertRaises(ValueError):
            compiler.compile(PublicPolicy(), egress_digest="b" * 64, broker_digest="c" * 64,
                             service_uid=4321, guest_port=18443, broker_epoch="epoch-1")
        with self.assertRaises(ValueError):
            compiler.compile(BrokerPolicy(), egress_digest="not-a-digest",
                             broker_digest="c" * 64, service_uid=4321,
                             guest_port=18443, broker_epoch="epoch-1")
        with self.assertRaises(ValueError):
            compiler.compile(BrokerPolicy(), egress_digest="b" * 64, broker_digest="c" * 64,
                             service_uid=0, guest_port=18443, broker_epoch="epoch-1")

    def test_supervisor_uses_only_fixed_digest_bound_verbs(self):
        from sandbox.runtimes.managed.services import CredentialBrokerSupervisor
        plan = broker_plan()
        process = BrokerProcess(stdout=broker_status_document(plan))
        supervisor = CredentialBrokerSupervisor(process=process, helper="/fixed/native-helper")
        supervisor.status(plan)
        supervisor.stop(plan)
        self.assertEqual([call[0][3] for call in process.calls],
                         ["credential-broker-status", "credential-broker-stop"])
        for argv, kwargs in process.calls:
            self.assertEqual(argv[:3], ("sudo", "-n", "/fixed/native-helper"))
            self.assertEqual(argv[4:], (plan["machine_id"], plan["policy_digest"],
                                        plan["egress_digest"], plan["broker_digest"]))
            self.assertEqual(len(argv), 8)
            self.assertEqual(kwargs["timeout"], 120)
            self.assertNotIn(plan["unit"], argv)
            self.assertNotIn(plan["executable"], argv)

    def test_start_never_reports_open_admission(self):
        from sandbox.runtimes.managed.services import CredentialBrokerSupervisor
        plan = broker_plan()
        for stdout, returncode in (
            (broker_status_document(plan, state="credential_pending"), 0),
            (broker_status_document(plan, state="ready", admission_open=True), 0),
            ("", 69),
        ):
            with self.subTest(returncode=returncode):
                supervisor = CredentialBrokerSupervisor(
                    process=BrokerProcess(returncode=returncode, stdout=stdout),
                    helper="/fixed/native-helper")
                result = supervisor.start(plan)
                self.assertFalse(result["admission_open"])
                self.assertFalse(result["mutated"])

    def test_status_refuses_unbounded_foreign_or_open_helper_output(self):
        from sandbox.runtimes.managed.services import CredentialBrokerSupervisor
        plan = broker_plan()
        for stdout in (
            "x" * 5000,
            "not json",
            broker_status_document(plan, machine_id="sb-ffffffffffff"),
            broker_status_document(plan, admission_open=True),
            broker_status_document(plan, state="wat"),
            __import__("json").dumps({"state": "absent", "leaked": "value"}),
        ):
            with self.subTest(stdout=stdout[:24]):
                supervisor = CredentialBrokerSupervisor(
                    process=BrokerProcess(stdout=stdout), helper="/fixed/native-helper")
                result = supervisor.status(plan)
                self.assertFalse(result["ok"])
                self.assertEqual(result["state"], "unavailable")
                self.assertIsNone(supervisor.observe(plan))

    def test_observation_reports_presence_only_from_a_read_that_answered(self):
        from sandbox.runtimes.managed.services import CredentialBrokerSupervisor
        plan = broker_plan()
        for state, expected in (("absent", "absent"), ("present", "present")):
            supervisor = CredentialBrokerSupervisor(
                process=BrokerProcess(stdout=broker_status_document(plan, state=state)),
                helper="/fixed/native-helper")
            self.assertEqual(supervisor.observe(plan), {
                "machine_id": plan["machine_id"], "policy_digest": plan["policy_digest"],
                "resource": "credential_broker", "resource_digest": plan["broker_digest"],
                "state": expected,
            })
        for state in ("drifted", "unavailable"):
            supervisor = CredentialBrokerSupervisor(
                process=BrokerProcess(stdout=broker_status_document(plan, state=state)),
                helper="/fixed/native-helper")
            self.assertIsNone(supervisor.observe(plan))

    def test_tampered_plan_is_refused_before_any_helper_invocation(self):
        from sandbox.runtimes.managed.services import CredentialBrokerSupervisor
        process = BrokerProcess()
        supervisor = CredentialBrokerSupervisor(process=process, helper="/fixed/native-helper")
        for plan in (
            broker_plan(broker_digest="d" * 64),
            broker_plan(admission_open=True),
            broker_plan(unit="sandbox-credential-broker@other.service"),
            broker_plan(executable="/usr/bin/anything"),
        ):
            with self.subTest(plan=plan["unit"]), self.assertRaises(ValueError):
                supervisor.start(plan)
        self.assertEqual(process.calls, [])


if __name__ == "__main__": unittest.main()
