from __future__ import annotations

import ast
import contextlib
import io
import time
import http.server
import json
import os
import pathlib
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch, MagicMock

from sandbox.hosting.front_door import nginx as current_nginx
from sandbox.hosting.zone_coverage import (
    COVERAGE_FALLBACK_UNVERIFIED,
    CoverageError,
)
from sandbox.hosting.zone_coverage.fallback import (
    FALLBACK_BODY,
    render_fallback_conf,
    run_origin_probe,
    verify_origin_probe_result,
)
from sandbox.hosting.zone_coverage.program import remote_command


class TestZoneCoverageSurface(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="sb-test-surface-")
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)

    # 19. Built store command executed with fake HOME and SANDBOX_HOME set; empty home creates nothing
    def test_19_built_store_command_execution(self):
        home_dir = os.path.join(self.td, "fake-home")
        sb_home_dir = os.path.join(self.td, "fake-sb-home")
        os.makedirs(home_dir, exist_ok=True)
        os.makedirs(sb_home_dir, exist_ok=True)

        env = dict(os.environ, HOME=home_dir, SANDBOX_HOME=sb_home_dir)

        # 1. read action on empty home
        read_cmd = remote_command({"action": "read", "zone": "asb.bd"})
        res = subprocess.run(read_cmd, shell=True, env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("state"), "absent")
        self.assertIsNone(data.get("document"))
        # Verify read on empty home created no files or directories
        runtime_dir = os.path.join(sb_home_dir, "runtime", "zone-coverage")
        self.assertFalse(os.path.exists(runtime_dir), "read must create nothing on disk")

        # 2. list action on empty home
        list_cmd = remote_command({"action": "list"})
        res = subprocess.run(list_cmd, shell=True, env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("zones"), [])
        self.assertFalse(os.path.exists(runtime_dir), "list must create nothing on disk")

        # 3. mutating action creates the directory layout
        begin_cmd = remote_command({"action": "begin", "zone": "asb.bd", "kind": "enable", "controller": "tester"})
        res = subprocess.run(begin_cmd, shell=True, env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertIn("operation_id", data)
        self.assertTrue(os.path.exists(os.path.join(runtime_dir, "zones", "asb.bd.json")))
        self.assertTrue(os.path.exists(os.path.join(runtime_dir, "coverage.lock")))

    # 20. 12 real-looking pair sets against 13c2d35 fixture
    def test_20_name_overlap_conflict_fixture_comparison(self):
        old_path = "/Users/alim/Sites/git/.scratch/sandbox/wildcard-070/terra-stage1-r3-nginx/old_nginx.py"
        self.assertTrue(os.path.exists(old_path), f"Fixture missing: {old_path}")

        old_text = pathlib.Path(old_path).read_text()
        tree = ast.parse(old_text)
        old_ns = {
            "re": __import__("re"),
            "Iterable": __import__("typing").Iterable,
            "CONF_DIR": current_nginx.CONF_DIR,
            "FILE_PREFIX": current_nginx.FILE_PREFIX,
        }
        func_nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in (
            "is_sandbox_file", "_name_covers", "names_overlap", "find_conflicts", "summarize",
        )]
        exec(compile(ast.Module(body=func_nodes, type_ignores=[]), "<old_nginx>", "exec"), old_ns)

        pairs = [
            ("example.com", "example.com"),
            ("a.example.com", "b.example.com"),
            ("*.example.com", "*.example.com"),
            ("*.example.com", "*.other.com"),
            ("app.example.com", "*.example.com"),
            ("app.sub.example.com", "*.example.com"),
            ("example.com", "*.example.com"),
            ("*.app.example.com", "*.example.com"),
            ("APP.EXAMPLE.COM", "*.example.com"),
            ("x.xn--bcher-kva.example", "*.xn--bcher-kva.example"),
            ("other.org", "*.example.com"),
            ("sub.sub.example.com", "sub.sub.example.com"),
        ]

        self.assertEqual(len(pairs), 12)

        for pat_a, pat_b in pairs:
            old_overlap = old_ns["names_overlap"](pat_a, pat_b)
            new_overlap = current_nginx.names_overlap(pat_a, pat_b)
            self.assertEqual(old_overlap, new_overlap, f"Divergence in names_overlap({pat_a}, {pat_b})")

            old_covers = old_ns["_name_covers"](pat_b, pat_a)
            new_covers = current_nginx._name_covers(pat_b, pat_a)
            self.assertEqual(old_covers, new_covers, f"Divergence in _name_covers({pat_b}, {pat_a})")

        # Conflict check with regular route: exact agreement
        regular_route = ("/etc/nginx/conf.d/sandbox-app.conf", "app.example.com")
        self.assertEqual(
            old_ns["find_conflicts"](["app.example.com"], [regular_route]),
            current_nginx.find_conflicts(["app.example.com"], [regular_route]),
        )

        # Conflict check with fallback route: exact documented divergence
        fallback_route = ("/etc/nginx/conf.d/sandbox-coverage-example.com.conf", "*.example.com")
        # Old code reported conflict because it had no fallback exemption
        old_conflicts = old_ns["find_conflicts"](["app.example.com"], [fallback_route])
        self.assertEqual(len(old_conflicts), 1)

        # New code exempts exact route from conflicting with its own fallback
        new_conflicts = current_nginx.find_conflicts(["app.example.com"], [fallback_route])
        self.assertEqual(new_conflicts, [])

    # 21. 9 cases from terra-stage1-r3-nginx/nginx_checks.py
    def test_21_overlap_cases_table(self):
        p = "/etc/nginx/conf.d/sandbox-coverage-a.example.conf"
        cases = [
            ("x.a.example", p, "*.a.example", [], "a.example"),
            ("*.a.example", p, "*.a.example", [{"hostname": "*.a.example", "server_name": "*.a.example", "file": p, "owner": "sandbox"}], "a.example"),
            ("x.a.example", "/etc/nginx/conf.d/sub/sandbox-coverage-a.example.conf", "*.a.example", [{"hostname": "x.a.example", "server_name": "*.a.example", "file": "/etc/nginx/conf.d/sub/sandbox-coverage-a.example.conf", "owner": "sandbox"}], None),
            ("x.a.example", "/etc/nginx/sites-enabled/sandbox-coverage-a.example.conf", "*.a.example", [{"hostname": "x.a.example", "server_name": "*.a.example", "file": "/etc/nginx/sites-enabled/sandbox-coverage-a.example.conf", "owner": "panel"}], None),
            ("x.b.example", p, "*.b.example", [{"hostname": "x.b.example", "server_name": "*.b.example", "file": p, "owner": "sandbox"}], "a.example"),
            ("x.b.example", p, "*.a.example", [], "a.example"),
            ("X.A.EXAMPLE.", p, "*.a.example", [], "a.example"),
            ("x.xn--bcher-kva.example", "/etc/nginx/conf.d/sandbox-coverage-xn--bcher-kva.example.conf", "*.xn--bcher-kva.example", [], "xn--bcher-kva.example"),
            ("xa.example", p, "*.a.example", [], "a.example"),
        ]

        self.assertEqual(len(cases), 9)

        for host, path, name, expected_conflicts, expected_zone in cases:
            with self.subTest(host=host, path=path, name=name):
                conflicts = current_nginx.find_conflicts([host], [(path, name)])
                self.assertEqual(conflicts, expected_conflicts)
                zone = current_nginx.coverage_zone_of(path)
                self.assertEqual(zone, expected_zone)

    # 22. Fallback route in probe and summarize
    def test_22_fallback_route_in_probe_and_summarize(self):
        fallback_file = "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf"
        probe_facts = {
            "routes": [
                {
                    "file": fallback_file,
                    "server_names": ["*.asb.bd"],
                    "owner": "sandbox",
                    "coverage_zone": "asb.bd",
                    "certificates": [{"present": True, "renewal_overdue": False, "not_after": "2027-01-01"}],
                },
                {
                    "file": "/etc/nginx/conf.d/sandbox-app.asb.bd.conf",
                    "server_names": ["app.asb.bd"],
                    "owner": "sandbox",
                    "certificates": [{"present": True, "renewal_overdue": False, "not_after": "2027-01-01"}],
                },
            ]
        }

        # Find conflicts between app route and fallback route
        existing = [(r["file"], name) for r in probe_facts["routes"] for name in r["server_names"]]
        conflicts = current_nginx.find_conflicts(["app.asb.bd"], existing)
        conflict_files = {c["file"] for c in conflicts}
        self.assertNotIn(fallback_file, conflict_files)

        # Summarize reports no certificate problems
        summary = current_nginx.summarize(probe_facts)
        self.assertEqual(summary.get("certificate_problems"), [])

        # Overdue fallback certificate reports exact advice
        probe_facts_overdue = {
            "routes": [
                {
                    "file": fallback_file,
                    "server_names": ["*.asb.bd"],
                    "owner": "sandbox",
                    "coverage_zone": "asb.bd",
                    "certificates": [{"present": True, "renewal_overdue": True, "not_after": "soon"}],
                }
            ]
        }
        summary_overdue = current_nginx.summarize(probe_facts_overdue)
        prob_msg = summary_overdue["certificate_problems"][0]
        self.assertIn("renewing a fallback certificate is not available yet", prob_msg)

    # 23. Local nginx configuration validation with throwaway cert
    def test_23_local_nginx_configuration_validation(self):
        nginx_bin = shutil.which("nginx") or "/opt/homebrew/bin/nginx"
        if not os.path.exists(nginx_bin):
            self.skipTest("nginx binary not available on host")

        cert_path = os.path.join(self.td, "cert.pem")
        key_path = os.path.join(self.td, "key.pem")
        cmd = f'openssl req -x509 -newkey rsa:2048 -keyout {key_path} -out {cert_path} -days 1 -nodes -subj "/CN=*.asb.bd"'
        subprocess.run(cmd, shell=True, check=True, capture_output=True)

        fallback_conf = render_fallback_conf("asb.bd", cert_path=cert_path, key_path=key_path, http2_directive=True)
        vhost_path = os.path.join(self.td, "fallback.conf")
        with open(vhost_path, "w") as f:
            f.write(fallback_conf)

        nginx_conf = os.path.join(self.td, "nginx.conf")
        with open(nginx_conf, "w") as f:
            f.write(f"""
pid {self.td}/nginx.pid;
events {{ worker_connections 64; }}
http {{
    include {vhost_path};
}}
""")

        r = subprocess.run([nginx_bin, "-t", "-c", nginx_conf], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"nginx -t failed:\nstdout: {r.stdout}\nstderr: {r.stderr}")
        self.assertIn("syntax is ok", r.stderr)
        self.assertIn("test is successful", r.stderr)

    # 24. Origin probe on real local TLS socket
    def test_24_origin_probe_loopback_tls_socket(self):
        cert_path = os.path.join(self.td, "cert.pem")
        key_path = os.path.join(self.td, "key.pem")
        cmd = f'openssl req -x509 -newkey rsa:2048 -keyout {key_path} -out {cert_path} -days 1 -nodes -subj "/CN=*.asb.bd"'
        subprocess.run(cmd, shell=True, check=True, capture_output=True)

        seen_sni = []

        def sni_callback(sock, req_sni, ctx):
            seen_sni.append(req_sni)

        tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_ctx.load_cert_chain(cert_path, key_path)
        tls_ctx.sni_callback = sni_callback

        response_status = 404
        response_headers = {"X-Sandbox-Fallback": "1", "Content-Type": "text/html"}
        response_body = FALLBACK_BODY.encode("utf-8")

        class DynamicHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(response_status)
                for k, v in response_headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(response_body)

            def log_message(self, *args):
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), DynamicHandler)
        httpd.socket = tls_ctx.wrap_socket(httpd.socket, server_side=True)
        port = httpd.socket.getsockname()[1]

        t = threading.Thread(target=httpd.serve_forever)
        t.daemon = True
        t.start()
        self.addCleanup(httpd.shutdown)

        entry = {"name": "local-test"}

        def local_ssh(entry, cmd, timeout=10):
            return subprocess.run(cmd, shell=True, capture_output=True, text=True)

        # 1. Happy path: 404 with fallback header and body
        res = run_origin_probe(entry, "asb.bd", port=port, probe_host="probe-label.asb.bd", ssh_run=local_ssh)
        self.assertTrue(verify_origin_probe_result(res))
        self.assertEqual(seen_sni[-1], "probe-label.asb.bd")

        # 2. HTTP 200 fails
        response_status = 200
        with self.assertRaises(CoverageError) as ctx:
            run_origin_probe(entry, "asb.bd", port=port, probe_host="probe-200.asb.bd", ssh_run=local_ssh)
        self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)

        # 3. Missing fallback header fails
        response_status = 404
        response_headers = {"Content-Type": "text/html"}
        with self.assertRaises(CoverageError) as ctx:
            run_origin_probe(entry, "asb.bd", port=port, probe_host="probe-no-hdr.asb.bd", ssh_run=local_ssh)
        self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)

        # 4. Extra or different body fails (fixed body + extra text)
        response_headers = {"X-Sandbox-Fallback": "1", "Content-Type": "text/html"}
        response_body = FALLBACK_BODY.encode("utf-8") + b"<!-- extra trailing content -->"
        with self.assertRaises(CoverageError) as ctx:
            run_origin_probe(entry, "asb.bd", port=port, probe_host="probe-diff-body.asb.bd", ssh_run=local_ssh)
        self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)

        # Direct test on verify_origin_probe_result with fixed body + extra text
        extra_body_data = {
            "status": 404,
            "fallback_header": "1",
            "body": FALLBACK_BODY + "<!-- extra trailing content -->",
        }
        with self.assertRaises(CoverageError) as ctx:
            verify_origin_probe_result(extra_body_data)
        self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)

        # 5. Connection refused / error returns unverified without message text
        with self.assertRaises(CoverageError) as ctx:
            run_origin_probe(entry, "asb.bd", port=65530, probe_host="probe-closed.asb.bd", ssh_run=local_ssh)
        self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)
        self.assertNotIn("Connection refused", str(ctx.exception))

    # 25. CLI parsing and execution bounds
    def test_25_cli_parsing_and_execution_bounds(self):
        from sandbox.commands import remote as remote_cmd

        # 1. Refuse unavailable / Stage 3 flags
        for flag in ("disable", "repair", "renew_certificate", "remove_fallback", "release_dependent"):
            args = types.SimpleNamespace(
                action="coverage",
                name="test-remote",
                zone="asb.bd",
                **{flag: True},
            )
            with patch("sandbox.core._remote.get_remote", return_value={"name": "test-remote", "front_door": "nginx"}):
                with self.assertRaises(SystemExit) as raised:
                    remote_cmd._cmd_coverage(args, as_json=False)
                self.assertEqual(raised.exception.code, 1)

        # 2. Caddy front door refuses
        args = types.SimpleNamespace(
            action="coverage",
            name="caddy-remote",
            zone="asb.bd",
            enable=True,
            confirm=False,
            disable=False,
            repair=False,
            renew_certificate=False,
            remove_fallback=False,
            release_dependent=False,
        )
        with patch("sandbox.core._remote.get_remote", return_value={"name": "caddy-remote", "front_door": "caddy"}):
            with self.assertRaises(SystemExit) as raised:
                remote_cmd._cmd_coverage(args, as_json=False)
            self.assertEqual(raised.exception.code, 1)

        # 3. Failing coverage exits non-zero
        args = types.SimpleNamespace(
            action="coverage",
            name="test-remote",
            zone="asb.bd",
            enable=True,
            confirm=True,
            plan_digest="dummy",
            disable=False,
            repair=False,
            renew_certificate=False,
            remove_fallback=False,
            release_dependent=False,
        )
        with patch("sandbox.core._remote.get_remote", return_value={"name": "test-remote", "front_door": "nginx"}), \
             patch("sandbox.hosting.zone_coverage.service.plan_enable", side_effect=CoverageError("coverage_store_unavailable", "boom")):
            with self.assertRaises(SystemExit) as raised:
                remote_cmd._cmd_coverage(args, as_json=False)
            self.assertEqual(raised.exception.code, 1)

        # 4. Edge status prints coverage fallback tag
        args_edge = types.SimpleNamespace(
            action="edge",
            name="test-remote",
            json=False,
            remove=False,
        )
        edge_report = {
            "nginx_active": True,
            "config_valid": True,
            "nginx_version": "1.26.0",
            "unloaded_routes": [],
            "conflicts": [],
            "certificate_problems": [],
            "routes": [
                {
                    "file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf",
                    "loaded": True,
                    "coverage_fallback": True,
                    "hostnames": ["*.asb.bd"],
                    "certificates": [],
                }
            ],
        }
        with patch("sandbox.core._remote.get_remote", return_value={"name": "test-remote", "front_door": "nginx"}), \
             patch("sandbox.hosting.front_door.nginx.status", return_value=edge_report), \
             patch("builtins.print") as mock_print:
            remote_cmd._cmd_edge(args_edge, as_json=False)
            printed = [call[0][0] for call in mock_print.call_args_list if call[0]]
            fallback_printed = any("(coverage fallback)" in line for line in printed)
            self.assertTrue(fallback_printed, f"Expected '(coverage fallback)' in output: {printed}")

    # 26. AST import boundary scan
    def test_26_ast_import_boundary_scan(self):
        zone_cov_dir = pathlib.Path(__file__).parent.parent / "sandbox" / "hosting" / "zone_coverage"
        self.assertTrue(zone_cov_dir.is_dir(), f"Not a directory: {zone_cov_dir}")

        allowed_prefixes = (
            "sandbox.core",
            "sandbox.hosting.front_door",
            "sandbox.hosting.zone_coverage",
        )

        violations = []
        for py_file in zone_cov_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("sandbox.") and not node.module.startswith(allowed_prefixes):
                        violations.append((str(py_file.name), node.module))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("sandbox.") and not alias.name.startswith(allowed_prefixes):
                            violations.append((str(py_file.name), alias.name))

        self.assertEqual(violations, [], f"Architecture boundary violations detected in zone_coverage: {violations}")



    # 27. CLI expired enable resume then failed probe rolls back owned records
    def test_cli_expired_enable_resume_then_failed_probe_rolls_back_owned_records(self):
        from sandbox import cli
        from sandbox.hosting.zone_coverage.client import ZoneCoverageClient
        from sandbox.hosting.zone_coverage import program
        from tests.test_zone_coverage_matrix import StatefulFakeProvider

        entry = {
            "name": "test-remote",
            "front_door": "nginx",
            "origin_ipv4": "198.51.100.1",
        }

        # Real temporary store execution via local subprocess
        def real_ssh_run(ent, command, timeout=15):
            env = dict(os.environ, SANDBOX_HOME=self.td, HOME=self.td)
            return subprocess.run(command, shell=True, env=env, capture_output=True, text=True, timeout=timeout)

        # 1. Seed store with an expired enabling operation holding publish_intent
        store_client = ZoneCoverageClient(entry, ssh_run=real_ssh_run)
        begin_res = store_client.begin("asb.bd", "enable", "controller-prior", zone_id="z-test")
        self.assertTrue(begin_res.get("ok"))
        op_id = begin_res["document"]["operation"]["id"]

        prior_intent = {
            "marker": "prior-marker-1234",
            "records": [
                {"name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True},
            ],
        }
        phase_res = store_client.phase("asb.bd", op_id, "publish_intent", updates={"publish_intent": prior_intent})
        self.assertTrue(phase_res.get("ok"))

        # Expire lease on disk
        doc_path = os.path.join(self.td, "runtime", "zone-coverage", "zones", "asb.bd.json")
        with open(doc_path, "r") as f:
            doc_data = json.load(f)
        doc_data["operation"]["lease_expires_at"] = time.time() - 50
        with open(doc_path, "w") as f:
            json.dump(doc_data, f)

        # 2. Stateful fake provider holding our prior record plus a third party record
        cf = StatefulFakeProvider(
            zone_id="z-test",
            records=[
                {"id": "rec-ours", "name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True, "comment": "prior-marker-1234"},
                {"id": "rec-third", "name": "third.asb.bd", "type": "A", "content": "9.9.9.9", "proxied": False, "comment": "third-party"},
            ],
        )

        facts = {"config_valid": True, "version": [1, 26, 0], "routes": []}

        # 3. CLI Plan via actual argv parsing
        plan_out = io.StringIO()
        plan_argv = ["remote", "coverage", "test-remote", "--zone", "asb.bd", "--enable", "--plan", "--json"]
        with patch("sandbox.core._remote.get_remote", return_value=entry), \
             patch("sandbox.core._remote.ssh_run", side_effect=real_ssh_run), \
             patch("sandbox.core._cloudflare.Client", return_value=cf), \
             patch("sandbox.hosting.front_door.nginx.probe", return_value=facts), \
             contextlib.redirect_stdout(plan_out):
            with patch.object(sys, "argv", ["sb"] + plan_argv): cli.main()

        plan_data = json.loads(plan_out.getvalue())
        self.assertTrue(plan_data.get("ok"))
        self.assertTrue(plan_data.get("resumable"))
        self.assertEqual(len(plan_data.get("records_owned_from_prior_attempt")), 1)
        self.assertEqual(plan_data["records_owned_from_prior_attempt"][0]["id"], "rec-ours")
        plan_digest = plan_data["plan_digest"]

        # 4. CLI Confirm without --resume refuses with non-zero exit
        confirm_no_resume_argv = [
            "remote", "coverage", "test-remote", "--zone", "asb.bd", "--enable", "--confirm",
            "--plan-digest", plan_digest, "--json",
        ]
        with patch("sandbox.core._remote.get_remote", return_value=entry), \
             patch("sandbox.core._remote.ssh_run", side_effect=real_ssh_run), \
             patch("sandbox.core._cloudflare.Client", return_value=cf), \
             patch("sandbox.hosting.front_door.nginx.probe", return_value=facts):
            with self.assertRaises(SystemExit) as raised:
                with patch.object(sys, "argv", ["sb"] + confirm_no_resume_argv): cli.main()
            self.assertEqual(raised.exception.code, 1)

        # 5. CLI Confirm with --resume and failed probe rolls back owned records
        confirm_resume_argv = [
            "remote", "coverage", "test-remote", "--zone", "asb.bd", "--enable", "--confirm",
            "--plan-digest", plan_digest, "--resume", "--json",
        ]
        with patch("sandbox.core._remote.get_remote", return_value=entry), \
             patch("sandbox.core._remote.ssh_run", side_effect=real_ssh_run), \
             patch("sandbox.core._cloudflare.Client", return_value=cf), \
             patch("sandbox.hosting.front_door.nginx.probe", return_value=facts), \
             patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f", "digest": "d"}), \
             patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False):
            with self.assertRaises(SystemExit) as raised:
                with patch.object(sys, "argv", ["sb"] + confirm_resume_argv): cli.main()
            self.assertEqual(raised.exception.code, 1)

        # Assert: owned record was deleted during rollback
        deleted_ids = [c[2] for c in cf.calls if c[0] == "delete_record"]
        self.assertIn("rec-ours", deleted_ids)
        # Assert: third party record was NOT deleted
        self.assertNotIn("rec-third", deleted_ids)

        # Assert: store state ended as incomplete
        with open(doc_path, "r") as f:
            final_doc = json.load(f)
        self.assertEqual(final_doc.get("state"), "fallback_only")


    # J10. Caddy status capability guard test via real argv
    def test_j10_caddy_status_fails_first_with_zero_calls(self):
        from sandbox import cli
        from sandbox.hosting.front_door import FrontDoorError
        import io
        from contextlib import redirect_stdout, redirect_stderr

        caddy_remote = {
            "name": "caddy-remote",
            "front_door": "caddy",
        }

        # Real argv dispatching through cli.main
        argv = ["sb", "remote", "coverage", "caddy-remote", "--zone", "asb.bd", "--json"]
        with patch("sys.argv", argv), \
             patch("sandbox.core._remote.get_remote", return_value=caddy_remote), \
             patch("sandbox.hosting.zone_coverage.client.ZoneCoverageClient") as mock_client, \
             patch("sandbox.core._cloudflare.Client") as mock_cf, \
             patch("sandbox.core._remote.ssh_run") as mock_ssh:
            out_buf = io.StringIO()
            err_buf = io.StringIO()
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main()
                self.assertEqual(ctx.exception.code, 1)

            # Zero store, provider or SSH calls
            mock_client.assert_not_called()
            mock_cf.assert_not_called()
            mock_ssh.assert_not_called()

            # Output contains front_door_capability_unavailable
            payload = json.loads(out_buf.getvalue())
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "front_door_capability_unavailable")

    # J6. Fallback and shared DNS observation in status with safe calculation
    def test_j6_status_fallback_and_dns_observation_combinations(self):
        from sandbox.hosting.zone_coverage.service import status
        entry = {"name": "test-remote", "front_door": "nginx"}

        class MemoryStore:
            def __init__(self, doc):
                self.doc = doc
            def read(self, zone):
                return {"ok": True, "document": self.doc}

        class MockCf:
            def __init__(self, records=None, exc=None):
                self._records = records or []
                self._exc = exc
            def zone(self, zone):
                return {"id": "z1"}
            def records(self, zone_id, name):
                if self._exc:
                    raise self._exc
                return self._records
            def zone_records(self, zone_id):
                if self._exc:
                    raise self._exc
                return self._records

        stored_doc = {
            "schema_version": 1,
            "zone": "asb.bd",
            "zone_id": "z1",
            "state": "active",
            "generation": 1,
            "dependents_revision": 0,
            "shared_records": [{"id": "r1", "name": "*.asb.bd", "type": "A", "content": "1.2.3.4", "proxied": True}],
            "fallback": {"file": "/r.conf", "digest": "sha256-correct"},
            "dependents": {},
            "history": [],
        }

        # 1. match / match -> safe True
        cf_match = MockCf([{"id": "r1", "name": "*.asb.bd", "type": "A", "content": "1.2.3.4", "proxied": True}])
        with patch("sandbox.hosting.front_door.nginx.read_route", return_value="dummy-content"), \
             patch("hashlib.sha256") as mock_hash:
            mock_hash.return_value.hexdigest.return_value = "sha256-correct"
            res = status(entry, "asb.bd", cf_client=cf_match, store_client=MemoryStore(stored_doc))
            self.assertEqual(res["observed"]["fallback"], "match")
            self.assertEqual(res["observed"]["shared_dns"], "match")
            self.assertTrue(res["safe"])

        # 2. missing fallback / match dns -> safe False
        with patch("sandbox.hosting.front_door.nginx.read_route", return_value=None):
            res = status(entry, "asb.bd", cf_client=cf_match, store_client=MemoryStore(stored_doc))
            self.assertEqual(res["observed"]["fallback"], "missing")
            self.assertEqual(res["observed"]["shared_dns"], "match")
            self.assertFalse(res["safe"])

        # 3. changed fallback / match dns -> safe False
        with patch("sandbox.hosting.front_door.nginx.read_route", return_value="dummy-content"), \
             patch("hashlib.sha256") as mock_hash:
            mock_hash.return_value.hexdigest.return_value = "sha256-different"
            res = status(entry, "asb.bd", cf_client=cf_match, store_client=MemoryStore(stored_doc))
            self.assertEqual(res["observed"]["fallback"], "changed")
            self.assertEqual(res["observed"]["shared_dns"], "match")
            self.assertFalse(res["safe"])

        # 4. unavailable fallback / match dns -> safe False
        with patch("sandbox.hosting.front_door.nginx.read_route", side_effect=RuntimeError("ssh fail")):
            res = status(entry, "asb.bd", cf_client=cf_match, store_client=MemoryStore(stored_doc))
            self.assertEqual(res["observed"]["fallback"], "unavailable")
            self.assertEqual(res["observed"]["shared_dns"], "match")
            self.assertFalse(res["safe"])

        # 5. match fallback / absent dns -> safe False
        cf_absent = MockCf([])
        with patch("sandbox.hosting.front_door.nginx.read_route", return_value="dummy-content"), \
             patch("hashlib.sha256") as mock_hash:
            mock_hash.return_value.hexdigest.return_value = "sha256-correct"
            res = status(entry, "asb.bd", cf_client=cf_absent, store_client=MemoryStore(stored_doc))
            self.assertEqual(res["observed"]["fallback"], "match")
            self.assertEqual(res["observed"]["shared_dns"], "absent")
            self.assertFalse(res["safe"])

        # 6. match fallback / changed dns -> safe False
        cf_changed = MockCf([{"id": "r1", "name": "*.asb.bd", "type": "A", "content": "9.9.9.9", "proxied": True}])
        with patch("sandbox.hosting.front_door.nginx.read_route", return_value="dummy-content"), \
             patch("hashlib.sha256") as mock_hash:
            mock_hash.return_value.hexdigest.return_value = "sha256-correct"
            res = status(entry, "asb.bd", cf_client=cf_changed, store_client=MemoryStore(stored_doc))
            self.assertEqual(res["observed"]["fallback"], "match")
            self.assertEqual(res["observed"]["shared_dns"], "changed")
            self.assertFalse(res["safe"])

        # 7. match fallback / unavailable dns -> safe False
        cf_unavail = MockCf(exc=RuntimeError("cf down"))
        with patch("sandbox.hosting.front_door.nginx.read_route", return_value="dummy-content"), \
             patch("hashlib.sha256") as mock_hash:
            mock_hash.return_value.hexdigest.return_value = "sha256-correct"
            res = status(entry, "asb.bd", cf_client=cf_unavail, store_client=MemoryStore(stored_doc))
            self.assertEqual(res["observed"]["fallback"], "match")
            self.assertEqual(res["observed"]["shared_dns"], "unavailable")
            self.assertFalse(res["safe"])


if __name__ == "__main__":
    unittest.main()
