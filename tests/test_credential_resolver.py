"""Foundational broker-only reference resolver and one-use lease contracts."""

from pathlib import Path
import tempfile
import threading
import unittest


def _binding():
    from sandbox.isolation.credential_binding import CredentialBinding

    return CredentialBinding(
        "bind-resolver-1", "sb-0123456789ab", "fixture/API_TOKEN",
        "a" * 64, "b" * 64, "c" * 64, "https", "api.example.com", 443,
        "GET", "/v1/items", "api_key", "2999-01-01T00:00:00Z", "project:fixture",
    ).transition("ready")


class TestCredentialResolver(unittest.TestCase):
    def _resolver(self, root):
        from sandbox.isolation.credential_resolver import SecretReferenceResolver
        from sandbox.secrets.sources import SourceRegistry

        source = root / ".env.fixture"
        source.write_text("API_TOKEN=SB_SYNTHETIC_VALUE_NOT_A_SECRET_123456\n")
        source.chmod(0o600)
        registry = SourceRegistry(
            root, {"fixture": {"path": ".env.fixture"}}, personal_path=root / ".personal",
        )
        return SecretReferenceResolver(registry)

    def test_issue_requires_ready_binding_and_source_is_registered_and_owned(self):
        from sandbox.isolation.credential_binding import CredentialBinding
        from sandbox.isolation.credential_resolver import SecretReferenceResolver
        from sandbox.secrets.sources import SourceRegistry

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver = self._resolver(root)
            pending = CredentialBinding(
                "bind-pending", "sb-0123456789ab", "fixture/API_TOKEN",
                "a" * 64, "b" * 64, "c" * 64, "https", "api.example.com", 443,
                "GET", "/v1/items", "bearer", "2999-01-01T00:00:00Z", "project:fixture",
            )
            with self.assertRaisesRegex(Exception, "not ready"):
                resolver.issue(pending)
            with self.assertRaisesRegex(Exception, "plaintext"):
                resolver.resolve("fixture/API_TOKEN")
            with self.assertRaises(Exception):
                resolver.issue("unknown/API_TOKEN", binding_id="b", binding_version=1,
                               expires_at="2999-01-01T00:00:00Z")
            restricted = SecretReferenceResolver(
                SourceRegistry(root, {"fixture": {"path": ".env.fixture"}},
                               personal_path=root / ".personal"),
                allowed_scopes=("personal",),
            )
            with self.assertRaisesRegex(Exception, "scope"):
                restricted.issue(_binding())

    def test_one_use_lease_delivers_only_to_callback_and_wipes_transient_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver = self._resolver(root)
            binding = _binding()
            lease = resolver.issue(binding)
            seen = []
            result = lease.consume(lambda value: seen.append(value) or {"status": 200})
            self.assertEqual(result, {"status": 200})
            self.assertEqual(seen, [b"SB_SYNTHETIC_VALUE_NOT_A_SECRET_123456"])
            self.assertNotIn(b"SB_SYNTHETIC_VALUE_NOT_A_SECRET_123456", repr(lease).encode())
            with self.assertRaisesRegex(Exception, "already been consumed"):
                lease.consume(lambda _value: {"status": 200})
            with self.assertRaisesRegex(Exception, "plaintext"):
                resolver.resolve(binding.source_reference)

    def test_plaintext_callback_result_and_invalid_callback_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver = self._resolver(root)
            binding = _binding()
            lease = resolver.issue(binding)
            with self.assertRaisesRegex(Exception, "structured result"):
                lease.consume(lambda value: value)
            with self.assertRaisesRegex(Exception, "consumer is invalid"):
                lease.consume(None)
            lease = resolver.issue(binding)
            lease.invalidate()
            with self.assertRaisesRegex(Exception, "revoked"):
                lease.consume(lambda _value: {"status": 200})

    def test_unsafe_registered_source_is_refused_before_lease_creation(self):
        from sandbox.isolation.credential_resolver import SecretReferenceResolver
        from sandbox.secrets.sources import SourceRegistry

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / ".env.fixture"
            source.write_text("API_TOKEN=SB_SYNTHETIC_VALUE_NOT_A_SECRET_123456\n")
            source.chmod(0o644)
            registry = SourceRegistry(
                root, {"fixture": {"path": ".env.fixture"}}, personal_path=root / ".personal",
            )
            resolver = SecretReferenceResolver(registry)
            with self.assertRaisesRegex(Exception, "broker-readable"):
                resolver.issue(_binding())

    def test_reader_seam_still_never_returns_bytes_from_issue(self):
        from sandbox.isolation.credential_resolver import SecretReferenceResolver
        from sandbox.secrets.sources import SourceRegistry

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver = SecretReferenceResolver(
                SourceRegistry(root, {"fixture": {"path": ".env.fixture"}},
                               personal_path=root / ".personal"),
                reader=lambda _reference: b"SB_SYNTHETIC_VALUE_NOT_A_SECRET_123456",
            )
            # The registry still has to prove an owner-only registered source;
            # issue is intentionally refused before the injected reader could
            # bypass that check.
            with self.assertRaises(Exception):
                resolver.issue(_binding())

    def test_reader_errors_are_sanitized_before_reaching_the_consumer(self):
        from sandbox.isolation.credential_resolver import SecretReferenceResolver
        from sandbox.secrets.models import SecretBrokerError
        from sandbox.secrets.sources import SourceRegistry

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver = SecretReferenceResolver(
                SourceRegistry(root, {"fixture": {"path": ".env.fixture"}},
                               personal_path=root / ".personal"),
                reader=lambda _reference: (_ for _ in ()).throw(
                    SecretBrokerError("adapter_failed", "LEAKED_SOURCE_VALUE")),
            )
            # Probe still proves the registered source before the reader seam
            # is reached, so this test uses the normal safe fixture setup.
            source = root / ".env.fixture"
            source.write_text("API_TOKEN=SB_SYNTHETIC_VALUE_NOT_A_SECRET_123456\n")
            source.chmod(0o600)
            lease = resolver.issue(_binding())
            with self.assertRaisesRegex(Exception, "unavailable") as raised:
                lease.consume(lambda _value: {"status": 200})
            self.assertNotIn("LEAKED_SOURCE_VALUE", str(raised.exception))

    def _revision_bound_lease(self):
        from datetime import datetime, timedelta, timezone
        from sandbox.isolation.credential_resolver import BrokerLease, SecretReference

        class RefusingResolver:
            def __init__(self): self.reads = 0
            def _read_reference(self, _reference):
                self.reads += 1
                raise AssertionError("revision-bound lease reopened its source")

        resolver = RefusingResolver()
        lease = BrokerLease(resolver, SecretReference("fixture", "API_TOKEN", "project"),
            binding_id="binding-race", binding_version=1,
            deadline=datetime.now(timezone.utc) + timedelta(minutes=1),
            lease_id="lease-race", material=b"REVISION_BOUND_SNAPSHOT",
            snapshot_bound=True)
        return resolver, lease

    @staticmethod
    def _ordered_lock(order):
        class OrderedLock:
            def __init__(self):
                self.condition = threading.Condition(); self.active = False
            def __enter__(self):
                name = threading.current_thread().name
                with self.condition:
                    while self.active or not order or order[0] != name:
                        self.condition.wait()
                    order.pop(0); self.active = True
                return self
            def __exit__(self, *_args):
                with self.condition:
                    self.active = False; self.condition.notify_all()
        return OrderedLock()

    def test_revision_bound_invalidation_wins_before_consume_without_source_fallback(self):
        resolver, lease = self._revision_bound_lease()
        lease._lock = self._ordered_lock(["invalidate", "consume"])
        start = threading.Event(); errors = []; delivered = []
        def invalidate(): start.wait(); lease.invalidate()
        def consume():
            start.wait()
            try: lease.consume(lambda value: delivered.append(value) or {})
            except Exception as exc: errors.append(exc)
        threads = [threading.Thread(target=invalidate, name="invalidate"),
                   threading.Thread(target=consume, name="consume")]
        for thread in threads: thread.start()
        start.set()
        for thread in threads: thread.join(timeout=2)
        self.assertEqual(delivered, []); self.assertEqual(resolver.reads, 0)
        self.assertEqual(getattr(errors[0], "code", None), "lease_revoked")
        self.assertIsNone(lease._material)

    def test_revision_bound_consume_detaches_before_concurrent_invalidation(self):
        resolver, lease = self._revision_bound_lease()
        lease._lock = self._ordered_lock(["consume", "invalidate"])
        start = threading.Event(); invalidated = threading.Event(); delivered = []; results = []
        def consume():
            start.wait()
            def callback(value):
                delivered.append(value); invalidated.wait(timeout=2); return {"ok": True}
            results.append(lease.consume(callback))
        def invalidate():
            start.wait(); lease.invalidate(); invalidated.set()
        threads = [threading.Thread(target=consume, name="consume"),
                   threading.Thread(target=invalidate, name="invalidate")]
        for thread in threads: thread.start()
        start.set()
        for thread in threads: thread.join(timeout=2)
        self.assertEqual(delivered, [b"REVISION_BOUND_SNAPSHOT"])
        self.assertEqual(results, [{"ok": True}]); self.assertEqual(resolver.reads, 0)
        self.assertIsNone(lease._material)


if __name__ == "__main__":
    unittest.main()
