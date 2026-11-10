import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp" / "wp-server"))
import tailnet_access as access  # noqa: E402

try:
    import starlette  # noqa: F401
    import httpx  # noqa: F401
    HAS_STARLETTE = True
except ImportError:
    HAS_STARLETTE = False

MEMBER = "100.64.0.5"
TOKEN = "control-token-sentinel"
WHOIS = json.dumps({"UserProfile": {"LoginName": "teammate@example.test"},
                    "Node": {"ComputedName": "laptop"}})


class FakeRun:
    def __init__(self, stdout=WHOIS, returncode=0):
        self.stdout, self.returncode, self.calls = stdout, returncode, 0

    def __call__(self, argv, **kwargs):
        self.calls += 1
        return SimpleNamespace(stdout=self.stdout, returncode=self.returncode)


class Home(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.file = self.home / "runtime" / "control-access.json"

    def switch(self, value=True, mode=0o600, content=None):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(content if content is not None else json.dumps({"tailnet_members": value}))
        os.chmod(self.file, mode)


class MemberAccessTest(Home):
    def test_on_unless_the_owner_turned_it_off(self):
        members = access.MemberAccess(self.home)
        self.assertTrue(members.enabled())
        self.switch(False)
        self.assertFalse(members.enabled())

    def test_a_change_takes_effect_without_a_restart(self):
        members = access.MemberAccess(self.home)
        self.switch(False)
        self.assertFalse(members.enabled())
        self.switch(True)
        self.assertTrue(members.enabled())

    def test_anything_unexpected_means_off(self):
        cases = {"writable by others": dict(mode=0o666), "not JSON": dict(content="yes"),
                 "not an object": dict(content="[true]"), "a truthy string": dict(content='{"tailnet_members": "true"}')}
        for label, kwargs in cases.items():
            with self.subTest(label):
                self.switch(**kwargs)
                self.assertFalse(access.MemberAccess(self.home).enabled())

    def test_a_symlink_is_not_followed(self):
        target = self.home / "elsewhere.json"
        target.write_text(json.dumps({"tailnet_members": True}))
        os.chmod(target, 0o600)
        self.file.parent.mkdir(parents=True)
        self.file.symlink_to(target)
        self.assertFalse(access.MemberAccess(self.home).enabled())


class ControlGateTest(Home):
    def gate(self, run=None, members=True):
        return access.ControlGate(TOKEN, access.MemberAccess(self.home) if members else None,
                                  access.Whois(run=run or FakeRun()))

    def admit(self, gate, authorization=None, peer=MEMBER):
        import asyncio
        return asyncio.run(gate.admit(authorization, peer))

    def test_the_token_always_works_and_never_needs_a_lookup(self):
        run = FakeRun()
        self.assertEqual(self.admit(self.gate(run), f"Bearer {TOKEN}"), {"via": "token"})
        self.assertEqual(run.calls, 0)

    def test_a_tailnet_member_is_admitted_unless_the_owner_turned_it_off(self):
        gate = self.gate()
        self.assertEqual(self.admit(gate), {"via": "tailnet", "login": "teammate@example.test",
                                            "node": "laptop"})
        self.switch(False)
        self.assertIsNone(self.admit(gate))

    def test_a_wrong_token_from_a_member_still_gets_member_access(self):
        self.switch(True)
        self.assertEqual(self.admit(self.gate(), "Bearer wrong")["via"], "tailnet")

    def test_an_address_tailscale_cannot_resolve_is_refused(self):
        self.switch(True)
        for run in (FakeRun(returncode=1), FakeRun(stdout="{}"), FakeRun(stdout="not json")):
            with self.subTest(run=run.stdout):
                self.assertIsNone(self.admit(self.gate(run)))

    def test_peers_outside_the_tailnet_are_refused_without_a_lookup(self):
        self.switch(True)
        run = FakeRun()
        gate = self.gate(run)
        for peer in ("203.0.113.7", "127.0.0.1", None, "nonsense"):
            with self.subTest(peer=peer):
                self.assertIsNone(self.admit(gate, peer=peer))
        self.assertEqual(run.calls, 0)

    def test_without_member_access_only_the_token_works(self):
        self.switch(True)
        gate = self.gate(members=False)
        self.assertIsNone(self.admit(gate))
        self.assertEqual(self.admit(gate, f"Bearer {TOKEN}"), {"via": "token"})


@unittest.skipUnless(HAS_STARLETTE, "needs starlette and httpx")
class ServerWiringTest(Home):
    def app(self, bind="100.64.0.9"):
        import server
        with mock.patch.dict(os.environ, {"SANDBOX_HOME": str(self.home)}), \
                mock.patch("uvicorn.run") as run:
            server._run_streamable_http(bind, 9174, TOKEN)
        return run.call_args.args[0]

    def get(self, app, *, peer=MEMBER, token=None):
        from starlette.testclient import TestClient
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with mock.patch.object(access.Whois, "_resolve",
                               return_value={"login": "teammate@example.test", "node": "laptop",
                                             "reason": None}), \
                mock.patch("server._hosted_inventory_snapshot",
                           return_value={"ok": True, "inventory_schema": 1, "transport": "control"}):
            return TestClient(app, client=(peer, 50000)).get(
                "/inventory", headers={**headers, "Host": "100.64.0.9:9174"})

    def test_a_member_reaches_the_control_service_unless_turned_off(self):
        app = self.app()
        self.assertEqual(self.get(app).status_code, 200)
        self.switch(False)
        self.assertEqual(self.get(app).status_code, 401)

    def test_the_owner_token_still_works(self):
        self.assertEqual(self.get(self.app(), token=TOKEN).status_code, 200)

    def test_a_public_https_service_never_admits_without_the_token(self):
        self.switch(True)
        app = self.app(bind="127.0.0.1")
        self.assertEqual(self.get(app, peer="127.0.0.1").status_code, 401)


if __name__ == "__main__":
    unittest.main()
