from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sandbox.services.process import ProcessResult


class RecordingProcess:
    def __init__(self):
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        return ProcessResult(tuple(argv), 0, "", "")


class SequencedProcess(RecordingProcess):
    def __init__(self, returncodes):
        super().__init__()
        self.returncodes = iter(returncodes)

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        code = next(self.returncodes)
        return ProcessResult(tuple(argv), code, "", "failed" if code else "")


class Reservation:
    def __init__(self):
        self.address = "127.0.0.54"; self.port = 5300; self.released = False

    def release(self):
        self.released = True


class TestDomainAuthority(unittest.TestCase):
    def test_composition_uses_only_the_fixed_root_verified_dnsmasq_binary(self):
        context = (Path(__file__).parent.parent / "sandbox/application/context.py").read_text()
        domain_block = context.split("def domain_service", 1)[1].split(
            "def ingress_service", 1,
        )[0]
        self.assertIn('Path("/usr/sbin/dnsmasq")', domain_block)
        self.assertIn("details.st_uid == 0", domain_block)
        self.assertNotIn('shutil.which("dnsmasq")', domain_block)

    def test_foreign_udp_endpoint_collision_is_preserved(self):
        import socket
        from sandbox.services.ports import SocketDnsEndpointAllocator

        foreign = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        foreign.bind(("127.0.0.1", 0))
        try:
            with self.assertRaisesRegex(ValueError, "unavailable"):
                SocketDnsEndpointAllocator("127.0.0.1").reserve(foreign.getsockname()[1])
        finally:
            foreign.close()

    def test_generated_config_is_non_forwarding_and_scoped(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        exact = ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/demo::default",), desired={},
        )
        zone = ResolutionBinding.create(
            kind="zone", name="*.site.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/demo::default",), desired={},
        )
        text = DnsmasqAuthority.render_config(
            address="127.0.0.54", port=5300, bindings=(exact, zone),
            pid_file="/tmp/pid", log_file="/tmp/log",
        )
        self.assertIn("no-resolv", text)
        self.assertIn("no-hosts", text)
        self.assertNotIn("server=", text)
        self.assertIn("host-record=demo.test,127.0.0.77", text)
        self.assertIn("address=/site.test/127.0.0.77", text)
        self.assertNotIn("address=/demo.test/", text)

    def test_ensure_preserves_existing_project_bindings_when_adding_a_sibling(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        one = ResolutionBinding.create(
            kind="exact", name="one.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/one::default",), desired={},
        )
        two = ResolutionBinding.create(
            kind="exact", name="two.test", target="127.0.0.78", adapter_id="resolved",
            owners=("/tmp/two::default",), desired={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            process = RecordingProcess()
            authority = DnsmasqAuthority(
                Path(tmp), process=process, binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (123, "boot:1"),
                pid_matches=lambda pid, identity: pid == 123 and identity == "boot:1",
                pid_executable=lambda pid: pid == 123,
                listener_matches=lambda pid, address, port: pid == 123 and address == "127.0.0.54" and port == 5300,
            )
            authority.ensure((one,), address="127.0.0.54", port=5300)
            authority.ensure((one, two), address="127.0.0.54", port=5300)
            config = authority.config_path.read_text()
        self.assertIn("host-record=one.test,127.0.0.77", config)
        self.assertIn("host-record=two.test,127.0.0.78", config)

    def test_active_shared_authority_cannot_be_moved_by_a_racing_plan(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        binding = ResolutionBinding.create(
            kind="exact", name="one.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/one::default",), desired={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            authority = DnsmasqAuthority(
                Path(tmp), process=RecordingProcess(), binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (123, "boot:1"),
                pid_matches=lambda pid, identity: pid == 123 and identity == "boot:1",
                pid_executable=lambda pid: pid == 123,
                listener_matches=lambda pid, address, port: pid == 123 and address == "127.0.0.54" and port == 5300,
            )
            authority.ensure((binding,), address="127.0.0.54", port=5300)
            with self.assertRaisesRegex(RuntimeError, "refusing to move"):
                authority.ensure((binding,), address="127.0.0.55", port=5301)
            state = authority.status()
        self.assertEqual((state["address"], state["port"]), ("127.0.0.54", 5300))

    def test_authority_lock_refuses_symlink_substitution(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        binding = ResolutionBinding.create(
            kind="exact", name="one.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/one::default",), desired={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            authority = DnsmasqAuthority(
                Path(tmp), process=RecordingProcess(), binary="/usr/sbin/dnsmasq",
            )
            target = Path(tmp) / "foreign.lock"
            target.write_text("")
            authority.lock_path.symlink_to(target)
            with self.assertRaises(OSError):
                authority.ensure((binding,), address="127.0.0.54", port=5300)

    def test_udp_tcp_reservation_is_held_through_validation_and_released_at_exec_handoff(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        reservation = Reservation()

        class Process(RecordingProcess):
            def run(self, argv, **kwargs):
                if len(argv) > 1 and argv[1] == "--test":
                    self_case.assertFalse(reservation.released)
                if len(argv) > 1 and argv[1].startswith("--conf-file="):
                    self_case.assertTrue(reservation.released)
                return super().run(argv, **kwargs)

        self_case = self
        binding = ResolutionBinding.create(
            kind="exact", name="one.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/one::default",), desired={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            authority = DnsmasqAuthority(
                Path(tmp), process=Process(), binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (123, "boot:1"),
                pid_matches=lambda *_args: True,
                pid_executable=lambda _pid: True,
                listener_matches=lambda *_args: True,
            )
            authority.ensure((binding,), address="127.0.0.54", port=5300,
                             reservation=reservation)
        self.assertTrue(reservation.released)

    def test_failed_authority_restart_restores_the_previous_project_set(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        one = ResolutionBinding.create(
            kind="exact", name="one.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/one::default",), desired={},
        )
        two = ResolutionBinding.create(
            kind="exact", name="two.test", target="127.0.0.78", adapter_id="resolved",
            owners=("/tmp/two::default",), desired={},
        )
        # first test/start; second test/kill/failed start/restored start
        process = SequencedProcess((0, 0, 0, 0, 1, 0))
        with tempfile.TemporaryDirectory() as tmp:
            authority = DnsmasqAuthority(
                Path(tmp), process=process, binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (123, "boot:1"),
                pid_matches=lambda pid, identity: pid == 123 and identity == "boot:1",
                pid_executable=lambda pid: pid == 123,
                listener_matches=lambda pid, address, port: pid == 123 and address == "127.0.0.54" and port == 5300,
            )
            authority.ensure((one,), address="127.0.0.54", port=5300)
            with self.assertRaisesRegex(RuntimeError, "failed"):
                authority.ensure((one, two), address="127.0.0.54", port=5300)
            config = authority.config_path.read_text()
        self.assertIn("host-record=one.test,127.0.0.77", config)
        self.assertNotIn("two.test", config)

    def test_ensure_is_idempotent_and_final_cleanup_stops_owned_pid(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        binding = ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/demo::default",), desired={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            alive = {"value": True}

            class Process(RecordingProcess):
                def run(self, argv, **kwargs):
                    result = super().run(argv, **kwargs)
                    if tuple(argv)[:2] == ("kill", "-TERM"):
                        alive["value"] = False
                    return result

            process = Process()
            authority = DnsmasqAuthority(
                Path(tmp), process=process, binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (123, "boot:1"),
                pid_matches=lambda pid, identity: alive["value"] and pid == 123 and identity == "boot:1",
                pid_executable=lambda pid: pid == 123,
                listener_matches=lambda pid, address, port: alive["value"] and pid == 123 and address == "127.0.0.54" and port == 5300,
                sleeper=lambda _seconds: None,
            )
            first = authority.ensure((binding,), address="127.0.0.54", port=5300)
            second = authority.ensure((binding,), address="127.0.0.54", port=5300)
            removed = authority.remove(binding.binding_id)
        self.assertEqual(first.config_digest, second.config_digest)
        starts = [call for call, _kwargs in process.calls
                  if len(call) > 1 and call[1].startswith("--conf-file=")]
        self.assertEqual(len(starts), 1)
        self.assertTrue(removed)
        self.assertTrue(any(call[:2] == ("kill", "-TERM") for call, _kwargs in process.calls))

    def test_reused_pid_without_stored_start_and_executable_identity_fails_closed(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        binding = ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/demo::default",), desired={},
        )
        starts = ["boot:1"]
        with tempfile.TemporaryDirectory() as tmp:
            process = RecordingProcess()
            authority = DnsmasqAuthority(
                Path(tmp), process=process, binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (123, starts[-1]),
                pid_matches=lambda pid, identity: pid == 123 and identity == starts[-1],
                pid_executable=lambda _pid: False,
                listener_matches=lambda *_args: True,
            )
            authority.ensure((binding,), address="127.0.0.54", port=5300)
            starts.append("boot:2")
            with self.assertRaisesRegex(RuntimeError, "process identity drifted"):
                authority.ensure((binding,), address="127.0.0.54", port=5300)
        launches = [call for call, _kwargs in process.calls
                    if len(call) > 1 and call[1].startswith("--conf-file=")]
        self.assertEqual(len(launches), 1)

    def test_existing_config_without_receipt_is_never_overwritten(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        binding = ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/demo::default",), desired={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            authority = DnsmasqAuthority(
                Path(tmp), process=RecordingProcess(), binary="/usr/sbin/dnsmasq",
            )
            authority.config_path.write_text("foreign\n")
            authority.config_path.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, "without an ownership receipt"):
                authority.ensure((binding,), address="127.0.0.54", port=5300)
            self.assertEqual(authority.config_path.read_text(), "foreign\n")

    def test_owned_config_digest_drift_is_never_overwritten(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        binding = ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/demo::default",), desired={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            authority = DnsmasqAuthority(
                Path(tmp), process=RecordingProcess(), binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (123, "boot:1"),
                pid_matches=lambda *_args: True,
                pid_executable=lambda _pid: True,
                listener_matches=lambda *_args: True,
            )
            authority.ensure((binding,), address="127.0.0.54", port=5300)
            authority.config_path.write_text("drifted\n")
            with self.assertRaisesRegex(RuntimeError, "config drifted"):
                authority.ensure((binding,), address="127.0.0.54", port=5300)
            self.assertEqual(authority.config_path.read_text(), "drifted\n")

    def test_final_cleanup_preserves_state_on_pid_identity_drift(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        binding = ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/demo::default",), desired={},
        )
        identity = {"value": "boot:1"}
        with tempfile.TemporaryDirectory() as tmp:
            process = RecordingProcess()
            authority = DnsmasqAuthority(
                Path(tmp), process=process, binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (123, identity["value"]),
                pid_matches=lambda _pid, expected: expected == identity["value"],
                pid_executable=lambda _pid: True,
                listener_matches=lambda *_args: True,
                sleeper=lambda _seconds: None,
            )
            authority.ensure((binding,), address="127.0.0.54", port=5300)
            identity["value"] = "boot:2"
            self.assertFalse(authority.remove(binding.binding_id))
            self.assertTrue(authority.state_path.exists())
            self.assertTrue(authority.config_path.exists())
            self.assertFalse(any(call[:2] == ("kill", "-TERM") for call, _ in process.calls))

    def test_final_cleanup_preserves_state_when_term_fails_or_process_stays_live(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        binding = ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/demo::default",), desired={},
        )
        for kill_code in (1, 0):
            with self.subTest(kill_code=kill_code), tempfile.TemporaryDirectory() as tmp:
                class Process(RecordingProcess):
                    def run(self, argv, **kwargs):
                        self.calls.append((tuple(argv), kwargs))
                        code = kill_code if tuple(argv)[:2] == ("kill", "-TERM") else 0
                        return ProcessResult(tuple(argv), code, "", "failed" if code else "")

                authority = DnsmasqAuthority(
                    Path(tmp), process=Process(), binary="/usr/sbin/dnsmasq",
                    pid_reader=lambda _path: (123, "boot:1"),
                    pid_matches=lambda *_args: True,
                    pid_executable=lambda _pid: True,
                    listener_matches=lambda *_args: True,
                    sleeper=lambda _seconds: None,
                )
                authority.ensure((binding,), address="127.0.0.54", port=5300)
                self.assertFalse(authority.remove(binding.binding_id))
                self.assertTrue(authority.state_path.exists())
                self.assertTrue(authority.config_path.exists())


if __name__ == "__main__":
    unittest.main()


class TestDnsmasqArgumentForm(unittest.TestCase):
    """dnsmasq rejects a space-separated long option with "junk found in
    command line". Using it made every live adoption fail as an endpoint
    collision, so the equals form is part of the contract."""

    def test_every_dnsmasq_invocation_uses_the_equals_form(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        process = RecordingProcess()
        binding = ResolutionBinding.create(
            kind="exact", name="one.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/one::default",), desired={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            authority = DnsmasqAuthority(
                Path(tmp), process=process, binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (123, "boot:1"),
                pid_matches=lambda *_args: True,
                pid_executable=lambda _pid: True,
                listener_matches=lambda *_args: True,
            )
            authority.ensure((binding,), address="127.0.0.54", port=5300)

        dnsmasq_calls = [call for call, _kwargs in process.calls
                         if call and call[0].endswith("dnsmasq")]
        self.assertTrue(dnsmasq_calls)
        for call in dnsmasq_calls:
            self.assertNotIn("--conf-file", call,
                             "space-separated form is rejected by dnsmasq")
            self.assertTrue(any(item.startswith("--conf-file=") for item in call),
                            f"no --conf-file= argument in {call}")


class TestStaleAuthorityRecordSelfHeals(unittest.TestCase):
    """A recorded authority process that is GONE is a stale record, not drift.
    Refusing there left the authority unstartable after any reboot or kill."""

    def _authority(self, tmp, *, pid_reader, pid_matches, pid_executable, process):
        from sandbox.network.authority import DnsmasqAuthority

        return DnsmasqAuthority(
            Path(tmp), process=process, binary="/usr/sbin/dnsmasq",
            pid_reader=pid_reader, pid_matches=pid_matches,
            pid_executable=pid_executable, listener_matches=lambda *_args: True,
        )

    @staticmethod
    def _binding():
        from sandbox.network.models import ResolutionBinding

        return ResolutionBinding.create(
            kind="exact", name="one.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/one::default",), desired={},
        )

    def test_dead_recorded_process_is_restarted(self):
        binding = self._binding()
        with tempfile.TemporaryDirectory() as tmp:
            live = self._authority(
                tmp, process=RecordingProcess(), pid_reader=lambda _p: (321, "boot:9"),
                pid_matches=lambda *_a: True, pid_executable=lambda _pid: True)
            live.ensure((binding,), address="127.0.0.55", port=5300)

            # The process is gone: no pid file, nothing matches.
            process = RecordingProcess()
            healed = self._authority(
                tmp, process=process, pid_reader=lambda _p: (None, None),
                pid_matches=lambda *_a: False, pid_executable=lambda _pid: False)
            result = healed.ensure((binding,), address="127.0.0.55", port=5300)

        self.assertIsNotNone(result)
        self.assertTrue(any(call and call[0].endswith("dnsmasq")
                            for call, _kwargs in process.calls))

    def test_live_unprovable_process_is_still_refused(self):
        binding = self._binding()
        with tempfile.TemporaryDirectory() as tmp:
            live = self._authority(
                tmp, process=RecordingProcess(), pid_reader=lambda _p: (321, "boot:9"),
                pid_matches=lambda *_a: True, pid_executable=lambda _pid: True)
            live.ensure((binding,), address="127.0.0.55", port=5300)

            # Same pid still alive, but its start marker no longer matches ours.
            foreign = self._authority(
                tmp, process=RecordingProcess(), pid_reader=lambda _p: (321, "boot:99"),
                pid_matches=lambda *_a: True, pid_executable=lambda _pid: True)
            with self.assertRaises(RuntimeError) as caught:
                foreign.ensure((binding,), address="127.0.0.55", port=5300)

        self.assertIn("drifted", str(caught.exception))


class TestFinalRemovalWhenAuthorityIsGone(unittest.TestCase):
    """Removing the last owned zone when the process is already dead must
    converge: refusing left cleanup incomplete forever with nothing to clean."""

    @staticmethod
    def _binding():
        from sandbox.network.models import ResolutionBinding

        return ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77", adapter_id="resolved",
            owners=("/tmp/demo::default",), desired={},
        )

    def test_dead_process_allows_final_removal(self):
        from sandbox.network.authority import DnsmasqAuthority

        binding = self._binding()
        with tempfile.TemporaryDirectory() as tmp:
            live = DnsmasqAuthority(
                Path(tmp), process=RecordingProcess(), binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (123, "boot:1"),
                pid_matches=lambda *_args: True, pid_executable=lambda _pid: True,
                listener_matches=lambda *_args: True, sleeper=lambda _s: None)
            live.ensure((binding,), address="127.0.0.55", port=5300)

            gone = DnsmasqAuthority(
                Path(tmp), process=RecordingProcess(), binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (None, None),
                pid_matches=lambda *_args: False, pid_executable=lambda _pid: False,
                listener_matches=lambda *_args: False, sleeper=lambda _s: None)
            self.assertTrue(gone.remove(binding.binding_id))
            self.assertFalse(gone.config_path.exists())
            self.assertFalse(gone.state_path.exists())

    def test_a_live_process_is_still_preserved(self):
        from sandbox.network.authority import DnsmasqAuthority

        binding = self._binding()
        with tempfile.TemporaryDirectory() as tmp:
            live = DnsmasqAuthority(
                Path(tmp), process=RecordingProcess(), binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (123, "boot:1"),
                pid_matches=lambda *_args: True, pid_executable=lambda _pid: True,
                listener_matches=lambda *_args: True, sleeper=lambda _s: None)
            live.ensure((binding,), address="127.0.0.55", port=5300)

            drifted = DnsmasqAuthority(
                Path(tmp), process=RecordingProcess(), binary="/usr/sbin/dnsmasq",
                pid_reader=lambda _path: (123, "boot:9"),
                pid_matches=lambda _pid, expected: expected == "boot:9",
                pid_executable=lambda _pid: True,
                listener_matches=lambda *_args: True, sleeper=lambda _s: None)
            self.assertFalse(drifted.remove(binding.binding_id))
            self.assertTrue(drifted.state_path.exists())
