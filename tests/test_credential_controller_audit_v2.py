import hashlib
import unittest

from sandbox.isolation.credential_controller_audit_v2 import (
    AuditV2Error,
    ControllerAuditAuthorityV2,
    CredentialEffectExecutorV2,
    DurableAuditRepositoryV2,
    EffectResultV2,
)
from sandbox.isolation.credential_controller_protocol_v2 import PROTOCOL, digest_document
from sandbox.isolation.credential_controller_service_v2 import (
    ControllerBrokerSession,
    ControllerServiceConfig,
    ProcessIdentity,
)
from sandbox.isolation.models import EgressGrantSet


NOW = 1_800_000_000_000
MACHINE = "sb-0123456789ab"
BROKER_EPOCH = "01" * 16
CONTROLLER_EPOCH = "02" * 16
DIGESTS = [hashlib.sha256(f"audit-{index}".encode()).hexdigest() for index in range(8)]


class Connection:
    def __init__(self): self.sent = []
    def getsockopt(self, *_args): return b""
    def recvmsg(self, *_args): return b"", [], 0, None
    def sendall(self, value): self.sent.append(value)
    def close(self): pass


def identity(offset):
    return ProcessIdentity(
        uid=1000 + offset, gid=2000 + offset, pid=3000 + offset,
        start_ticks=4000 + offset, executable_digest=DIGESTS[offset],
        unit_digest=DIGESTS[offset + 2], config_digest=DIGESTS[offset + 4],
    )


CONFIG = ControllerServiceConfig(
    machine_id=MACHINE, controller=identity(0), broker=identity(1),
    policy_digest=DIGESTS[0],
    egress_digest=EgressGrantSet(MACHINE, DIGESTS[0]).digest,
    broker_digest=DIGESTS[2], proof_digest=DIGESTS[3],
    effective_isolation_digest=DIGESTS[4], evidence_id="evidence-0123456",
)


def session():
    value = ControllerBrokerSession(
        Connection(), CONFIG, CONTROLLER_EPOCH, "controller-session-0123456789abcdef",
        on_terminal=lambda _reason: None,
    )
    value.authenticated = True
    value.broker_epoch = BROKER_EPOCH
    value.sequences.accept("broker_to_controller", 1)
    value.sequences.accept("controller_to_broker", 1)
    value._next_outgoing = 2
    return value


class Repository(DurableAuditRepositoryV2):
    def __init__(self, records=(), *, durable=True):
        self.items = [dict(item) for item in records]
        self.durable = durable
        self.attempts = []

    def records(self, machine_id):
        return tuple(item for item in self.items if item["machine_id"] == machine_id)

    def append(self, record):
        self.attempts.append(dict(record))
        if self.durable:
            self.items.append(dict(record))
        return self.durable


def pre(sequence=2, *, event="credential_effect_pre"):
    value = {
        "protocol": PROTOCOL, "type": "AUDIT_PRE_V2", "machine_id": MACHINE,
        "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
        "sequence": sequence, "operation_id": "operation-012345",
        "binding_id": "binding-01234567", "binding_version": 1,
        "decision_id": "decision-0123456", "audit_root_id": "audit-root0123456789",
        "phase_id": "audit-pre0123456789", "event_code": event,
    }
    value["audit_fingerprint"] = digest_document("audit_pre_fingerprint", {
        key: value[key] for key in ("machine_id", "operation_id", "binding_id",
            "binding_version", "decision_id", "audit_root_id", "phase_id", "event_code")
    })
    return value


def post(pre_ack, sequence=3, *, certainty="completed", outcome="completed",
         reason="upstream_completed"):
    value = {
        "protocol": PROTOCOL, "type": "AUDIT_POST_V2", "machine_id": MACHINE,
        "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
        "sequence": sequence, "operation_id": "operation-012345",
        "binding_id": "binding-01234567", "binding_version": 1,
        "decision_id": "decision-0123456", "audit_root_id": "audit-root0123456789",
        "phase_id": "audit-post012345678", "pre_commit_id": pre_ack["commit_id"],
        "outcome_class": outcome, "effect_certainty": certainty, "reason_code": reason,
    }
    value["audit_fingerprint"] = digest_document("audit_post_fingerprint", {
        key: value[key] for key in ("machine_id", "operation_id", "binding_id",
            "binding_version", "decision_id", "audit_root_id", "phase_id",
            "pre_commit_id", "outcome_class", "effect_certainty", "reason_code")
    })
    return value


def accept(owner, message):
    owner.session.sequences.accept("broker_to_controller", message["sequence"])
    owner.session._last_received_frame = dict(message)
    owner.session._last_received_consumed = False


def authority(repository, owner=None, *, commits=None, phases=None, recover=True):
    commits = iter(commits or ("commit-pre012345678", "commit-post01234567"))
    phases = iter(phases or ("audit-recovery012345",))
    value = ControllerAuditAuthorityV2(
        owner or session(), repository,
        commit_id_factory=lambda: next(commits),
        phase_id_factory=lambda: next(phases),
    )
    if recover:
        value.recover_unclosed_pre(now_ms=NOW - 1)
    return value


class TestCredentialControllerAuditV2(unittest.TestCase):
    def test_pre_and_post_are_durable_before_ack_and_persist_no_forbidden_fields(self):
        repo = Repository()
        owner = authority(repo)
        first = pre()
        accept(owner, first)
        pre_ack = owner.handle(first, now_ms=NOW)
        self.assertEqual((pre_ack["phase"], pre_ack["disposition"]), ("pre", "committed"))
        second = post(pre_ack)
        accept(owner, second)
        post_ack = owner.handle(second, now_ms=NOW + 1)
        self.assertEqual((post_ack["phase"], post_ack["disposition"]), ("post", "committed"))
        forbidden = {"operation_id", "request_digest", "lease_id", "authorization_digest",
                     "auth_form", "source_handle", "descriptor", "headers", "body",
                     "protocol", "broker_epoch", "controller_epoch", "sequence"}
        self.assertTrue(all(forbidden.isdisjoint(item) for item in repo.items))
        self.assertTrue(owner.activation_ready)

    def test_exact_semantic_retry_reuses_commit_and_conflict_is_sticky(self):
        repo = Repository()
        owner = authority(repo, commits=("commit-pre012345678",))
        message = pre()
        accept(owner, message)
        first = owner.handle(message, now_ms=NOW)
        replay = dict(message, sequence=3)
        accept(owner, replay)
        second = owner.handle(replay, now_ms=NOW + 1)
        self.assertEqual(first["commit_id"], second["commit_id"])
        self.assertEqual(len(repo.items), 1)

        conflicting = dict(replay, event_code="credential_effect_pre")
        conflicting["operation_id"] = "operation-abcdef"
        conflicting["audit_fingerprint"] = digest_document("audit_pre_fingerprint", {
            key: conflicting[key] for key in ("machine_id", "operation_id", "binding_id",
                "binding_version", "decision_id", "audit_root_id", "phase_id", "event_code")
        })
        conflicting["sequence"] = 4
        accept(owner, conflicting)
        with self.assertRaisesRegex(AuditV2Error, "audit_conflict"):
            owner.handle(conflicting, now_ms=NOW + 2)
        with self.assertRaisesRegex(AuditV2Error, "audit_conflict"):
            owner.recover_unclosed_pre(now_ms=NOW + 3)

    def test_unproven_durable_append_never_acknowledges(self):
        repo = Repository(durable=False)
        owner = authority(repo)
        message = pre()
        accept(owner, message)
        with self.assertRaisesRegex(AuditV2Error, "audit_unavailable"):
            owner.handle(message, now_ms=NOW)
        self.assertEqual(owner.session.connection.sent, [])
        self.assertFalse(owner.activation_ready)

    def test_recovery_closes_open_pre_without_operation_identity(self):
        repo = Repository()
        first_owner = authority(repo)
        message = pre()
        accept(first_owner, message)
        first_owner.handle(message, now_ms=NOW)
        recovered = authority(
            repo, session(), commits=("commit-recover012345",),
            phases=("audit-recovery012345",),
            recover=False,
        )
        self.assertFalse(recovered.activation_ready)
        self.assertEqual(recovered.recover_unclosed_pre(now_ms=NOW + 10), 1)
        self.assertTrue(recovered.activation_ready)
        record = repo.items[-1]
        self.assertEqual((record["outcome_class"], record["effect_certainty"],
                          record["reason_code"], record["recovery"]),
                         ("indeterminate", "possible", "audit_unavailable", True))
        self.assertNotIn("operation_id", record)

    def test_repository_mutation_and_forbidden_field_are_refused_on_recovery(self):
        repo = Repository()
        owner = authority(repo)
        message = pre()
        accept(owner, message)
        owner.handle(message, now_ms=NOW)
        for mutation in (
            lambda value: value.update(operation_id="operation-forbidden"),
            lambda value: value.update(commit_id="bad"),
            lambda value: value.update(phase="post"),
        ):
            with self.subTest(mutation=mutation):
                changed = dict(repo.items[0])
                mutation(changed)
                changed_owner = authority(Repository((changed,)), session(), recover=False)
                with self.assertRaises(AuditV2Error):
                    changed_owner.recover_unclosed_pre(now_ms=NOW + 1)

        invalid_phase = dict(repo.items[0], phase="unknown")
        invalid_owner = authority(Repository((invalid_phase,)), session(), recover=False)
        with self.assertRaisesRegex(AuditV2Error, "audit_repository_invalid"):
            invalid_owner.recover_unclosed_pre(now_ms=NOW + 1)
        self.assertFalse(invalid_owner.session.admission_open)
        self.assertEqual(len(invalid_owner.repository.items), 1)

    def test_construction_is_inert_and_recovery_bounds_infinite_iterators(self):
        class InfiniteRepository(DurableAuditRepositoryV2):
            def __init__(self, template):
                self.reads = 0
                self.template = dict(template)

            def records(self, machine_id):
                del machine_id
                while True:
                    self.reads += 1
                    yield dict(self.template)

            def append(self, record):
                del record
                return True

        seed = Repository()
        seed_owner = authority(seed)
        message = pre()
        accept(seed_owner, message)
        seed_owner.handle(message, now_ms=NOW)
        repo = InfiniteRepository(seed.items[0])
        owner = authority(repo, recover=False)
        self.assertEqual(repo.reads, 0)
        with self.assertRaises(AuditV2Error):
            owner.recover_unclosed_pre(now_ms=NOW)
        self.assertEqual(repo.reads, 4097)
        self.assertFalse(owner.session.admission_open)

    def test_typed_effect_results_refuse_invalid_certainty_pairs(self):
        self.assertEqual(EffectResultV2("completed", "completed", "upstream_completed").reason_code,
                         "upstream_completed")
        self.assertEqual(EffectResultV2("refused", "none", "egress_denied").reason_code,
                         "egress_denied")
        self.assertEqual(EffectResultV2(
            "indeterminate", "completed", "upstream_refused").reason_code,
            "upstream_refused")
        with self.assertRaisesRegex(AuditV2Error, "effect_result_invalid"):
            EffectResultV2("completed", "possible", "upstream_completed")
        with self.assertRaisesRegex(TypeError, "abstract"):
            CredentialEffectExecutorV2()


if __name__ == "__main__":
    unittest.main()
