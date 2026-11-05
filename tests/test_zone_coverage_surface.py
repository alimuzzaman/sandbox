from __future__ import annotations

from tests.subprocess_support import synthetic_environment

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

import sandbox.commands.remote as remote_cmd
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

        from tests.subprocess_support import synthetic_environment
        env = synthetic_environment({'HOME': home_dir, 'SANDBOX_HOME': sb_home_dir})

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
        # Base functions embedded directly from commit 13c2d35 (no external machine-local files)
        import re
        from typing import Iterable

        CONF_DIR = current_nginx.CONF_DIR
        FILE_PREFIX = current_nginx.FILE_PREFIX

        def base_is_sandbox_file(path: str) -> bool:
            base = path.rsplit("/", 1)[-1]
            return path.startswith(CONF_DIR + "/") and base.startswith(FILE_PREFIX) and base.endswith(".conf")

        def base_name_covers(pattern: str, host: str) -> bool:
            if pattern in {"_", '""', ""}:
                return False
            if pattern.startswith("~"):
                flags, regex = (re.IGNORECASE, pattern[2:]) if pattern.startswith("~*") else (0, pattern[1:])
                try:
                    return re.search(regex, host, flags) is not None
                except re.error:
                    return False
            if pattern.startswith("."):
                base = pattern[1:]
                return host == base or host.endswith("." + base)
            if pattern.startswith("*."):
                return host.endswith(pattern[1:])
            if pattern.endswith(".*"):
                return host.startswith(pattern[:-1])
            return host == pattern

        def base_names_overlap(declared: str, existing: str) -> bool:
            declared = declared.lower()
            existing = existing.lower()
            if declared.startswith("*."):
                base = declared[2:]
                if existing.startswith(("*.", ".")):
                    other = existing.lstrip("*.")
                    return other == base or other.endswith("." + base) or base.endswith("." + other)
                if existing.startswith("~"):
                    return base_name_covers(existing, "sandbox-probe." + base)
                return existing.endswith("." + base)
            return base_name_covers(existing, declared)

        def base_find_conflicts(declared: Iterable[str], pairs: Iterable[tuple[str, str]],
                               own_file: str | None = None) -> list[dict]:
            rows = []
            for host in declared:
                for path, name in pairs:
                    if own_file is not None and path == own_file:
                        continue
                    if base_names_overlap(host, name):
                        rows.append({"hostname": host, "server_name": name, "file": path,
                                     "owner": "sandbox" if base_is_sandbox_file(path) else "panel"})
            return rows

        def base_summarize(facts: dict) -> dict:
            routes = facts.get("routes") or []
            unloaded = [row["file"] for row in routes if row.get("loaded") is False]
            conflicts = [dict(item, route_file=row["file"]) for row in routes
                         for item in row.get("conflicts") or []]
            problems = []
            for row in routes:
                if row.get("readable") is False:
                    problems.append(f"{row['file']}: unreadable")
                    continue
                if row.get("mode") not in (None, "0o600"):
                    problems.append(f"{row['file']}: mode {row.get('mode')} (expected 0600)")
                for cert in row.get("certificates") or []:
                    if not cert.get("present"):
                        problems.append(f"{row['file']}: certificate missing {cert['path']}")
                    elif cert.get("expired"):
                        problems.append(f"{row['file']}: certificate expired {cert.get('not_after')}")
                    elif cert.get("renewal_overdue"):
                        action = ("renewal has not succeeded" if cert.get("issuer") == "public"
                                  else "re-apply to reissue the Origin CA certificate")
                        problems.append(f"{row['file']}: certificate expires {cert.get('not_after')}; {action}")
            return {
                "mode": "nginx",
                "nginx_installed": bool(facts.get("nginx_installed")),
                "nginx_active": bool(facts.get("active")),
                "nginx_version": ".".join(str(x) for x in facts.get("version") or []) or None,
                "config_valid": bool(facts.get("config_valid")),
                "config_test_tail": facts.get("config_test_tail") or [],
                "includes_conf_d": bool(facts.get("includes_conf_d")),
                "sandbox_caddy": facts.get("sandbox_caddy") or {},
                "routes": routes,
                "unloaded_routes": unloaded,
                "conflicts": conflicts,
                "certificate_problems": problems,
            }

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
            # Extended pair sets: .domain, regex, _, empty string
            (".example.com", "app.example.com"),
            (".example.com", "*.example.com"),
            ("~^.*\\.example\\.com$", "app.example.com"),
            ("_", "app.example.com"),
            ("_", "*.example.com"),
            ('""', "app.example.com"),
        ]

        self.assertGreaterEqual(len(pairs), 18)

        for pat_a, pat_b in pairs:
            old_overlap = base_names_overlap(pat_a, pat_b)
            new_overlap = current_nginx.names_overlap(pat_a, pat_b)
            self.assertEqual(old_overlap, new_overlap, f"Divergence in names_overlap({pat_a}, {pat_b})")

            old_covers = base_name_covers(pat_b, pat_a)
            new_covers = current_nginx._name_covers(pat_b, pat_a)
            self.assertEqual(old_covers, new_covers, f"Divergence in _name_covers({pat_b}, {pat_a})")

        # Conflict check with regular route across multiple files: exact agreement
        route_pairs = [
            ("/etc/nginx/conf.d/sandbox-app.conf", "app.example.com"),
            ("/etc/nginx/sites-available/panel-site.conf", "other.example.com"),
        ]
        self.assertEqual(
            base_find_conflicts(["app.example.com"], route_pairs),
            current_nginx.find_conflicts(["app.example.com"], route_pairs),
        )

        # Conflict check with fallback route: exact documented divergence
        fallback_route = ("/etc/nginx/conf.d/sandbox-coverage-example.com.conf", "*.example.com")
        # Base code reported conflict because it had no fallback exemption
        old_conflicts = base_find_conflicts(["app.example.com"], [fallback_route])
        self.assertEqual(len(old_conflicts), 1)

        # Current code exempts exact route from conflicting with its own fallback
        new_conflicts = current_nginx.find_conflicts(["app.example.com"], [fallback_route])
        self.assertEqual(new_conflicts, [])

        # Call summarize and verify consistency
        sample_facts = {
            "nginx_installed": True,
            "active": True,
            "version": [1, 24, 0],
            "config_valid": True,
            "routes": [
                {
                    "file": "/etc/nginx/conf.d/sandbox-test.conf",
                    "hostnames": ["test.asb.bd"],
                    "readable": True,
                    "mode": "0o600",
                    "loaded": True,
                    "certificates": [{"present": True, "expired": False, "renewal_overdue": False, "issuer": "origin_ca"}],
                }
            ],
        }
        base_sum = base_summarize(sample_facts)
        new_sum = current_nginx.summarize(sample_facts)
        self.assertEqual(base_sum["nginx_version"], new_sum["nginx_version"])
        self.assertEqual(base_sum["config_valid"], new_sum["config_valid"])
        self.assertEqual(base_sum["certificate_problems"], new_sum["certificate_problems"])

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
        # J9 / K6: Real probe test with nginx -T dump containing fallback, 2 exact routes, and 1 independent conflict
        with tempfile.TemporaryDirectory() as td:
            bin_dir = os.path.join(td, "bin")
            os.makedirs(bin_dir, exist_ok=True)
            conf_dir = os.path.join(td, "conf.d")
            os.makedirs(conf_dir, exist_ok=True)

            fallback_file = os.path.join(conf_dir, "sandbox-coverage-asb.bd.conf")
            app1_file = os.path.join(conf_dir, "sandbox-app1.asb.bd.conf")
            app2_file = os.path.join(conf_dir, "sandbox-app2.asb.bd.conf")
            dup1_file = os.path.join(conf_dir, "sandbox-dup1.other.net.conf")
            dup2_file = os.path.join(conf_dir, "sandbox-dup2.other.net.conf")

            files = [
                (fallback_file, "*.asb.bd"),
                (app1_file, "app1.asb.bd"),
                (app2_file, "app2.asb.bd"),
                (dup1_file, "dup.other.net"),
                (dup2_file, "dup.other.net"),
            ]

            dump_parts = []
            for p, h in files:
                with open(p, "w") as f:
                    f.write(f"server {{\n    server_name {h};\n    listen 80;\n}}\n")
                os.chmod(p, 0o600)
                dump_parts.append(f"# configuration file {p}:\nserver {{\n    listen 80;\n    server_name {h};\n}}\n")

            dump_file = os.path.join(td, "dump.txt")
            with open(dump_file, "w") as f:
                f.write("".join(dump_parts))

            fake_nginx = os.path.join(bin_dir, "nginx")
            with open(fake_nginx, "w") as f:
                f.write(f"""#!{sys.executable}
import sys
arg = sys.argv[1] if len(sys.argv) > 1 else ""
if arg == "-v":
    print("nginx version: nginx/1.24.0")
elif arg == "-V":
    print("nginx version: nginx/1.24.0 --with-http_realip_module")
elif arg == "-t":
    exit(0)
elif arg == "-T":
    with open({repr(dump_file)}) as f:
        sys.stdout.write(f.read())
    exit(0)
exit(0)
""")
            os.chmod(fake_nginx, 0o755)

            # Execute real _PROBE_PROGRAM
            program = current_nginx._PROBE_PROGRAM % {
                "conf_dir": conf_dir,
                "prefix": "sandbox-",
                "shared": current_nginx._SHARED_SOURCE,
                "marker_path": current_nginx.MARKER_PATH,
                "overdue": current_nginx.RENEWAL_OVERDUE_DAYS,
            }
            env = synthetic_environment()
            old_path = env.get("PATH", "")
            env["PATH"] = f"{bin_dir}:{old_path}"
            res = subprocess.run(
                [sys.executable, "-c", program],
                input=json.dumps({"declared": ["app1.asb.bd", "app2.asb.bd", "dup.other.net"], "own_file": None}),
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(res.returncode, 0, f"Probe failed: {res.stderr}")
            probe_facts = json.loads(res.stdout)

            routes = probe_facts.get("routes", [])
            self.assertEqual(len(routes), 5)

            # Find fallback route
            fallback_route = next(r for r in routes if r["file"] == fallback_file)
            self.assertIn("*.asb.bd", fallback_route["hostnames"])
            self.assertEqual(fallback_route.get("coverage_zone"), "asb.bd")

            # Fallback route conflicts: must NOT report exact app1 or app2 as conflicts
            fallback_conflicts = fallback_route.get("conflicts", [])
            self.assertEqual(fallback_conflicts, [], f"Expected 0 conflicts in fallback route, got: {fallback_conflicts}")

            # Exact routes must have no conflicts
            app1_route = next(r for r in routes if r["file"] == app1_file)
            self.assertEqual(app1_route["conflicts"], [])
            app2_route = next(r for r in routes if r["file"] == app2_file)
            self.assertEqual(app2_route["conflicts"], [])

            # Independent overlapping conflict MUST be reported
            dup1_route = next(r for r in routes if r["file"] == dup1_file)
            dup1_conflicts = dup1_route.get("conflicts", [])
            self.assertEqual(len(dup1_conflicts), 1)
            self.assertEqual(dup1_conflicts[0]["server_name"], "dup.other.net")
            self.assertEqual(dup1_conflicts[0]["file"], dup2_file)

            dup2_route = next(r for r in routes if r["file"] == dup2_file)
            dup2_conflicts = dup2_route.get("conflicts", [])
            self.assertEqual(len(dup2_conflicts), 1)
            self.assertEqual(dup2_conflicts[0]["server_name"], "dup.other.net")
            self.assertEqual(dup2_conflicts[0]["file"], dup1_file)

            # Summarize reports no certificate problems
            summary = current_nginx.summarize(probe_facts)
            self.assertEqual(summary.get("certificate_problems"), [])

            # The real conflict survives summarize, with no fallback exceptions in it.
            conflict_files = {row["route_file"] for row in summary["conflicts"]}
            self.assertEqual(conflict_files, {dup1_file, dup2_file})
            self.assertTrue(all(row["server_name"] == "dup.other.net" for row in summary["conflicts"]))

            # Edge health follows those facts: unhealthy with the conflict, healthy without.
            def edge_health(report):
                ready = dict(report, nginx_active=True, config_valid=True, unloaded_routes=[],
                             certificate_problems=[])
                args = types.SimpleNamespace(name="r1", remove_route=None, action="edge")
                out = io.StringIO()
                with patch.object(remote_cmd.sr, "get_remote", return_value={"front_door": "nginx"}), \
                        patch.object(current_nginx, "status", return_value=ready), \
                        contextlib.redirect_stdout(out):
                    remote_cmd._cmd_edge(args, as_json=True)
                return json.loads(out.getvalue())["healthy"]

            self.assertIs(edge_health(summary), False)
            self.assertIs(edge_health(dict(summary, conflicts=[])), True)

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
            self.assertIn("re-apply to reissue the Origin CA certificate", prob_msg)

    # 23. Local nginx configuration validation with throwaway cert
    def test_23_local_nginx_configuration_validation(self):
        nginx_bin = shutil.which("nginx") or "/opt/homebrew/bin/nginx"
        if not os.path.exists(nginx_bin):
            self.skipTest("nginx binary not available on host")

        cert_path = os.path.join(self.td, "cert.pem")
        key_path = os.path.join(self.td, "key.pem")
        cmd = f'openssl req -x509 -newkey rsa:2048 -keyout {key_path} -out {cert_path} -days 1 -nodes -subj "/CN=*.asb.bd"'
        subprocess.run(cmd, shell=True, check=True, capture_output=True, env=synthetic_environment({"HOME": self.td}))

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

        r = subprocess.run([nginx_bin, "-t", "-c", nginx_conf], capture_output=True, text=True, env=synthetic_environment({"HOME": self.td}))
        self.assertEqual(r.returncode, 0, f"nginx -t failed:\nstdout: {r.stdout}\nstderr: {r.stderr}")
        self.assertIn("syntax is ok", r.stderr)
        self.assertIn("test is successful", r.stderr)

    # 24. Origin probe on real local TLS socket
    def test_24_origin_probe_loopback_tls_socket(self):
        cert_path = os.path.join(self.td, "cert.pem")
        key_path = os.path.join(self.td, "key.pem")
        cmd = f'openssl req -x509 -newkey rsa:2048 -keyout {key_path} -out {cert_path} -days 1 -nodes -subj "/CN=*.asb.bd"'
        subprocess.run(cmd, shell=True, check=True, capture_output=True, env=synthetic_environment({"HOME": self.td}))

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
            return subprocess.run(cmd, shell=True, capture_output=True, text=True, env=synthetic_environment())

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
            from tests.subprocess_support import synthetic_environment
            env = synthetic_environment({'SANDBOX_HOME': self.td, 'HOME': self.td})
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



    # J14 / K7. Generic error, executed: all paths and methods return generic error via real local nginx
    def test_fallback_all_paths_and_methods_return_generic_error(self):
        import http.client, socket
        from sandbox.hosting.zone_coverage.fallback import render_fallback_conf, FALLBACK_BODY

        nginx_bin = shutil.which("nginx") or next(
            (p for p in ("/opt/homebrew/bin/nginx", "/usr/local/bin/nginx", "/usr/sbin/nginx")
             if os.path.exists(p)), None)
        if not nginx_bin:
            self.skipTest("nginx not installed locally")

        # Registered first so it runs last: nginx stops before its prefix goes away.
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        td = holder.name

        def get_port():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                return s.getsockname()[1]
        h_port, s_port = get_port(), get_port()

        cert_path = os.path.join(td, "cert.pem")
        key_path = os.path.join(td, "key.pem")
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", key_path,
            "-out", cert_path, "-days", "1", "-nodes", "-subj", "/CN=*.asb.bd"
        ], check=True, capture_output=True, env=synthetic_environment({"HOME": td}))

        fb_conf = render_fallback_conf(
            "asb.bd", cert_path=cert_path, key_path=key_path,
            http_port=h_port, https_port=s_port, http2_directive=True,
        )
        # Test-only: keep the listeners on loopback. The rule under test is unchanged.
        for port in (h_port, s_port):
            self.assertIn(f"listen {port}", fb_conf)
            fb_conf = fb_conf.replace(f"listen {port}", f"listen 127.0.0.1:{port}")
        fb_path = os.path.join(td, "fallback.conf")
        with open(fb_path, "w") as f:
            f.write(fb_conf)

        # A decoy default server stands in for a panel site, so a pass proves the fallback
        # was selected by name rather than being the only server present.
        main_conf = os.path.join(td, "nginx.conf")
        with open(main_conf, "w") as f:
            f.write(f"""
daemon off;
error_log {td}/error.log;
pid {td}/nginx.pid;
events {{ worker_connections 64; }}
http {{
    access_log {td}/access.log;
    client_body_temp_path {td}/client_body;
    proxy_temp_path {td}/proxy;
    fastcgi_temp_path {td}/fastcgi;
    uwsgi_temp_path {td}/uwsgi;
    scgi_temp_path {td}/scgi;
    server {{
        listen 127.0.0.1:{h_port} default_server;
        listen 127.0.0.1:{s_port} ssl default_server;
        server_name _;
        ssl_certificate {cert_path};
        ssl_certificate_key {key_path};
        return 418 "decoy";
    }}
    include {fb_path};
}}
""")

        env = synthetic_environment({"HOME": td})
        t_res = subprocess.run([nginx_bin, "-t", "-c", main_conf, "-p", td], capture_output=True, text=True, env=env)
        self.assertEqual(t_res.returncode, 0, f"nginx -t failed:\n{t_res.stderr}")

        proc = subprocess.Popen([nginx_bin, "-c", main_conf, "-p", td], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def _stop():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        self.addCleanup(_stop)

        deadline = time.time() + 10
        ready = False
        while time.time() < deadline and not ready:
            try:
                for port in (h_port, s_port):
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                ready = True
            except OSError:
                time.sleep(0.05)
        self.assertTrue(ready, "nginx did not become ready within 10s")

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        def request(scheme, host, method, path):
            port = h_port if scheme == "http" else s_port
            raw = socket.create_connection(("127.0.0.1", port), timeout=5)
            sock = raw if scheme == "http" else ssl_ctx.wrap_socket(raw, server_hostname=host)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.sock = sock
            try:
                body_in = b"payload" if method in ("POST", "PUT", "PATCH") else None
                conn.request(method, path, body=body_in, headers={"Host": host})
                resp = conn.getresponse()
                return resp.status, resp.getheader("X-Sandbox-Fallback"), resp.getheader("X-Robots-Tag"), resp.read()
            finally:
                conn.close()

        methods = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
        paths = ["/", "/robots.txt", "/.well-known/acme-challenge/x", current_nginx.MARKER_PATH]
        hosts = ["sb-x.asb.bd", "a.b.asb.bd"]
        for scheme in ("http", "https"):
            for host in hosts:
                for path in paths:
                    for method in methods:
                        with self.subTest(scheme=scheme, host=host, path=path, method=method):
                            status, marker, robots, body = request(scheme, host, method, path)
                            self.assertEqual(status, 404)
                            self.assertEqual(marker, "1")
                            self.assertEqual(robots, "noindex, nofollow")
                            self.assertEqual(body.decode("utf-8"), "" if method == "HEAD" else FALLBACK_BODY)

        # The documented limit: a host outside the zone is not caught, and goes to the default.
        for scheme in ("http", "https"):
            with self.subTest(scheme=scheme, host="outside"):
                status, marker, _, _ = request(scheme, "example.org", "GET", "/")
                self.assertEqual(status, 418)
                self.assertIsNone(marker)

    def test_subprocess_calls_supply_explicit_env(self):
        import ast
        import os

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        test_files = [
            os.path.join(repo_root, 'tests', 'test_zone_coverage_surface.py'),
            os.path.join(repo_root, 'tests', 'test_zone_coverage_matrix.py'),
            os.path.join(repo_root, 'tests', 'test_front_door_nginx.py'),
        ]

        target_funcs = {'run', 'Popen', 'check_output'}
        violations = []

        for tf in test_files:
            with open(tf, 'r') as f:
                tree = ast.parse(f.read(), filename=tf)

            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    is_subproc = False
                    if isinstance(node.func, ast.Attribute) and node.func.attr in target_funcs:
                        if isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess':
                            is_subproc = True
                    if is_subproc:
                        has_env = any(kw.arg == 'env' for kw in node.keywords)
                        if not has_env:
                            violations.append(f"{os.path.basename(tf)}:{node.lineno}")

        self.assertEqual(violations, [], f"Subprocess calls without env=: {violations}")


if __name__ == "__main__":
    unittest.main()
