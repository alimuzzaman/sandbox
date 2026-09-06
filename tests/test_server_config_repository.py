from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest


class ServerConfigRepositoryTests(unittest.TestCase):
    def test_read_only_observation_does_not_create_state(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "server-config"
            repository = ServerConfigRepository(base, "inc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
            self.assertEqual(repository.observe(), {"status": "absent"})
            self.assertFalse(base.exists())

    def test_initialize_creates_owner_only_incarnation_tree(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = ServerConfigRepository(
                Path(directory) / "server-config", "inc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            )
            repository.initialize()
            for path in (repository.root, repository.fragments_dir, repository.generations_dir):
                self.assertTrue(path.is_dir())
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o700)

    def test_fragment_generation_and_state_are_immutable_or_atomic(self):
        from sandbox.server_config.models import ServerConfigFragment, ServerType
        from sandbox.server_config.repository import ServerConfigRepository
        from datetime import datetime, timezone

        with tempfile.TemporaryDirectory() as directory:
            repository = ServerConfigRepository(
                Path(directory) / "server-config", "inc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            )
            content_id = repository.store_fragment(b"set $cache 1;\n")
            self.assertTrue(content_id.startswith("sha256:"))
            model = ServerConfigFragment.create(
                name="page-cache", authority="wordpress-cache-v1",
                server_type=ServerType.NGINX, content=b"set $cache 1;\n",
                content_locator=(
                    "fragments/"
                    + content_id.removeprefix("sha256:")
                    + ".fragment"
                ),
                instance_incarnation_id="inc_" + "1" * 32,
                created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
                policy_revision="wordpress-cache-v1/nginx/1",
            )
            self.assertEqual(model.content_id, content_id)
            self.assertEqual(repository.read_fragment(content_id), b"set $cache 1;\n")
            generation_id = repository.publish_generation(
                {"combined.conf": b"set $cache 1;\n"},
                {
                    "schema": 1,
                    "fragment_set_id": "sha256:" + "1" * 64,
                    "renderer_revision": "nginx/1",
                },
            )
            self.assertTrue(generation_id.startswith("sha256:"))
            generation = repository.generations_dir / generation_id.removeprefix("sha256:")
            self.assertTrue((generation / "manifest.json").is_file())
            self.assertEqual((generation / "combined.conf").read_bytes(), b"set $cache 1;\n")
            self.assertEqual(os.stat(generation / "manifest.json").st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(generation / "combined.conf").st_mode & 0o222, 0)
            with self.assertRaisesRegex(ValueError, "generation_immutable"):
                repository.publish_generation(
                    {"combined.conf": b"changed\n"},
                    {
                        "schema": 1,
                        "fragment_set_id": "sha256:" + "1" * 64,
                        "renderer_revision": "nginx/1",
                    },
                    generation_id=generation_id,
                )
            with self.assertRaisesRegex(ValueError, "generation_immutable"):
                repository.publish_generation(
                    {"combined.conf": b"set $cache 1;\n"},
                    {
                        "schema": 1,
                        "fragment_set_id": "sha256:" + "1" * 64,
                        "renderer_revision": "nginx/1",
                    },
                    generation_id="sha256:" + "f" * 64,
                )
            state = {"schema": 1, "generation_id": generation_id}
            repository.write_state(state)
            self.assertEqual(repository.read_state(), state)
            self.assertEqual(os.stat(repository.state_path).st_mode & 0o777, 0o600)

            transaction = {"schema": 1, "phase": "prepared"}
            receipt = {"schema": 1, "generation_id": generation_id}
            repository.write_transaction(transaction)
            repository.write_receipt(receipt)
            self.assertEqual(repository.read_transaction(), transaction)
            self.assertEqual(repository.read_receipt(), receipt)
            self.assertEqual(repository.transaction_path.name, "transaction.json")

    def test_incarnation_validation_prevents_cross_root_adoption(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with self.assertRaisesRegex(ValueError, "instance_incarnation_invalid"):
                ServerConfigRepository(base, "../other")
            root = base / "server-config" / "inc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            root.parent.mkdir(parents=True)
            outside = base / "outside"
            outside.mkdir()
            root.symlink_to(outside, target_is_directory=True)
            repository = ServerConfigRepository(
                base / "server-config", "inc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            )
            with self.assertRaisesRegex(ValueError, "repository_unsafe"):
                repository.initialize()

    def test_corrupt_state_is_reported_without_repair(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = ServerConfigRepository(
                Path(directory) / "server-config", "inc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            )
            repository.initialize()
            repository.state_path.write_text("not-json")
            repository.state_path.chmod(0o600)
            before = repository.state_path.stat().st_mtime_ns
            with self.assertRaisesRegex(ValueError, "state_corrupt"):
                repository.read_state()
            self.assertEqual(repository.state_path.stat().st_mtime_ns, before)

    def test_content_and_generation_tampering_fail_closed(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = ServerConfigRepository(
                Path(directory) / "server-config", "inc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            )
            content_id = repository.store_fragment(b"original\n")
            fragment_path = (
                repository.fragments_dir
                / (content_id.removeprefix("sha256:") + ".fragment")
            )
            fragment_path.write_bytes(b"altered\n")
            fragment_path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "fragment_corrupt"):
                repository.read_fragment(content_id)

            generation_id = repository.publish_generation(
                {"combined.conf": b"safe\n"},
                {
                    "schema": 1,
                    "fragment_set_id": "sha256:" + "1" * 64,
                    "renderer_revision": "nginx/1",
                },
            )
            generation = repository.generations_dir / generation_id.removeprefix("sha256:")
            unexpected = generation / "unexpected.conf"
            unexpected.write_bytes(b"not manifested\n")
            unexpected.chmod(0o400)
            with self.assertRaisesRegex(ValueError, "generation_immutable"):
                repository.publish_generation(
                    {"combined.conf": b"safe\n"},
                    {
                        "schema": 1,
                        "fragment_set_id": "sha256:" + "1" * 64,
                        "renderer_revision": "nginx/1",
                    },
                )

    def test_repository_refuses_wrong_file_modes_without_repair(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = ServerConfigRepository(
                Path(directory) / "server-config", "inc_" + "1" * 32
            )
            repository.write_state({"schema": 1})
            repository.state_path.chmod(0o400)
            with self.assertRaisesRegex(ValueError, "repository_unsafe"):
                repository.read_state()
            self.assertEqual(os.stat(repository.state_path).st_mode & 0o777, 0o400)

    def test_context_incarnation_is_the_only_repository_identity_format(self):
        from sandbox.server_config.context import project_mount
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            incarnation = "inc_" + "1" * 32
            mount = project_mount(Path(directory), incarnation)
            repository = ServerConfigRepository(Path(directory), incarnation)
            self.assertEqual(repository.root, mount.source_root)
            with self.assertRaisesRegex(ValueError, "instance_incarnation_invalid"):
                ServerConfigRepository(Path(directory), "sci_" + "1" * 32)

    def test_retention_keeps_every_state_and_transaction_reference(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = ServerConfigRepository(
                Path(directory) / "server-config", "inc_" + "1" * 32
            )
            first = repository.publish_generation(
                {"combined.conf": b"first\n"},
                {
                    "schema": 1,
                    "fragment_set_id": "sha256:" + "1" * 64,
                    "renderer_revision": "nginx/1",
                },
            )
            second = repository.publish_generation(
                {"combined.conf": b"second\n"},
                {
                    "schema": 1,
                    "fragment_set_id": "sha256:" + "2" * 64,
                    "renderer_revision": "nginx/1",
                },
            )
            repository.write_state({"schema": 1, "generation_id": first})
            repository.write_transaction({
                "schema": 1, "prior_generation_id": first,
                "candidate_generation_id": second, "phase": "prepared",
            })
            with repository.locked() as mutation:
                self.assertEqual(mutation.prune_unreferenced_generations(), ())

            with repository.locked() as mutation:
                mutation.write_transaction({
                    "schema": 1, "prior_generation_id": first,
                    "candidate_generation_id": second, "phase": "committed",
                    "terminal": "active",
                })
                mutation.clear_transaction()
                self.assertEqual(mutation.prune_unreferenced_generations(), (second,))
            self.assertTrue(
                (repository.generations_dir / first.removeprefix("sha256:")).is_dir()
            )
            self.assertFalse(
                (repository.generations_dir / second.removeprefix("sha256:")).exists()
            )

    def test_retention_refuses_unknown_schema_before_deleting(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = ServerConfigRepository(
                Path(directory) / "server-config", "inc_" + "1" * 32
            )
            generation = repository.publish_generation(
                {"combined.conf": b"safe\n"},
                {
                    "schema": 1,
                    "fragment_set_id": "sha256:" + "1" * 64,
                    "renderer_revision": "nginx/1",
                },
            )
            repository.write_state({"schema": 99})
            with repository.locked() as mutation:
                with self.assertRaisesRegex(ValueError, "state_corrupt"):
                    mutation.prune_unreferenced_generations()
            self.assertTrue(
                (repository.generations_dir / generation.removeprefix("sha256:")).is_dir()
            )

    def test_retention_refuses_tampered_generation_before_deleting(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = ServerConfigRepository(
                Path(directory) / "server-config", "inc_" + "1" * 32
            )
            generation = repository.publish_generation(
                {"combined.conf": b"safe\n"},
                {
                    "schema": 1,
                    "fragment_set_id": "sha256:" + "1" * 64,
                    "renderer_revision": "nginx/1",
                },
            )
            repository.write_state({"schema": 1})
            generation_path = (
                repository.generations_dir / generation.removeprefix("sha256:")
            )
            unexpected = generation_path / "unexpected.conf"
            unexpected.write_bytes(b"tamper\n")
            unexpected.chmod(0o400)
            with repository.locked() as mutation:
                with self.assertRaisesRegex(ValueError, "generation_immutable"):
                    mutation.prune_unreferenced_generations()
            self.assertTrue(generation_path.is_dir())

            unexpected.unlink()
            invalid_later = repository.generations_dir / "zz-invalid"
            invalid_later.mkdir(mode=0o700)
            with repository.locked() as mutation:
                with self.assertRaisesRegex(ValueError, "repository_unsafe"):
                    mutation.prune_unreferenced_generations()
            self.assertTrue(generation_path.is_dir())

    def test_recovery_needed_without_exact_generation_refs_cannot_prune(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = ServerConfigRepository(
                Path(directory) / "server-config", "inc_" + "1" * 32
            )
            generation = repository.publish_generation(
                {"combined.conf": b"safe\n"},
                {
                    "schema": 1,
                    "fragment_set_id": "sha256:" + "1" * 64,
                    "renderer_revision": "nginx/1",
                },
            )
            repository.write_state({"schema": 1})
            repository.write_transaction({"schema": 1, "terminal": "recovery_needed"})
            with repository.locked() as mutation:
                with self.assertRaisesRegex(ValueError, "state_corrupt"):
                    mutation.prune_unreferenced_generations()
            self.assertTrue(
                (repository.generations_dir / generation.removeprefix("sha256:")).is_dir()
            )

    def test_clear_transaction_refuses_corrupt_nonterminal_and_recovery_needed_state(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = ServerConfigRepository(
                Path(directory) / "server-config", "inc_" + "1" * 32
            )
            first = "sha256:" + "1" * 64
            second = "sha256:" + "2" * 64
            records = (
                {"schema": 1, "phase": "prepared",
                 "prior_generation_id": first, "candidate_generation_id": second},
                {"schema": 1, "terminal": "recovery_needed",
                 "prior_generation_id": first, "candidate_generation_id": second},
            )
            for record in records:
                with self.subTest(record=record):
                    repository.write_transaction(record)
                    with repository.locked() as mutation:
                        with self.assertRaisesRegex(ValueError, "transaction_not_clearable"):
                            mutation.clear_transaction()
                    self.assertEqual(repository.read_transaction(), record)

            repository.transaction_path.write_bytes(b"not-json\n")
            repository.transaction_path.chmod(0o600)
            with repository.locked() as mutation:
                with self.assertRaisesRegex(ValueError, "state_corrupt"):
                    mutation.clear_transaction()
            self.assertTrue(repository.transaction_path.exists())

    def test_locked_mutations_use_the_held_incarnation_root_descriptor(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = ServerConfigRepository(
                Path(directory) / "server-config", "inc_" + "1" * 32
            )
            repository.initialize()
            detached = repository.base / ("inc_" + "2" * 32)
            with repository.locked() as mutation:
                repository.root.rename(detached)
                repository.root.mkdir(mode=0o700)
                (repository.root / "fragments").mkdir(mode=0o700)
                (repository.root / "generations").mkdir(mode=0o700)
                mutation.write_state({"schema": 1})
                self.assertEqual(mutation.read_state(), {"schema": 1})
            self.assertFalse((repository.root / "state.json").exists())
            self.assertEqual(json.loads((detached / "state.json").read_text()), {"schema": 1})

    def test_existing_unsafe_permissions_are_refused_not_repaired(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "server-config"
            base.mkdir(mode=0o755)
            repository = ServerConfigRepository(base, "inc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
            with self.assertRaisesRegex(ValueError, "repository_unsafe"):
                repository.initialize()
            self.assertEqual(os.stat(base).st_mode & 0o777, 0o755)

    def test_incarnation_lock_refuses_an_overlapping_mutation_without_writes(self):
        from sandbox.server_config.repository import ServerConfigRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = ServerConfigRepository(
                Path(directory) / "server-config", "inc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            )
            with repository.locked():
                with self.assertRaisesRegex(ValueError, "operation_conflict"):
                    with repository.locked():
                        self.fail("overlapping lock unexpectedly acquired")


if __name__ == "__main__":
    unittest.main()
