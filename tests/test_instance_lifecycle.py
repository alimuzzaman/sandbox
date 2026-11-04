import unittest
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class TestInstanceLifecycleConfig(unittest.TestCase):
    def test_instance_running_bounds_unavailable_compose_probe(self):
        from sandbox.core import _instances

        with mock.patch.object(
                _instances, 'compose',
                side_effect=subprocess.TimeoutExpired(['docker', 'compose'], 5)) as compose:
            self.assertFalse(_instances._instance_running('fixture'))
        self.assertEqual(compose.call_args.kwargs['timeout'], 5.0)

    def test_inventory_site_url_skips_network_probe(self):
        from sandbox.core import _domains

        cfg = {
            'domain': 'fixture.tst',
            'wordpress_port': 8999,
            'url': 'https://fixture.tst',
        }
        with mock.patch.object(
                _domains, '_sandbox_proxy_active',
                side_effect=AssertionError('inventory must not probe')):
            self.assertEqual(_domains.site_url(cfg, probe=False),
                             'http://localhost:8999')

    def test_wordpress_wake_reads_new_instance_and_probes_backend(self):
        from sandbox.application import context
        from sandbox.runtimes.base import OperationRequest
        import sandbox.core as core
        import sandbox_core as registry

        current = {'instances': {'new-site': {'wordpress_port': 8999}}}
        with mock.patch.object(registry, 'registry_get', return_value={
                'instance': 'new-site', 'root': '/fixture'}), \
             mock.patch.object(registry, 'registry_all', return_value={'/fixture': {
                 'instance': 'new-site', 'root': '/fixture', 'wordpress_port': 8998}}), \
             mock.patch.object(core, 'compose', return_value=SimpleNamespace(
                 returncode=0, stdout='', stderr='')), \
             mock.patch.object(core, '_wait_reachable', return_value=True) as wait, \
             mock.patch.object(registry, 'registry_put'), \
             mock.patch.object(core, 'load_config', return_value=current):
            service = context.runtime_service({'instances': {}})
            result = service._adapters.for_kind('wordpress').adapter._operations['resume'](
                OperationRequest('/fixture', 'resume'))
        self.assertTrue(result['ok'])
        self.assertEqual(wait.call_args.args[0]['wordpress_port'], 8999)
        self.assertTrue(wait.call_args.kwargs['backend_only'])

    def test_backend_readiness_never_enters_the_canonical_route(self):
        from sandbox.core import _instances
        response = mock.Mock(status=200)
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(_instances, 'site_url', side_effect=AssertionError('recursive route')), \
             mock.patch('urllib.request.build_opener', return_value=opener):
            self.assertTrue(_instances._wait_reachable(
                {'wordpress_port': 8999, 'domain': 'fixture.tst'}, timeout=1, backend_only=True))
        self.assertEqual(opener.open.call_args.args[0], 'http://localhost:8999')
        response.close.assert_called_once()

    def test_omission_resolves_to_idle_stop_and_request_wake(self):
        from sandbox.config.instance_lifecycle import normalize_instance_lifecycle

        self.assertEqual(normalize_instance_lifecycle(), {
            "mode": "idle_stop",
            "wakeOnRequest": True,
            "idleAfterSeconds": 1800,
            "wakeTimeoutSeconds": 60,
            "stopGraceSeconds": 30,
            "maxPendingRequests": 32,
        })

    def test_common_manifest_materializes_default_and_allows_explicit_opt_out(self):
        from sandbox.config.manifest import apply_common_config

        resolved = apply_common_config({})
        self.assertEqual(resolved["instanceLifecycle"]["mode"], "idle_stop")
        self.assertIs(resolved["instanceLifecycle"]["wakeOnRequest"], True)
        self.assertEqual(resolved["instanceLifecycle"]["idleAfterSeconds"], 1800)
        opted_out = apply_common_config({"instanceLifecycle": {"mode": "always_on"}})
        self.assertEqual(opted_out["instanceLifecycle"]["mode"], "always_on")

    def test_idle_stop_is_strict_and_detached(self):
        from sandbox.config.instance_lifecycle import normalize_instance_lifecycle

        raw = {"mode": "idle_stop", "idleAfterSeconds": 300}
        normalized = normalize_instance_lifecycle(raw)
        raw["idleAfterSeconds"] = 60
        self.assertEqual(normalized["mode"], "idle_stop")
        self.assertEqual(normalized["idleAfterSeconds"], 300)

    def test_invalid_values_fail_closed(self):
        from sandbox.config.instance_lifecycle import InstanceLifecycleConfigError, normalize_instance_lifecycle

        for raw in (
            {"mode": "provider_native"},
            {"mode": "idle_stop", "idleAfterSeconds": 1},
            {"maxPendingRequests": True},
            {"unknown": 1},
            {"wakeOnRequest": 1},
        ):
            with self.subTest(raw=raw), self.assertRaises(InstanceLifecycleConfigError):
                normalize_instance_lifecycle(raw)

    def test_explicit_null_is_not_treated_as_omission(self):
        from sandbox.config.manifest import apply_common_config

        with self.assertRaisesRegex(ValueError, "instanceLifecycle must be an object"):
            apply_common_config({"instanceLifecycle": None})

    def test_compose_descriptor_carries_policy_through_common_manifest(self):
        from sandbox.config.compose import ComposeSchemaProvider
        from sandbox.config.manifest import apply_common_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose",
                "instanceLifecycle": {"mode": "idle_stop", "idleAfterSeconds": 300},
                "compose": {
                    "file": "compose.yaml", "service": "web", "internal_port": 80,
                    "health_path": "/healthz",
                },
            }))
            descriptor = ComposeSchemaProvider().resolve(root)
            resolved = apply_common_config(descriptor)
            self.assertEqual(resolved["instanceLifecycle"]["mode"], "idle_stop")
            self.assertEqual(resolved["instanceLifecycle"]["idleAfterSeconds"], 300)

    def test_wordpress_instance_resolution_preserves_normalized_policy(self):
        import sandbox.core._instances as instances

        cfg = {"instances": {"site-a": {
            "instance_lifecycle": {"mode": "idle_stop", "idleAfterSeconds": 300},
            "activation_route": {"id": "ar_1234567890abcdef", "token": "t" * 32},
        }}}
        registry = {"fixture": {
            "instance": "site-a", "root": "/tmp/site-a", "kind": "wordpress",
        }}
        with mock.patch.object(
                instances, "_core",
                return_value=SimpleNamespace(registry_all=lambda: registry)):
            resolved = instances.resolve_instances(cfg)["site-a"]
        self.assertEqual(resolved["instance_lifecycle"]["mode"], "idle_stop")
        self.assertEqual(resolved["instance_lifecycle"]["idleAfterSeconds"], 300)
        self.assertEqual(resolved["activation_route"]["id"], "ar_1234567890abcdef")
        self.assertEqual(resolved["activation_route"]["token"], "t" * 32)


class TestActivationCoordinator(unittest.TestCase):
    def test_default_activation_policy_waits_thirty_minutes(self):
        from sandbox.activation.coordinator import ActivationPolicy

        policy = ActivationPolicy()
        self.assertEqual(policy.idle_after_seconds, 1800)

    def test_single_flight_pending_bound_and_idle_due(self):
        from sandbox.activation.coordinator import ActivationCoordinator, ActivationPolicy, ActivationState

        now = [1000.0]
        coordinator = ActivationCoordinator(clock=lambda: now[0])
        coordinator.register("site-a", ActivationPolicy(
            mode="idle_stop", idle_after_seconds=60, max_pending_requests=1,
        ))
        self.assertTrue(coordinator.begin_request("site-a"))
        self.assertFalse(coordinator.begin_request("site-a"))
        coordinator.mark_ready("site-a")
        coordinator.end_request("site-a")
        self.assertEqual(coordinator.due_for_suspend(), ())
        now[0] += 61
        self.assertEqual(coordinator.due_for_suspend(), ("site-a",))
        self.assertEqual(coordinator.snapshot("site-a")["state"], ActivationState.DRAINING.value)

    def test_ready_route_admits_bursts_beyond_pending_bound(self):
        from sandbox.activation.coordinator import ActivationCoordinator, ActivationPolicy
        coordinator = ActivationCoordinator(clock=lambda: 1000.0)
        coordinator.register("site-a", ActivationPolicy(
            mode="idle_stop", idle_after_seconds=60, max_pending_requests=1,
        ))
        self.assertTrue(coordinator.begin_request("site-a"))
        self.assertFalse(coordinator.begin_request("site-a"))
        coordinator.mark_ready("site-a")
        self.assertTrue(all(coordinator.begin_request("site-a") for _ in range(100)))

    def test_activation_server_uses_large_backlog(self):
        from unittest import mock
        from sandbox.activation import server
        created = []

        def fake_init(self, *_args, **_kwargs):
            created.append(self)
        with mock.patch.object(server.ThreadingHTTPServer, "__init__", fake_init), \
                mock.patch.object(server.ThreadingHTTPServer, "serve_forever"):
            server.serve(mock.Mock(), port=18766)
        self.assertGreaterEqual(type(created[0]).request_queue_size, 128)
        self.assertTrue(type(created[0]).daemon_threads)

    def test_lease_prevents_suspend_until_expiry(self):
        from sandbox.activation.coordinator import ActivationCoordinator, ActivationPolicy

        now = [100.0]
        coordinator = ActivationCoordinator(clock=lambda: now[0])
        coordinator.register("site-b", ActivationPolicy(mode="idle_stop", idle_after_seconds=60))
        coordinator.mark_ready("site-b")
        lease = coordinator.acquire_lease("site-b", "job", ttl_seconds=100)
        now[0] += 61
        self.assertEqual(coordinator.due_for_suspend(), ())
        now[0] += 40
        self.assertEqual(coordinator.due_for_suspend(), ())
        now[0] += 60
        self.assertEqual(coordinator.due_for_suspend(), ("site-b",))
        self.assertFalse(coordinator.release_lease(lease.lease_id))

    def test_activation_service_starts_once_for_concurrent_requests(self):
        from sandbox.activation import ActivationPolicy, ActivationService
        import threading

        service = ActivationService()
        service.register("site-c", ActivationPolicy(mode="idle_stop"))
        starts = []
        ready = threading.Barrier(3)
        resumed = threading.Event()
        results = []

        def resume(route_id, timeout):
            starts.append((route_id, timeout))
            resumed.set()
            return True

        def invoke():
            ready.wait(timeout=2)
            results.append(service.activate("site-c", resume=resume))

        workers = [threading.Thread(target=invoke) for _ in range(2)]
        for worker in workers:
            worker.start()
        ready.wait(timeout=2)
        for worker in workers:
            worker.join(timeout=2)
        self.assertTrue(resumed.is_set())
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item.ok for item in results))


class TestApplyServerChangePolicy(unittest.TestCase):
    def test_apply_refuses_declared_server_change_with_no_compose_and_no_state_write(self):
        import contextlib
        import sandbox_core as sc
        from sandbox.core import _instances

        pconf = {
            "root": "/tmp/fake-project",
            "server": "litespeed",
            "server_declared": True,
        }
        existing = {
            "instance": "demo",
            "label": "default",
            "wordpress_port": 8088,
            "db_port": 3307,
            "mailpit_port": 8025,
            "server": "nginx",
        }
        cfg = {
            "instances": {
                "demo": {"server": "nginx", "wordpress_port": 8088, "db_port": 3307, "mailpit_port": 8025},
            }
        }
        with mock.patch.object(sc, "load_project_config", return_value=pconf), \
             mock.patch.object(sc, "registry_get", return_value=existing), \
             mock.patch.object(sc, "project_lock", return_value=contextlib.nullcontext()), \
             mock.patch.object(_instances, "compose") as mock_compose, \
             mock.patch.object(_instances, "_write_local_yaml") as mock_write_local:
            with self.assertRaises(sc.ConfigError) as ctx:
                _instances.apply_config(cfg, project_dir="/tmp/fake-project")

            err = ctx.exception
            self.assertEqual(getattr(err, "code", None), "apply_server_change_refused")
            msg = str(err)
            self.assertIn("apply_server_change_refused", msg)
            self.assertIn("nginx", msg)
            self.assertIn("litespeed", msg)
            self.assertIn("No state was changed", msg)
            self.assertIn("sb server demo litespeed", msg)
            self.assertIn("on the host that runs this instance", msg.lower())
            self.assertIn("(it does not verify the result or roll back; check the site after)", msg)
            self.assertIn("or set the project's `server` to 'nginx' if that is what you want.", msg)
            mock_compose.assert_not_called()
            mock_write_local.assert_not_called()

    def test_apply_undeclared_server_keeps_litespeed_block_without_false_refusal(self):
        import contextlib
        from sandbox.core import _instances
        import sandbox_core as sc

        pconf = {
            "root": "/tmp/fake-project",
            "server": "nginx",  # default in pconf
            "server_declared": False,
        }
        existing = {
            "instance": "demo",
            "label": "default",
            "wordpress_port": 8088,
            "db_port": 3307,
            "mailpit_port": 8025,
            "server": "litespeed",
        }
        cfg = {
            "instances": {
                "demo": {"server": "litespeed", "wordpress_port": 8088, "db_port": 3307, "mailpit_port": 8025},
            }
        }
        with mock.patch.object(sc, "load_project_config", return_value=pconf), \
             mock.patch.object(sc, "registry_get", return_value=existing), \
             mock.patch.object(sc, "project_lock", return_value=contextlib.nullcontext()), \
             mock.patch.object(_instances, "_assert_apply_runtime_dependencies"), \
             mock.patch.object(_instances, "prepare_php_extension_runtime", return_value=None), \
             mock.patch.object(_instances, "_write_local_yaml") as mock_write_local, \
             mock.patch.object(_instances, "load_config", return_value=cfg), \
             mock.patch.object(_instances, "write_compose_files"), \
             mock.patch.object(_instances, "compose", return_value=mock.MagicMock(returncode=0)), \
             mock.patch.object(_instances, "_wait_reachable", return_value=True), \
             mock.patch.object(_instances, "_wire_project_plugins"), \
             mock.patch.object(_instances, "_wire_project_themes"), \
             mock.patch.object(_instances, "_reconcile_wp_core", return_value={"ok": True}), \
             mock.patch.object(_instances, "site_url", return_value="http://localhost:8088"), \
             mock.patch.object(sc, "registry_put", return_value=existing):
            _instances.apply_config(cfg, project_dir="/tmp/fake-project")
            saved_local = mock_write_local.call_args[0][0]
            self.assertEqual(saved_local["instances"]["demo"]["server"], "litespeed")

    def test_apply_matching_declared_server_passes_without_refusal(self):
        import contextlib
        from sandbox.core import _instances
        import sandbox_core as sc

        pconf = {
            "root": "/tmp/fake-project",
            "server": "nginx",
            "server_declared": True,
        }
        existing = {
            "instance": "demo",
            "label": "default",
            "wordpress_port": 8088,
            "db_port": 3307,
            "mailpit_port": 8025,
            "server": "nginx",
        }
        cfg = {
            "instances": {
                "demo": {"server": "nginx", "wordpress_port": 8088, "db_port": 3307, "mailpit_port": 8025},
            }
        }
        with mock.patch.object(sc, "load_project_config", return_value=pconf), \
             mock.patch.object(sc, "registry_get", return_value=existing), \
             mock.patch.object(sc, "project_lock", return_value=contextlib.nullcontext()), \
             mock.patch.object(_instances, "_assert_apply_runtime_dependencies"), \
             mock.patch.object(_instances, "prepare_php_extension_runtime", return_value=None), \
             mock.patch.object(_instances, "_write_local_yaml") as mock_write_local, \
             mock.patch.object(_instances, "load_config", return_value=cfg), \
             mock.patch.object(_instances, "write_compose_files"), \
             mock.patch.object(_instances, "compose", return_value=mock.MagicMock(returncode=0)), \
             mock.patch.object(_instances, "_wait_reachable", return_value=True), \
             mock.patch.object(_instances, "_wire_project_plugins"), \
             mock.patch.object(_instances, "_wire_project_themes"), \
             mock.patch.object(_instances, "_reconcile_wp_core", return_value={"ok": True}), \
             mock.patch.object(_instances, "site_url", return_value="http://localhost:8088"), \
             mock.patch.object(sc, "registry_put", return_value=existing):
            _instances.apply_config(cfg, project_dir="/tmp/fake-project")
            saved_local = mock_write_local.call_args[0][0]
            self.assertEqual(saved_local["instances"]["demo"]["server"], "nginx")

    def test_apply_herd_crossing_message_does_not_suggest_delete_and_ensure(self):
        import contextlib
        import sandbox_core as sc
        from sandbox.core import _instances
        from sandbox.commands import net

        pconf = {
            "root": "/tmp/fake-project",
            "server": "herd",
            "server_declared": True,
        }
        existing = {
            "instance": "demo",
            "label": "default",
            "wordpress_port": 8088,
            "db_port": 3307,
            "mailpit_port": 8025,
            "server": "nginx",
        }
        cfg = {
            "instances": {
                "demo": {"server": "nginx", "wordpress_port": 8088, "db_port": 3307, "mailpit_port": 8025},
            }
        }
        with mock.patch.object(sc, "load_project_config", return_value=pconf), \
             mock.patch.object(sc, "registry_get", return_value=existing), \
             mock.patch.object(sc, "project_lock", return_value=contextlib.nullcontext()):
            with self.assertRaises(sc.ConfigError) as ctx:
                _instances.apply_config(cfg, project_dir="/tmp/fake-project")
            msg = str(ctx.exception)
            self.assertIn("re-provision", msg)
            self.assertIn("deletes the DB and files and needs its own plan", msg)
            self.assertNotIn("delete", msg.lower().replace("deletes the db", ""))

        # Also verify net.cmd_server herd crossing
        args = mock.MagicMock()
        args.name = "demo"
        args.server_type = "herd"
        with mock.patch.object(net, "resolve_instances", return_value={"demo": {"server": "nginx"}}), \
             mock.patch.object(net, "die", side_effect=SystemExit(1)) as mock_die:
            with self.assertRaises(SystemExit):
                net.cmd_server(cfg, args)
            die_msg = mock_die.call_args[0][0]
            self.assertIn("re-provision", die_msg)
            self.assertIn("deletes the DB and files and needs its own plan", die_msg)
            self.assertNotIn("delete", die_msg.lower().replace("deletes the db", ""))



class TestEnsureServerChangePolicyRefusal(unittest.TestCase):
    def test_ensure_refuses_a_declared_change_before_any_effect(self):
        import contextlib
        import sandbox_core as sc
        from sandbox.core import _instances

        pconf = {
            "root": "/tmp/fake-project",
            "server": "litespeed",
            "server_declared": True,
        }
        cfg = {
            "instances": {
                "demo": {"server": "nginx", "wordpress_port": 8088, "db_port": 3307, "mailpit_port": 8025},
            }
        }
        for status in ("ready", "stopped", "error"):
            with self.subTest(status=status):
                existing = {
                    "instance": "demo",
                    "label": "default",
                    "status": status,
                    "wordpress_port": 8088,
                    "db_port": 3307,
                    "mailpit_port": 8025,
                    "server": "nginx",
                }
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(sc, "load_project_config", return_value=pconf))
                    stack.enter_context(mock.patch.object(sc, "registry_get", return_value=existing))
                    stack.enter_context(mock.patch.object(sc, "project_lock", return_value=contextlib.nullcontext()))
                    mock_put = stack.enter_context(mock.patch.object(sc, "registry_put"))
                    mock_start_stopped = stack.enter_context(mock.patch.object(_instances, "_start_stopped_instance"))
                    mock_resolve_ports = stack.enter_context(mock.patch.object(_instances, "_resolve_port_conflicts"))
                    mock_attest = stack.enter_context(mock.patch.object(_instances, "attest_source_mounts"))
                    mock_compose = stack.enter_context(mock.patch.object(_instances, "compose"))
                    mock_reserve = stack.enter_context(mock.patch("sandbox.server_config.creation_requests.reserve_creation_request"))
                    mock_pending = stack.enter_context(mock.patch("sandbox.server_config.creation_requests.pending_receipts"))
                    mock_replay = stack.enter_context(mock.patch("sandbox.server_config.creation_requests.replay_creation_result"))
                    mock_validate = stack.enter_context(mock.patch("sandbox.server_config.creation_requests.validate_resolved_context"))

                    with self.assertRaises(sc.ConfigError) as ctx:
                        _instances._ensure_instance_impl(cfg, "/tmp/fake-project", "default")

                    self.assertEqual(getattr(ctx.exception, "code", None), "apply_server_change_refused")
                    mock_put.assert_not_called()
                    mock_start_stopped.assert_not_called()
                    mock_resolve_ports.assert_not_called()
                    mock_attest.assert_not_called()
                    mock_compose.assert_not_called()
                    mock_reserve.assert_not_called()
                    mock_pending.assert_not_called()
                    mock_replay.assert_not_called()
                    mock_validate.assert_not_called()

    def test_ensure_with_a_creation_request_refuses_before_writing_a_receipt(self):
        import contextlib
        import sandbox_core as sc
        from sandbox.core import _instances

        pconf = {
            "root": "/tmp/fake-project",
            "server": "litespeed",
            "server_declared": True,
        }
        cfg = {
            "instances": {
                "demo": {"server": "nginx", "wordpress_port": 8088, "db_port": 3307, "mailpit_port": 8025},
            }
        }
        existing = {
            "instance": "demo",
            "label": "default",
            "status": "ready",
            "wordpress_port": 8088,
            "db_port": 3307,
            "mailpit_port": 8025,
            "server": "nginx",
            "instance_incarnation_id": "inc-123",
        }
        creation_ctx = {
            "operation_id": "op-456",
            "intent_digest": "sha256:abc",
            "label": "default",
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sc, "load_project_config", return_value=pconf))
            stack.enter_context(mock.patch.object(sc, "registry_get", return_value=existing))
            stack.enter_context(mock.patch.object(sc, "project_lock", return_value=contextlib.nullcontext()))
            mock_put = stack.enter_context(mock.patch.object(sc, "registry_put"))
            mock_start_stopped = stack.enter_context(mock.patch.object(_instances, "_start_stopped_instance"))
            mock_resolve_ports = stack.enter_context(mock.patch.object(_instances, "_resolve_port_conflicts"))
            mock_attest = stack.enter_context(mock.patch.object(_instances, "attest_source_mounts"))
            mock_compose = stack.enter_context(mock.patch.object(_instances, "compose"))
            mock_reserve = stack.enter_context(mock.patch("sandbox.server_config.creation_requests.reserve_creation_request"))
            mock_pending = stack.enter_context(mock.patch("sandbox.server_config.creation_requests.pending_receipts"))
            mock_replay = stack.enter_context(mock.patch("sandbox.server_config.creation_requests.replay_creation_result"))
            mock_validate = stack.enter_context(mock.patch("sandbox.server_config.creation_requests.validate_resolved_context"))

            with self.assertRaises(sc.ConfigError) as ctx:
                _instances._ensure_instance_impl(cfg, "/tmp/fake-project", "default", creation_context=creation_ctx)

            self.assertEqual(getattr(ctx.exception, "code", None), "apply_server_change_refused")
            mock_put.assert_not_called()
            mock_start_stopped.assert_not_called()
            mock_resolve_ports.assert_not_called()
            mock_attest.assert_not_called()
            mock_compose.assert_not_called()
            mock_reserve.assert_not_called()
            mock_pending.assert_not_called()
            mock_replay.assert_not_called()
            mock_validate.assert_not_called()

    def test_ensure_pending_row_is_not_refused(self):
        import contextlib
        import sandbox_core as sc
        from sandbox.core import _instances

        pconf = {
            "root": "/tmp/fake-project",
            "server": "litespeed",
            "server_declared": True,
        }
        existing = {
            "instance": "demo",
            "label": "default",
            "status": "pending",
            "server": "nginx",
        }
        cfg = {
            "instances": {
                "demo": {"server": "litespeed", "wordpress_port": 8088, "db_port": 3307, "mailpit_port": 8025},
            }
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sc, "load_project_config", return_value=pconf))
            stack.enter_context(mock.patch.object(sc, "registry_get", return_value=existing))
            stack.enter_context(mock.patch.object(sc, "project_lock", return_value=contextlib.nullcontext()))
            stack.enter_context(mock.patch.object(_instances, "_docker_preflight"))
            stack.enter_context(mock.patch.object(_instances, "docker_daemon_preflight", return_value={"ok": True}))
            stack.enter_context(mock.patch.object(_instances, "_proxy_sudoers_installed", return_value=False))
            stack.enter_context(mock.patch.object(_instances, "_secure_at_create", return_value=False))
            stack.enter_context(mock.patch.object(_instances, "_resolve_port_conflicts", side_effect=lambda c, **kw: c))
            mock_build = stack.enter_context(mock.patch.object(_instances, "_build_instance_block", return_value={"server": "litespeed"}))
            stack.enter_context(mock.patch.object(_instances, "prepare_php_extension_runtime", return_value=None))
            stack.enter_context(mock.patch.object(_instances, "_write_local_yaml"))
            stack.enter_context(mock.patch.object(_instances, "load_config", return_value=cfg))
            stack.enter_context(mock.patch.object(_instances, "write_compose_files"))
            stack.enter_context(mock.patch("sandbox.commands.lifecycle.cmd_up"))
            stack.enter_context(mock.patch("sandbox.commands.lifecycle.cmd_install"))
            stack.enter_context(mock.patch.object(_instances, "_instance_web_services_running", return_value=(True, "")))
            stack.enter_context(mock.patch.object(_instances, "_auto_heal_wp_url"))
            stack.enter_context(mock.patch.object(_instances, "_wait_http", return_value=True))
            stack.enter_context(mock.patch.object(_instances, "_wait_reachable", return_value=True))
            stack.enter_context(mock.patch.object(_instances, "_wire_project_plugins"))
            stack.enter_context(mock.patch.object(_instances, "_wire_project_themes"))
            stack.enter_context(mock.patch.object(_instances, "site_url", return_value="http://localhost:8088"))
            stack.enter_context(mock.patch.object(_instances, "_refresh_registered_url", return_value=existing))
            stack.enter_context(mock.patch("sandbox.commands.data.capture_install_snapshots"))
            mock_put = stack.enter_context(mock.patch.object(sc, "registry_put", return_value=existing))

            res = _instances._ensure_instance_impl(cfg, "/tmp/fake-project", "default")
            self.assertIsNotNone(res)
            self.assertEqual(mock_build.call_args[0][5], "litespeed")

    def test_ensure_new_project_is_not_refused(self):
        import contextlib
        import sandbox_core as sc
        from sandbox.core import _instances

        pconf = {
            "root": "/tmp/fake-project",
            "server": "litespeed",
            "server_declared": True,
        }
        cfg = {"instances": {}}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sc, "load_project_config", return_value=pconf))
            stack.enter_context(mock.patch.object(sc, "registry_get", return_value=None))
            stack.enter_context(mock.patch.object(sc, "registry_list_for_root", return_value=[]))
            stack.enter_context(mock.patch.object(sc, "registry_all", return_value={}))
            stack.enter_context(mock.patch.object(sc, "project_lock", return_value=contextlib.nullcontext()))
            stack.enter_context(mock.patch.object(_instances, "_derive_instance_name", return_value="fake-project"))
            stack.enter_context(mock.patch.object(_instances, "_docker_preflight"))
            stack.enter_context(mock.patch.object(_instances, "docker_daemon_preflight", return_value={"ok": True}))
            stack.enter_context(mock.patch.object(_instances, "_proxy_sudoers_installed", return_value=False))
            stack.enter_context(mock.patch.object(_instances, "_secure_at_create", return_value=False))
            mock_build = stack.enter_context(mock.patch.object(_instances, "_build_instance_block", return_value={"server": "litespeed"}))
            stack.enter_context(mock.patch.object(_instances, "prepare_php_extension_runtime", return_value=None))
            stack.enter_context(mock.patch.object(_instances, "_write_local_yaml"))
            stack.enter_context(mock.patch.object(_instances, "load_config", return_value={"instances": {"fake-project": {"server": "litespeed"}}}))
            stack.enter_context(mock.patch.object(_instances, "write_compose_files"))
            stack.enter_context(mock.patch("sandbox.commands.lifecycle.cmd_up"))
            stack.enter_context(mock.patch("sandbox.commands.lifecycle.cmd_install"))
            stack.enter_context(mock.patch.object(_instances, "_instance_web_services_running", return_value=(True, "")))
            stack.enter_context(mock.patch.object(_instances, "_auto_heal_wp_url"))
            stack.enter_context(mock.patch.object(_instances, "_wait_http", return_value=True))
            stack.enter_context(mock.patch.object(_instances, "_wait_reachable", return_value=True))
            stack.enter_context(mock.patch.object(_instances, "_wire_project_plugins"))
            stack.enter_context(mock.patch.object(_instances, "_wire_project_themes"))
            stack.enter_context(mock.patch.object(_instances, "site_url", return_value="http://localhost:8088"))
            stack.enter_context(mock.patch.object(_instances, "_refresh_registered_url", return_value={"instance": "fake-project"}))
            stack.enter_context(mock.patch("sandbox.commands.data.capture_install_snapshots"))
            mock_put = stack.enter_context(mock.patch.object(sc, "registry_put", return_value={"instance": "fake-project"}))

            res = _instances._ensure_instance_impl(cfg, "/tmp/fake-project", "default")
            self.assertIsNotNone(res)
            self.assertEqual(mock_build.call_args[0][5], "litespeed")

    def test_ensure_ready_with_no_declaration_is_unchanged(self):
        import contextlib
        import sandbox_core as sc
        from sandbox.core import _instances

        pconf = {
            "root": "/tmp/fake-project",
            "server": "nginx",  # default in pconf
            "server_declared": False,
        }
        existing = {
            "instance": "demo",
            "label": "default",
            "status": "ready",
            "server": "litespeed",
            "wordpress_port": 8088,
            "db_port": 3307,
            "mailpit_port": 8025,
            "url": "http://localhost:8088",
            "login_url": "",
            "admin_url": "http://localhost:8088/wp-admin/",
        }
        cfg = {
            "instances": {
                "demo": {"server": "litespeed", "wordpress_port": 8088, "db_port": 3307, "mailpit_port": 8025},
            }
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sc, "load_project_config", return_value=pconf))
            stack.enter_context(mock.patch.object(sc, "registry_get", return_value=existing))
            stack.enter_context(mock.patch.object(sc, "project_lock", return_value=contextlib.nullcontext()))
            stack.enter_context(mock.patch.object(_instances, "_docker_preflight"))
            stack.enter_context(mock.patch.object(_instances, "docker_daemon_preflight", return_value={"ok": True}))
            stack.enter_context(mock.patch.object(_instances, "attest_source_mounts", return_value={"ok": True}))
            stack.enter_context(mock.patch.object(_instances, "_instance_reachable", return_value=True))
            stack.enter_context(mock.patch.object(_instances, "_wp_core_install_state", return_value="installed"))
            stack.enter_context(mock.patch.object(_instances, "_resolve_port_conflicts", side_effect=lambda c, **kw: c))
            stack.enter_context(mock.patch.object(_instances, "_warn_version_drift"))
            stack.enter_context(mock.patch.object(_instances, "_proxy_sudoers_installed", return_value=False))
            stack.enter_context(mock.patch.object(_instances, "site_url", return_value="http://localhost:8088"))
            stack.enter_context(mock.patch.object(_instances, "_auto_heal_wp_url"))
            stack.enter_context(mock.patch.object(_instances, "_wait_reachable", return_value=True))
            stack.enter_context(mock.patch.object(_instances, "_refresh_registered_url", return_value=existing))

            res = _instances._ensure_instance_impl(cfg, "/tmp/fake-project", "default")
            self.assertEqual(res["instance"], "demo")
            self.assertEqual(res["server"], "litespeed")

    def test_record_with_no_established_server_accepts_a_declaration(self):
        from sandbox.core import _instances

        cfg = {"instances": {}}
        existing = {
            "instance": "unrecorded-instance",
            "label": "default",
            "status": "stopped",
        }
        pconf = {
            "root": "/tmp/fake-project",
            "server": "litespeed",
            "server_declared": True,
        }
        server = _instances._resolve_reconcile_server(cfg, "unrecorded-instance", pconf, existing)
        self.assertEqual(server, "litespeed")

if __name__ == "__main__":
    unittest.main()
