import asyncio
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp" / "wp-server"))
import tailnet_access as access  # noqa: E402

try:
    import starlette  # noqa: F401
    import httpx  # noqa: F401
    HAS_STARLETTE = True
except ImportError:
    HAS_STARLETTE = False

PEER = "100.64.0.5"
WHOIS = json.dumps({"UserProfile": {"LoginName": "person@example.test"},
                    "Node": {"ComputedName": "laptop"}})
SECRET = "bearer-token-sentinel"


class FakeRun:
    def __init__(self, stdout=WHOIS, returncode=0, error=None):
        self.stdout, self.returncode, self.error, self.calls = stdout, returncode, error, []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(stdout=self.stdout, returncode=self.returncode)


class Base(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "logs" / "control-access.jsonl"

    def attribution(self, run):
        return access.Attribution(access.AccessLog(self.path), access.Whois(run=run))

    def records(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def observe(self, attribution, *, client=PEER, method="GET", path="/diagnostics",
                body=b"", status=200, drain=True):
        async def read():
            return body
        request = SimpleNamespace(
            client=SimpleNamespace(host=client) if client else None, method=method,
            url=SimpleNamespace(path=path),
            headers={"authorization": f"Bearer {SECRET}", "content-length": str(len(body))},
            body=read)
        async def call_next(_request):
            return SimpleNamespace(status_code=status)
        response = asyncio.run(attribution.observe(request, call_next))
        if drain:
            attribution.drain()
        return response


class BlockedRun(FakeRun):
    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def __call__(self, argv, **kwargs):
        self.release.wait(5)
        return super().__call__(argv, **kwargs)


class ModeTest(unittest.TestCase):
    def test_only_tailnet_addresses_qualify(self):
        for value in ("100.64.0.1", "100.127.255.254", "fd7a:115c:a1e0::1"):
            self.assertTrue(access.is_tailnet_address(value), value)
        for value in ("127.0.0.1", "203.0.113.5", "100.128.0.1", "::1", "", "not-an-ip", None,
                      "100.64.0.1; rm -rf /", "-v"):
            self.assertFalse(access.is_tailnet_address(value), value)

    def test_https_mode_builds_no_attribution(self):
        self.assertIsNone(access.build_attribution("127.0.0.1", "/tmp/unused"))

    def test_tailnet_mode_logs_under_the_sandbox_home(self):
        built = access.build_attribution("100.64.0.9", "/srv/sandbox")
        self.assertEqual(built.log.path, Path("/srv/sandbox/runtime/logs/control-access.jsonl"))


class WhoisTest(unittest.TestCase):
    def test_resolves_login_and_node_with_a_fixed_argv(self):
        run = FakeRun()
        self.assertEqual(access.Whois(run=run).lookup(PEER),
                         {"login": "person@example.test", "node": "laptop", "reason": None})
        argv, kwargs = run.calls[0]
        self.assertEqual(argv, ["tailscale", "whois", "--json", PEER])
        self.assertFalse(kwargs.get("shell", False))
        self.assertLessEqual(kwargs["timeout"], 2)

    def test_failures_are_unknown_with_a_bounded_reason(self):
        cases = {
            "timeout": FakeRun(error=subprocess.TimeoutExpired("tailscale", 2)),
            "not_installed": FakeRun(error=FileNotFoundError()),
            "whois_failed": FakeRun(returncode=1, stdout=""),
            "bad_response": FakeRun(stdout="not json"),
        }
        for reason, run in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(access.Whois(run=run).lookup(PEER),
                                 {"login": "unknown", "node": None, "reason": reason})

    def test_response_without_a_login_is_a_bad_response(self):
        result = access.Whois(run=FakeRun(stdout=json.dumps({"Node": {}}))).lookup(PEER)
        self.assertEqual(result["reason"], "bad_response")

    def test_hostile_field_values_are_cleaned_and_bounded(self):
        stdout = json.dumps({"UserProfile": {"LoginName": "a\nb" + "x" * 500},
                             "Node": {"ComputedName": "n\x07"}})
        result = access.Whois(run=FakeRun(stdout=stdout)).lookup(PEER)
        self.assertNotIn("\n", result["login"])
        self.assertLessEqual(len(result["login"]), 256)
        self.assertEqual(result["node"], "n")

    def test_invalid_address_never_reaches_the_subprocess(self):
        run = FakeRun()
        for value in ("--help", "203.0.113.5", "", None):
            self.assertEqual(access.Whois(run=run).lookup(value)["reason"], "invalid_address")
        self.assertEqual(run.calls, [])

    def test_cache_prevents_a_second_process_inside_the_window(self):
        run, now = FakeRun(), [0.0]
        whois = access.Whois(run=run, clock=lambda: now[0], ttl_seconds=60)
        whois.lookup(PEER)
        now[0] = 59
        whois.lookup(PEER)
        self.assertEqual(len(run.calls), 1)
        now[0] = 61
        whois.lookup(PEER)
        self.assertEqual(len(run.calls), 2)

    def test_cache_stays_bounded(self):
        whois = access.Whois(run=FakeRun(), max_entries=2)
        for suffix in (1, 2, 3, 4):
            whois.lookup(f"100.64.0.{suffix}")
        self.assertLessEqual(len(whois.entries), 2)


class AccessLogTest(Base):
    def test_file_and_directory_are_private(self):
        access.AccessLog(self.path).write({"login": "a"})
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.path.parent).st_mode), 0o700)

    def test_rotation_keeps_a_bounded_number_of_files(self):
        log = access.AccessLog(self.path, max_bytes=40, backups=2)
        for index in range(6):
            log.write({"n": index, "pad": "x" * 20})
        self.assertEqual(self.records()[-1]["n"], 5)
        self.assertTrue(Path(f"{self.path}.1").exists())
        self.assertTrue(Path(f"{self.path}.2").exists())
        self.assertFalse(Path(f"{self.path}.3").exists())
        self.assertEqual(stat.S_IMODE(os.stat(f"{self.path}.1").st_mode), 0o600)

    def test_a_symlink_at_the_log_path_is_not_followed(self):
        self.path.parent.mkdir(mode=0o700)
        target = self.path.parent / "elsewhere"
        target.write_text("original")
        self.path.symlink_to(target)
        access.AccessLog(self.path).write({"login": "a"})
        self.assertEqual(target.read_text(), "original")

    def test_an_existing_file_is_made_private(self):
        self.path.parent.mkdir(mode=0o700)
        self.path.write_text("")
        os.chmod(self.path, 0o644)
        access.AccessLog(self.path).write({"login": "a"})
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_write_failure_is_swallowed(self):
        self.path.parent.parent.mkdir(exist_ok=True)
        self.path.parent.write_text("a file where the directory should be")
        access.AccessLog(self.path).write({"login": "a"})


class ObserveTest(Base):
    def test_records_the_login_and_request(self):
        response = self.observe(self.attribution(FakeRun()), status=201)
        self.assertEqual(response.status_code, 201)
        record = self.records()[0]
        self.assertEqual((record["login"], record["node"], record["method"], record["path"],
                          record["status"]),
                         ("person@example.test", "laptop", "GET", "/diagnostics", 201))
        self.assertTrue(record["time"].endswith("Z"))

    def test_the_response_does_not_wait_for_the_lookup(self):
        run = BlockedRun()
        attribution = self.attribution(run)
        self.assertEqual(self.observe(attribution, status=202, drain=False).status_code, 202)
        self.assertFalse(self.path.exists())
        run.release.set()
        attribution.drain()
        self.assertEqual(self.records()[0]["login"], "person@example.test")

    def test_a_full_queue_drops_records_and_still_serves(self):
        run = BlockedRun()
        attribution = access.Attribution(access.AccessLog(self.path), access.Whois(run=run), max_queue=1)
        for _ in range(5):
            self.assertEqual(self.observe(attribution, drain=False).status_code, 200)
        run.release.set()
        attribution.drain()
        self.assertLess(len(self.records()), 5)

    def test_whois_failure_still_serves_and_records_unknown(self):
        for error in (subprocess.TimeoutExpired("tailscale", 2), FileNotFoundError()):
            response = self.observe(self.attribution(FakeRun(error=error)))
            self.assertEqual(response.status_code, 200)
        records = self.records()
        self.assertEqual([item["login"] for item in records], ["unknown", "unknown"])
        self.assertEqual([item["reason"] for item in records], ["timeout", "not_installed"])

    def test_log_failure_does_not_fail_the_request(self):
        self.path.parent.parent.mkdir(exist_ok=True)
        self.path.parent.write_text("blocked")
        self.assertEqual(self.observe(self.attribution(FakeRun())).status_code, 200)

    def test_missing_client_is_unknown(self):
        self.observe(self.attribution(FakeRun()), client=None)
        self.assertEqual(self.records()[0]["reason"], "invalid_address")

    def test_tool_name_is_recorded_without_arguments_or_token(self):
        body = json.dumps({"jsonrpc": "2.0", "method": "tools/call", "id": 1,
                           "params": {"name": "wp_status",
                                      "arguments": {"password": "argument-sentinel"}}}).encode()
        self.observe(self.attribution(FakeRun()), method="POST", path="/mcp", body=body)
        raw = self.path.read_text()
        self.assertEqual(self.records()[0]["tool"], "wp_status")
        self.assertNotIn("argument-sentinel", raw)
        self.assertNotIn(SECRET, raw)

    def test_bodies_on_other_routes_are_not_read_or_logged(self):
        self.observe(self.attribution(FakeRun()), method="POST", path="/wp-cli",
                     body=b'{"method":"tools/call","params":{"name":"body-sentinel"}}')
        raw = self.path.read_text()
        self.assertNotIn("body-sentinel", raw)
        self.assertNotIn("tool", self.records()[0])


class ToolNameTest(unittest.TestCase):
    def test_only_a_well_formed_tool_call_yields_a_name(self):
        call = {"method": "tools/call", "params": {"name": "wp_status"}}
        self.assertEqual(access.tool_from_body(json.dumps(call).encode()), "wp_status")
        for body in (b"", b"not json", b"[]", b'{"method":"initialize"}',
                     b'{"method":"tools/call","params":{"name":"a b\\n"}}',
                     b'{"method":"tools/call","params":{"name":5}}'):
            self.assertIsNone(access.tool_from_body(body), body)


@unittest.skipUnless(HAS_STARLETTE, "needs the MCP service dependencies")
class RealMiddlewareTest(Base):
    def test_the_handler_still_receives_the_body(self):
        from starlette.applications import Starlette
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        attribution = self.attribution(FakeRun())

        class Middleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                return await attribution.observe(request, call_next)

        async def echo(request):
            return JSONResponse({"received": (await request.body()).decode()})

        app = Starlette(routes=[Route("/mcp", echo, methods=["POST"])])
        app.add_middleware(Middleware)
        body = json.dumps({"method": "tools/call", "params": {"name": "wp_status"}})
        client = TestClient(app, client=(PEER, 50000))
        response = client.post("/mcp", content=body)
        self.assertEqual(response.json(), {"received": body})
        attribution.drain()
        self.assertEqual(self.records()[0]["tool"], "wp_status")
        self.assertEqual(self.records()[0]["login"], "person@example.test")


if __name__ == "__main__":
    unittest.main()
