"""Explicit operator signing through an existing SSH agent, never deployment admission."""
import base64
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

from .models import canonical_bytes
from .settlement_models import SettlementApproval
from .settlement_forward import ForwardSettlementApproval
from .settlement_policy import (
    SETTLEMENT_NAMESPACE, FORWARD_NAMESPACE, SettlementApprovalVerifier, _safe_public_key,
)
from ..plan_set import read_stable_file


class SettlementSshSigner:
    def __init__(self, public_key_path, authority_id, authority_revision):
        self.path = Path(public_key_path).expanduser()
        if not self.path.is_absolute(): raise ValueError('authority_invalid')
        self.public_key = _safe_public_key(read_stable_file(self.path, 4096).decode('ascii').strip())
        self.verifier = SettlementApprovalVerifier(self.public_key, authority_id, authority_revision,
            'sha256:' + hashlib.sha256(self.public_key.encode('ascii')).hexdigest())

    def _sign(self, subject, namespace):
        if namespace not in {SETTLEMENT_NAMESPACE, FORWARD_NAMESPACE}: raise ValueError('authority_invalid')
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
        socket = os.environ.get('SSH_AUTH_SOCK')
        if not socket or not Path(socket).is_absolute(): raise ValueError('authority_missing')
        environment['SSH_AUTH_SOCK'] = socket
        with tempfile.TemporaryDirectory(prefix='sandbox-operator-approval-') as directory:
            root = Path(directory)
            public = root / 'signer.pub'; public.write_text(self.public_key + '\n', encoding='ascii')
            payload = root / 'subject'; payload.write_bytes(canonical_bytes(subject))
            result = subprocess.run(('/usr/bin/ssh-keygen', '-Y', 'sign', '-U', '-f', str(public),
                '-n', namespace, str(payload)), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=environment, timeout=30, check=False)
            if result.returncode: raise ValueError('signature_failed')
            signature = read_stable_file(payload.with_suffix('.sig'), 4096)
            return base64.b64encode(signature).decode('ascii')

    def settlement(self, plan, *, issued_at, expires_at):
        subject = {'schema_version': 1, 'authority_id': self.verifier.authority_id,
            'authority_revision': self.verifier.authority_revision, 'plan_digest': plan.plan_digest,
            'issued_at': issued_at, 'expires_at': expires_at}
        signature = self._sign(subject, SETTLEMENT_NAMESPACE)
        approval = SettlementApproval.create(**{key: value for key, value in subject.items() if key != 'schema_version'}, signature=signature)
        if not self.verifier.verify(approval, plan, now=issued_at): raise ValueError('signature_failed')
        return approval

    def forward(self, subject, assessment, *, issued_at, expires_at):
        fields = {'operation', 'target', 'request_id', 'expected_generation', 'predecessor_digest',
            'transaction_digest', 'application_revision', 'plan_set_digest', 'proof_set_digest',
            'compose_snapshot_digest', 'policy_digest', 'rollback_grant_digest'}
        if type(subject) is not dict or set(subject) != fields: raise ValueError('authority_invalid')
        body = {'schema_version': 1, 'authority_id': self.verifier.authority_id,
            'authority_revision': self.verifier.authority_revision, **subject,
            'data_assessment': assessment.as_mapping(), 'issued_at': issued_at, 'expires_at': expires_at}
        signature = self._sign(body, FORWARD_NAMESPACE)
        approval = ForwardSettlementApproval.create(**{key: value for key, value in body.items() if key != 'schema_version'}, signature=signature)
        if not self.verifier.verify_forward(approval, now=issued_at): raise ValueError('signature_failed')
        return approval
