import unittest
import base64
import subprocess
import tempfile
from pathlib import Path

from tests.fixtures.hosting_image_activation import activation_policy, activation_request, authority_binding


class ActivationPolicyTests(unittest.TestCase):
    def test_public_ed25519_verifier_accepts_signed_grant_and_rejects_substitution(self):
        from sandbox.hosting.images.activation.models import (
            RollbackCompatibilityGrant, activation_digest,
        )
        from sandbox.hosting.images.activation.policy import SshRollbackGrantVerifier
        from tests.fixtures.hosting_image_activation import rollback_grant
        grant = rollback_grant()
        message = __import__("json").dumps(grant.unsigned_mapping(), sort_keys=True,
            separators=(",", ":")).encode()
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); key = root / "authority"
            subprocess.run(("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                            "-f", str(key)), env=environment, check=True)
            message_path = root / "grant.json"; message_path.write_bytes(message)
            subprocess.run(("/usr/bin/ssh-keygen", "-Y", "sign", "-f", str(key),
                            "-n", "sandbox-feature-051-rollback", str(message_path)),
                           env=environment, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            public = key.with_suffix(".pub").read_text().strip()
            signature = base64.b64encode((root / "grant.json.sig").read_bytes()).decode()
        body = {**grant.unsigned_mapping(), "authority_proof": signature}
        signed = RollbackCompatibilityGrant(
            authority_id=grant.authority_id, authority_revision=grant.authority_revision,
            issued_at=grant.issued_at, policy_revision=grant.policy_revision,
            subject=grant.subject, authority_proof=signature, expires_at=grant.expires_at,
            revoked=grant.revoked, grant_digest=activation_digest(
                "sandbox.hosting.images.rollback-grant.v1", body))
        verifier = SshRollbackGrantVerifier(" ".join(public.split()[:2]), grant.authority_id)
        self.assertTrue(verifier.verify(signed))
        replacement = SshRollbackGrantVerifier(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPpWPAE2WMbZ0fAZ8xsqiTIJqA28qDBfGru8kPrpNyUb",
            grant.authority_id)
        self.assertFalse(replacement.verify(signed))
    def test_capability_and_target_cannot_be_widened(self):
        from sandbox.hosting.images.activation.policy import admit_activation
        from sandbox.hosting.images.activation.models import ActivationRequest
        policy = activation_policy(); request = activation_request(); binding = authority_binding(policy=policy)
        self.assertFalse(admit_activation(request, policy, binding, capability="rollback").ok)
        changed = ActivationRequest.create(
            request_id=request.request_id, operation=request.operation,
            expected_generation=request.expected_generation,
            policy_digest=request.policy_digest, plan=request.plan, proof=request.proof,
            authority_binding_digest="sha256:" + "f" * 64,
            rollback_subject_digest=request.rollback_subject_digest,
            rollback_grant_digest=request.rollback_grant_digest,
            confirmed=request.confirmed)
        self.assertEqual(admit_activation(changed, policy, binding, capability="activate").code,
                         "authority_mismatch")

    def test_adoption_is_zero_init_only(self):
        from sandbox.hosting.images.activation.policy import admit_activation
        request = activation_request(operation="adopt")
        policy = activation_policy(); binding = authority_binding(policy=policy)
        self.assertEqual(admit_activation(request, policy, binding, capability="adopt").code,
                         "adoption_requires_zero_init")


if __name__ == "__main__": unittest.main()
