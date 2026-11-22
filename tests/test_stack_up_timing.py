"""Stack-up speed work: phase markers, one home lookup per dispatch, fewer
Cloudflare calls, and edge verification that overlaps waits."""
import subprocess
import threading
import time
import unittest
import urllib.error
import io
from unittest.mock import ANY, MagicMock, patch

import sandbox.core._cloudflare as cloudflare
import sandbox.core._remote as remote
import sandbox.commands.hosting as hosting_cmd
from sandbox.hosting.phase_timing import PhaseClock
from tests.test_hosting import _HostingOwnerFixture


class _Ticks:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0)


class TestPhaseClock(unittest.TestCase):
    def test_lap_finishes_the_running_phase_with_its_elapsed_time(self):
        lines = []
        clock = PhaseClock(lines.append, scope="apply", monotonic=_Ticks(10.0, 12.5, 12.5, 13.0),
                           stamp=lambda: "T")
        clock.lap("edge_dns")
        clock.lap("edge_verify")
        clock.finish()
        self.assertEqual(lines, [
            "[Sandbox] apply phase=edge_dns event=started at=T",
            "[Sandbox] apply phase=edge_dns event=finished at=T elapsed=2.5s",
            "[Sandbox] apply phase=edge_verify event=started at=T",
            "[Sandbox] apply phase=edge_verify event=finished at=T elapsed=0.5s",
        ])

    def test_finish_twice_reports_once_and_no_emitter_is_silent(self):
        lines = []
        clock = PhaseClock(lines.append, monotonic=_Ticks(1.0, 2.0), stamp=lambda: "T")
        clock.lap("secrets")
        clock.finish()
        clock.finish()
        self.assertEqual(len(lines), 2)
        PhaseClock(None).lap("secrets")  # no output, no error

    def test_names_are_fixed_identifiers(self):
        with self.assertRaises(ValueError):
            PhaseClock(None).lap("edge dns; rm -rf /")
        with self.assertRaises(ValueError):
            PhaseClock(None, scope="Apply Scope")

    def test_a_closed_stream_never_fails_the_command(self):
        def broken(_text):
            raise OSError("closed")
        clock = PhaseClock(broken)
        clock.lap("submit")
        clock.finish()


class TestRemoteHomeCache(unittest.TestCase):
    remote_a = {"ssh": "deploy@203.0.113.10"}
    remote_b = {"ssh": "deploy@203.0.113.11"}

    def run_home(self, *_args, **_kwargs):
        self.calls += 1
        return subprocess.CompletedProcess(["ssh"], 0, "/home/deploy/sandbox\n", "")

    def setUp(self):
        self.calls = 0

    def test_one_lookup_per_endpoint_inside_the_block(self):
        with patch.object(remote, "ssh_run", side_effect=self.run_home):
            with remote.remote_home_cache():
                homes = {remote.resolve_sandbox_home(self.remote_a) for _ in range(5)}
                self.assertEqual(remote.remote_sb_path(self.remote_a), "/home/deploy/sandbox/sb-src/sb")
                remote.resolve_sandbox_home(self.remote_b)
        self.assertEqual(homes, {"/home/deploy/sandbox"})
        self.assertEqual(self.calls, 2)

    def test_outside_the_block_every_call_asks_the_remote(self):
        with patch.object(remote, "ssh_run", side_effect=self.run_home):
            with remote.remote_home_cache():
                remote.resolve_sandbox_home(self.remote_a)
            remote.resolve_sandbox_home(self.remote_a)
            remote.resolve_sandbox_home(self.remote_a)
        self.assertEqual(self.calls, 3)

    def test_a_failed_lookup_is_not_remembered(self):
        replies = [subprocess.CompletedProcess(["ssh"], 255, "", "timeout"),
                   subprocess.CompletedProcess(["ssh"], 0, "/home/deploy/sandbox\n", "")]
        with patch.object(remote, "ssh_run", side_effect=replies) as ssh:
            with remote.remote_home_cache():
                with self.assertRaises(RuntimeError):
                    remote.resolve_sandbox_home(self.remote_a)
                self.assertEqual(remote.resolve_sandbox_home(self.remote_a), "/home/deploy/sandbox")
        self.assertEqual(ssh.call_count, 2)

    def test_an_invalid_home_is_refused_and_not_remembered(self):
        with patch.object(remote, "ssh_run", return_value=subprocess.CompletedProcess(
                ["ssh"], 0, "relative/home\n", "")) as ssh:
            with remote.remote_home_cache():
                for _ in range(2):
                    with self.assertRaises(ValueError):
                        remote.resolve_sandbox_home(self.remote_a)
        self.assertEqual(ssh.call_count, 2)


class TestAddressRecordCurrent(unittest.TestCase):
    def record(self, **changes):
        value = {"id": "r1", "type": "A", "name": "wp.example.test", "content": "203.0.113.10",
                 "proxied": False, "ttl": cloudflare.DNS_ONLY_TTL_SECONDS,
                 "comment": cloudflare.MANAGED_RECORD_COMMENT}
        value.update(changes)
        return value

    def test_exact_record_needs_no_write(self):
        self.assertTrue(cloudflare.address_record_current(self.record(), "203.0.113.10", proxied=False))
        self.assertTrue(cloudflare.address_record_current(
            self.record(type="AAAA", content="2001:db8::1"), "2001:db8::1", proxied=False))
        self.assertTrue(cloudflare.address_record_current(
            self.record(proxied=True, ttl=1), "203.0.113.10", proxied=True))

    def test_any_difference_means_a_write(self):
        for changed in ({"content": "203.0.113.11"}, {"proxied": True}, {"proxied": "false"},
                        {"ttl": 300}, {"comment": "hand made"}, {"type": "AAAA"}):
            with self.subTest(changed=changed):
                self.assertFalse(cloudflare.address_record_current(
                    self.record(**changed), "203.0.113.10", proxied=False))
        self.assertFalse(cloudflare.address_record_current(None, "203.0.113.10", proxied=False))


class TestZoneLookup(unittest.TestCase):
    def test_each_candidate_is_asked_once_per_apply(self):
        client = MagicMock()

        def zone(name):
            if name == "example.test":
                return {"id": "z1", "name": name}
            raise cloudflare.CloudflareError(f"no Cloudflare zone found for {name}", "zone_not_found")
        client.zone.side_effect = zone
        lookup = hosting_cmd._ZoneLookup(client)
        for host in ("a.example.test", "b.example.test", "a.example.test", "example.test"):
            self.assertEqual(hosting_cmd._zone_for_hostname(lookup, host)["id"], "z1")
        self.assertEqual(sorted(call.args[0] for call in client.zone.call_args_list),
                         ["a.example.test", "b.example.test", "example.test"])

    def test_a_provider_error_is_asked_again(self):
        client = MagicMock()
        client.zone.side_effect = [cloudflare.CloudflareError("rate limited", "provider_rate_limited"),
                                   {"id": "z1", "name": "example.test"}]
        lookup = hosting_cmd._ZoneLookup(client)
        with self.assertRaises(cloudflare.CloudflareError):
            lookup.zone("example.test")
        self.assertEqual(lookup.zone("example.test")["id"], "z1")


def _ok_response():
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    return response


class TestEdgeVerification(unittest.TestCase):
    routes = [{"hostname": f"{name}.example.test", "mode": "serve"} for name in ("wp", "hub", "admin")]

    @patch("sandbox.commands.hosting.urllib.request.build_opener")
    def test_routes_are_probed_together(self, build_opener):
        barrier = threading.Barrier(len(self.routes), timeout=5)

        def open_(request, timeout=None):
            barrier.wait()  # every route must be in flight at once, or this times out
            return _ok_response()
        build_opener.return_value.open.side_effect = open_
        with patch("sandbox.commands.hosting.time.sleep") as sleep:
            hosting_cmd._verify_edge(self.routes)
        sleep.assert_not_called()

    @patch("sandbox.commands.hosting.urllib.request.build_opener")
    def test_the_first_failure_in_declared_order_is_reported(self, build_opener):
        def open_(request, timeout=None):
            if "wp." in request.full_url:
                time.sleep(0.05)  # the earlier route fails last
            if "hub." in request.full_url:
                return _ok_response()
            raise urllib.error.HTTPError(request.full_url, 502, "Bad Gateway", {}, io.BytesIO())
        build_opener.return_value.open.side_effect = open_
        with patch("sandbox.commands.hosting.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "edge verification failed for wp.example.test"):
                hosting_cmd._verify_edge(self.routes)

    @patch("sandbox.commands.hosting.urllib.request.build_opener")
    def test_dns_grace_counts_the_time_since_the_records_were_written(self, build_opener):
        build_opener.return_value.open.return_value = _ok_response()
        fresh = frozenset({"wp.example.test"})
        grace = hosting_cmd._NEW_DNS_RECORD_GRACE_SECONDS
        now = 1000.0
        with patch("sandbox.commands.hosting.time.monotonic", return_value=now), \
                patch("sandbox.commands.hosting.time.sleep") as sleep:
            hosting_cmd._verify_edge(self.routes, fresh_hostnames=fresh, fresh_since=now - 8)
            hosting_cmd._verify_edge(self.routes, fresh_hostnames=fresh, fresh_since=now - grace - 1)
            hosting_cmd._verify_edge(self.routes, fresh_hostnames=fresh)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [grace - 8, grace])


class TestApplyEdgeWork(unittest.TestCase):
    def matching(self, fixture):
        wanted = {}
        for item in fixture.runtime["records"]:
            wanted.setdefault(item["hostname"], []).append({
                "id": f"{item['hostname']}-{len(item['address'])}",
                "type": "AAAA" if ":" in item["address"] else "A", "name": item["hostname"],
                "content": item["address"], "proxied": bool(fixture.validated["cloudflare"]["proxied"]),
                "ttl": 1 if fixture.validated["cloudflare"]["proxied"] else cloudflare.DNS_ONLY_TTL_SECONDS,
                "comment": cloudflare.MANAGED_RECORD_COMMENT})
        return wanted

    def test_unchanged_records_are_not_rewritten(self):
        fixture = _HostingOwnerFixture(self)
        existing = self.matching(fixture)
        fixture.client.records.side_effect = lambda _zone, hostname: list(existing.get(hostname, []))
        fixture.apply()
        fixture.client.upsert_address.assert_not_called()
        self.assertNotIn("fresh_hostnames", fixture.edge.call_args.kwargs)
        saved = fixture.repository.load()["hosts"][fixture.key]
        self.assertEqual(saved["edge"]["state"], "ready")
        self.assertEqual(len(saved["records"]), len(fixture.runtime["records"]))

    def test_a_changed_record_is_written(self):
        fixture = _HostingOwnerFixture(self)
        existing = self.matching(fixture)
        first = fixture.runtime["records"][0]["hostname"]
        existing[first][0] = dict(existing[first][0], content="192.0.2.99")
        fixture.client.records.side_effect = lambda _zone, hostname: list(existing.get(hostname, []))
        fixture.apply()
        self.assertEqual([call.args[1] for call in fixture.client.upsert_address.call_args_list], [first])

    def test_apply_reports_timed_phases(self):
        fixture = _HostingOwnerFixture(self)
        lines = []
        fixture.apply(progress=lines.append)
        finished = [line.split()[2] for line in lines
                    if line.startswith("[Sandbox] apply phase=") and "event=finished" in line]
        for phase in ("secrets", "source_artifact", "admission", "edge_preflight", "source_push",
                      "source_reset", "runtime_health", "runtime_observation", "edge_certificate",
                      "edge_route", "edge_dns", "edge_after_dns", "edge_verify", "edge_finalize",
                      "delivery_record", "mail_name"):
            self.assertIn(f"phase={phase}", finished)
        self.assertTrue(all(" elapsed=" in line for line in lines if "event=finished" in line
                            and line.startswith("[Sandbox] apply phase=")))
        self.assertEqual(fixture.edge.call_args.kwargs.get("fresh_since"), ANY)


if __name__ == "__main__":
    unittest.main()
