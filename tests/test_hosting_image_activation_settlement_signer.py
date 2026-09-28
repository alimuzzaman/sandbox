import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.hosting.images.activation.settlement_signer import SettlementSshSigner
from tests.test_hosting_image_activation_settlement_repository import _plan
from tests.subprocess_support import synthetic_environment


SIGNER_KEY = "ssh-ed25519 AAAA"


class SettlementSignerTests(unittest.TestCase):
    def _signer(self, root: Path) -> SettlementSshSigner:
        public = root / "rollback-authority.pub"
        public.write_text(SIGNER_KEY + "\n", encoding="ascii")
        public.chmod(0o600)
        return SettlementSshSigner(
            public, "rollback-authority/controller", "rollback-v3")

    def test_signing_requires_an_existing_absolute_agent_socket_before_subprocess(self):
        with tempfile.TemporaryDirectory() as directory:
            signer = self._signer(Path(directory))
            with patch("sandbox.hosting.images.activation.settlement_signer.os.environ",
                       synthetic_environment({"SSH_AUTH_SOCK": "relative.sock"})), \
                    patch("sandbox.hosting.images.activation.settlement_signer.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "authority_missing"):
                    signer.settlement(_plan(), issued_at=100, expires_at=200)
            run.assert_not_called()

    def test_settlement_signing_uses_only_fixed_child_environment_and_returns_no_private_input(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            payload = Path(argv[-1])
            payload.with_suffix(".sig").write_bytes(
                b"-----BEGIN SSH SIGNATURE-----\nsynthetic\n-----END SSH SIGNATURE-----\n")
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as directory:
            signer = self._signer(Path(directory))
            with patch("sandbox.hosting.images.activation.settlement_signer.os.environ", synthetic_environment({
                    "SSH_AUTH_SOCK": "/tmp/agent.sock",
                    "SETTLEMENT_PRIVATE_CANARY": "private-password-canary",
                })), \
                    patch("sandbox.hosting.images.activation.settlement_signer.subprocess.run",
                          side_effect=fake_run), \
                    patch("sandbox.hosting.images.activation.settlement_signer.SettlementApprovalVerifier.verify",
                          return_value=True) as verify:
                approval = signer.settlement(_plan(), issued_at=100, expires_at=200)
        self.assertEqual(len(calls), 1)
        environment = calls[0][1]["env"]
        self.assertEqual(set(environment), {"PATH", "LANG", "LC_ALL", "SSH_AUTH_SOCK"})
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")
        self.assertEqual(environment["SSH_AUTH_SOCK"], "/tmp/agent.sock")
        self.assertNotIn("private-password-canary", repr(calls))
        self.assertEqual(approval.authority_id, "rollback-authority/controller")
        self.assertNotIn("rollback-authority.pub", str(approval.as_mapping()))
        verify.assert_called_once()

    def test_unknown_signing_namespace_is_refused_without_agent_or_process_access(self):
        with tempfile.TemporaryDirectory() as directory:
            signer = self._signer(Path(directory))
            with self.assertRaisesRegex(ValueError, "authority_invalid"), patch(
                    "sandbox.hosting.images.activation.settlement_signer.subprocess.run") as run:
                signer._sign({"subject": "value"}, "wrong-namespace")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
