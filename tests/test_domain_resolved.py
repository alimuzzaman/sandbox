from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sandbox.services.process import ProcessResult


class RecordingProcess:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        return ProcessResult(tuple(argv), self.returncode, self.stdout, "failure" if self.returncode else "")


class TestResolvedAdapter(unittest.TestCase):
    @staticmethod
    def _identity():
        return {
            "schema": "sandbox-resolved-service-v1",
            "owner_id": "systemd-resolved:host",
            "unit": "systemd-resolved.service", "pid": 321,
            "start_ticks": 654321, "uid": 992,
            "control_group": "/system.slice/systemd-resolved.service",
        }

    def test_qualification_preflight_parses_identity_bound_helper_evidence(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter
        from sandbox.network.models import ResolverObservation

        process = RecordingProcess(stdout=(
            "sandbox-resolved-service-v1 "
            "owner=systemd-resolved:host unit=systemd-resolved.service "
            "pid=321 start=654321 uid=0 "
            "control=/system.slice/systemd-resolved.service\n"
        ))
        observed = ResolverObservation.create(
            owner_id="systemd-resolved:host", manager="resolved", mode="stub",
            support_tier="adoptable",
            extension={"kind": "route-only-domain", "global_takeover": False},
        )
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(process=process, network_root=Path(tmp))
            evidence = adapter.qualification_preflight(observed)

        self.assertEqual(evidence["pid"], 321)
        self.assertEqual(evidence["start_ticks"], 654321)
        self.assertEqual(evidence["owner_id"], observed.owner_id)
        self.assertEqual(process.calls[0][0], (
            "sudo", "-n", "/usr/local/libexec/sandbox-resolver-helper",
            "resolved-status",
        ))

    def test_qualification_preflight_rejects_malformed_or_changed_owner(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter
        from sandbox.network.models import ResolverObservation

        observed = ResolverObservation.create(
            owner_id="systemd-resolved:host", manager="resolved", mode="stub",
            support_tier="adoptable",
            extension={"kind": "route-only-domain", "global_takeover": False},
        )
        outputs = (
            "ready\n",
            "sandbox-resolved-service-v1 owner=networkmanager:host "
            "unit=NetworkManager.service pid=321 start=654321 uid=0 "
            "control=/system.slice/NetworkManager.service\n",
        )
        for output in outputs:
            with self.subTest(output=output), tempfile.TemporaryDirectory() as tmp:
                adapter = ResolvedAdapter(
                    process=RecordingProcess(stdout=output), network_root=Path(tmp),
                )
                self.assertIsNone(adapter.qualification_preflight(observed))

    def test_apply_uses_fixed_helper_and_preserves_resolv_conf_symlink(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        links = ["/run/systemd/resolve/stub-resolv.conf"]
        process = RecordingProcess()
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(
                process=process,
                network_root=Path(tmp), readlink=lambda _path: links[0],
            )
            applied = adapter.apply({
                "suffix": "test", "address": "127.0.0.54", "port": 5300,
                "owner_digest": "b" * 64, "service_identity": self._identity(),
            })
        self.assertTrue(applied["ok"])
        self.assertEqual(links, ["/run/systemd/resolve/stub-resolv.conf"])
        self.assertEqual(process.calls[0][0][:2], ("sudo", "-n"))
        self.assertEqual(process.calls[0][0][2:7], (
            "/usr/local/libexec/sandbox-resolver-helper", "resolved-apply",
            "b" * 64, "test", "127.0.0.54",
        ))
        self.assertNotIn(str(Path(tmp)), process.calls[0][0])
        self.assertEqual(process.calls[0][0][-4:], (
            "321", "654321", "992", "/system.slice/systemd-resolved.service",
        ))

    def test_apply_rejects_missing_final_service_identity_without_helper_call(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        process = RecordingProcess()
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(process=process, network_root=Path(tmp))
            result = adapter.apply({
                "suffix": "test", "address": "127.0.0.54", "port": 5300,
                "owner_digest": "b" * 64,
            })
        self.assertFalse(result["ok"])
        self.assertEqual(process.calls, [])

    def test_helper_failure_returns_no_false_success(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(
                process=RecordingProcess(returncode=1),
                network_root=Path(tmp), readlink=lambda _path: "/run/systemd/resolve/stub-resolv.conf",
            )
            result = adapter.apply({
                "suffix": "test", "address": "127.0.0.54", "port": 5300,
                "owner_digest": "b" * 64, "service_identity": self._identity(),
            })
        self.assertFalse(result["ok"])
        self.assertFalse(result["mutated"])

    def test_rollback_uses_expected_fragment_digest(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        process = RecordingProcess()
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(
                process=process, network_root=Path(tmp),
                readlink=lambda _path: "/run/systemd/resolve/stub-resolv.conf",
            )
            result = adapter.rollback({
                "suffix": "test", "address": "127.0.0.54", "port": 5300,
                "owner_digest": "b" * 64, "fragment_digest": "a" * 64,
                "service_identity": self._identity(),
            })
        self.assertTrue(result["ok"])
        self.assertEqual(process.calls[0][0][4:9], (
            "b" * 64, "test", "127.0.0.54", "5300", "a" * 64,
        ))

    def test_preapply_revoke_is_receipt_only_and_distinct_from_rollback(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        process = RecordingProcess()
        plan = {"suffix": "test", "address": "127.0.0.54", "port": 5300,
                "owner_digest": "b" * 64,
                "service_identity": self._identity()}
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(process=process, network_root=Path(tmp))
            adapter.revoke_authorization(plan)
        self.assertEqual(process.calls[0][0][3:5], (
            "revoke-authorization", "resolved",
        ))
        self.assertNotIn("resolved-remove", process.calls[0][0])

    def test_nonfixed_mutation_helper_is_rejected(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "fixed"):
            ResolvedAdapter(
                process=RecordingProcess(), helper="/tmp/repository-helper",
                network_root=Path(tmp),
            )

    def test_helper_install_requires_interactive_consent_and_verifies_fixed_copy(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        process = RecordingProcess(returncode=1)
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(
                process=process, repository_helper="/repo/tools/resolver-helper.sh",
                network_root=Path(tmp),
            )
            pending = adapter.ensure_helper(interactive=False)
        self.assertFalse(pending["ok"])
        self.assertEqual(len(process.calls), 1)
        self.assertEqual(process.calls[0][0][:3], (
            "sudo", "-n", "/usr/local/libexec/sandbox-resolver-helper",
        ))

    def test_old_helper_status_is_upgraded_before_qualification(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        class UpgradeProcess:
            def __init__(self): self.calls = []
            def run(self, argv, **kwargs):
                self.calls.append(tuple(argv))
                output = "ready\n" if len(self.calls) == 1 else "sandbox-resolver-helper-v2\n"
                return ProcessResult(tuple(argv), 0, output, "")

        process = UpgradeProcess()
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(
                process=process, repository_helper="/repo/tools/resolver-helper.sh",
                network_root=Path(tmp),
            )
            result = adapter.ensure_helper(interactive=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["mutated"])
        self.assertEqual(process.calls[1], (
            "sudo", "/repo/tools/resolver-helper.sh", "install",
        ))


if __name__ == "__main__":
    unittest.main()
