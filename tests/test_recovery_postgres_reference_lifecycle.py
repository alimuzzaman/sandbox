import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.recovery import postgres_helper as helper
from tests.test_recovery_postgres_helper import source
from tests.test_recovery_postgres_reference import evidence, reference_for, schema_records


class ReferenceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.slot = Path(self.temporary.name).resolve() / ('a' * 64)
        self.slot.mkdir(mode=0o700)
        self.source = source()
        self.evidence = evidence()
        self.archive_digest = 'sha256:' + 'e' * 64
        self.binding = helper._schema_binding(self.source, self.evidence, self.archive_digest,
            self.slot.name, 'lenzora', 'postgres')
        self.reference = reference_for(self.source, self.evidence,
            native_request_id=self.slot.name, archive_digest=self.archive_digest)
        self.dump = self.slot / 'database.dump'
        helper.private_write(self.dump, b'PGDMP synthetic unit fixture')

    def invoke(self):
        return helper._schema_reference(self.source, self.evidence, self.dump, self.binding,
            self.slot, self.evidence['schema_structure_digest'])

    @staticmethod
    def checkpoint_payload(*, counts=None, migration_checksum='a' * 32,
                            records=None, database_identity='b' * 32):
        records = copy.deepcopy(records or schema_records())
        counts = copy.deepcopy(counts or [{'name': 'orders', 'count': 3}])
        raw = {
            'major': 16,
            'database_identity': database_identity,
            **records,
        }
        lines = [json.dumps(['raw', raw], separators=(',', ':'))]
        lines.extend(json.dumps(['count', row], separators=(',', ':')) for row in counts)
        lines.append(json.dumps(['migrations', migration_checksum], separators=(',', ':')))
        lines.append(json.dumps(['comparison', records], separators=(',', ':')))
        return '\n'.join(lines)

    @staticmethod
    def checkpoint_actual(*, counts=None, migration_checksum='a' * 32,
                          records=None, database_identity='b' * 32):
        records = copy.deepcopy(records or schema_records())
        counts = copy.deepcopy(counts or [{'name': 'orders', 'count': 3}])
        return {
            'major': 16,
            'database_identity': database_identity,
            'table_counts': counts,
            'migration_checksum': migration_checksum,
            'schema_digest': helper._schema_digest(records),
            'schema_fingerprint_version': 2,
            'schema_structure_digest': helper.schema_structure_digest(records),
            'constraints_valid': True,
        }

    def evidence_for_checkpoint(self, actual):
        value = copy.deepcopy(self.evidence)
        for key in (
                'major', 'database_identity', 'table_counts', 'migration_checksum',
                'schema_digest', 'schema_fingerprint_version', 'schema_structure_digest',
                'constraints_valid'):
            value[key] = copy.deepcopy(actual[key])
        return value

    def test_verification_checkpoint_parses_closed_tags_in_one_read_only_transaction(self):
        records = schema_records()
        payload = self.checkpoint_payload(migration_checksum='c' * 32)
        with patch.object(helper, 'sql', return_value=payload) as query:
            actual, comparison = helper.verification_checkpoint(['client'], 'lenzora')

        self.assertEqual(actual, self.checkpoint_actual(migration_checksum='c' * 32))
        self.assertEqual(comparison, helper._stable_schema(records))
        query.assert_called_once()
        statement = query.call_args.args[2]
        self.assertEqual(statement.count('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;'), 1)
        self.assertEqual(statement.count('COMMIT;'), 1)
        self.assertNotRegex(statement,
                            r'(?im)\b(?:CREATE|ALTER|DROP|TRUNCATE|INSERT|UPDATE|DELETE)\b')

    def test_verify_observation_uses_fresh_checkpoint_for_count_schema_and_migration(self):
        base = self.checkpoint_actual()
        evidence = self.evidence_for_checkpoint(base)
        stale = copy.deepcopy(base)
        stable_records = helper._stable_schema(schema_records())
        cases = (
            ('count', {'table_counts': [{'name': 'orders', 'count': 4}]}),
            ('schema', {'schema_structure_digest': 'sha256:' + 'c' * 64}),
            ('migration', {'migration_checksum': 'd' * 32}),
        )

        for name, changes in cases:
            with self.subTest(change=name):
                fresh = {**base, **changes}
                with patch.object(helper, 'verification_checkpoint',
                                  return_value=(fresh, stable_records)), \
                        patch.object(helper, '_schema_reference',
                                     side_effect=AssertionError('proof path must not run')):
                    with self.assertRaisesRegex(ValueError, 'restore_verification_failed'):
                        helper.verify_observation(
                            self.source, evidence, stale, ['client'], 'lenzora', 'postgres',
                            self.dump, self.dump, self.slot)

    def test_fresh_count_or_migration_drift_leaves_no_verify_result(self):
        base = self.checkpoint_actual()
        evidence = self.evidence_for_checkpoint(base)
        stable_records = helper._stable_schema(schema_records())

        for name, changes in (
                ('count', {'table_counts': [{'name': 'orders', 'count': 4}]}),
                ('migration', {'migration_checksum': 'd' * 32})):
            with self.subTest(change=name), tempfile.TemporaryDirectory() as directory:
                identity = 'f' * 64
                slot = Path(directory) / identity
                slot.mkdir(mode=0o700)
                fresh = {**base, **changes}
                row = {'Id': 'b' * 64, 'State': {'Running': True}}

                def inspect(_source, _archive, work, _name, *, slot=None):
                    helper.private_write(work / 'evidence.json', helper.canonical(evidence))
                    helper.private_write(work / 'database.dump', b'PGDMP synthetic')
                    return {'database_available': True, 'container_id': row['Id'],
                            'observation': copy.deepcopy(base)}

                with patch.object(helper, 'inspect_restore', side_effect=inspect), \
                        patch.object(helper, 'restore_target', return_value=(row, {})), \
                        patch.object(helper, 'sql', return_value='0'), \
                        patch.object(helper, 'verification_checkpoint',
                                     return_value=(fresh, stable_records)):
                    with self.assertRaisesRegex(ValueError, 'restore_verification_failed'):
                        helper._execute(
                            {}, self.source, 'verify-restore', identity, slot, b'',
                            b'synthetic archive')

                self.assertFalse((slot / 'result.json').exists())

    def test_reference_path_refreshes_checkpoint_before_rejecting_non_schema_change(self):
        base = self.checkpoint_actual()
        evidence = self.evidence_for_checkpoint(base)
        evidence['schema_digest'] = 'sha256:' + 'e' * 64
        first = copy.deepcopy(base)
        refreshed = {**base, 'table_counts': [{'name': 'orders', 'count': 4}]}
        stable_records = helper._stable_schema(schema_records())
        archive_digest = 'sha256:' + hashlib.sha256(self.dump.read_bytes()).hexdigest()
        reference = reference_for(
            self.source, evidence, native_request_id=self.slot.name,
            archive_digest=archive_digest,
            reference_schema_digest=base['schema_digest'])
        with patch.object(
                helper, 'verification_checkpoint',
                side_effect=((first, stable_records), (refreshed, stable_records))) as checkpoint, \
                patch.object(helper, '_schema_reference', return_value=reference) as create_reference:
            with self.assertRaisesRegex(ValueError, 'restore_verification_failed'):
                helper.verify_observation(
                    self.source, evidence, copy.deepcopy(base), ['client'], 'lenzora', 'postgres',
                    self.dump, self.dump, self.slot)

        self.assertEqual(checkpoint.call_count, 2)
        create_reference.assert_called_once()

    def test_completed_reference_is_reused_without_source_or_container_actions(self):
        helper.private_write(self.slot / 'schema-reference.json', helper.canonical(self.reference))
        with patch.object(helper, 'run', side_effect=AssertionError('no runtime action')), \
                patch.object(helper, '_captured_structure', side_effect=AssertionError('no source read')):
            self.assertEqual(self.invoke(), self.reference)

    def test_wrong_binding_and_unsafe_cached_file_fail_before_runtime_actions(self):
        path = self.slot / 'schema-reference.json'
        for field, value in (('archive_digest', 'sha256:' + 'f' * 64),
                             ('image_id', 'sha256:' + 'f' * 64),
                             ('native_request_id', 'f' * 64), ('schema_version', True)):
            with self.subTest(field=field):
                changed = {**self.reference, field: value}
                helper.private_write(path, helper.canonical(changed))
                with patch.object(helper, 'run', side_effect=AssertionError('no runtime action')):
                    with self.assertRaisesRegex(ValueError, 'schema_evidence_invalid'):
                        self.invoke()
                path.unlink()
        helper.private_write(path, helper.canonical(self.reference)); path.chmod(0o644)
        with patch.object(helper, 'run', side_effect=AssertionError('no runtime action')):
            with self.assertRaisesRegex(ValueError, 'path_unsafe'): self.invoke()

    def test_uncertain_creation_is_not_repeated_even_when_no_container_is_observed(self):
        commands = []
        def command(argv, **kwargs):
            commands.append(argv)
            if argv[:2] == ['docker', 'create']:
                raise subprocess.TimeoutExpired('synthetic-create', 1)
            return b''
        with patch.object(helper, 'run', side_effect=command):
            with self.assertRaises(subprocess.TimeoutExpired): self.invoke()
        self.assertTrue((self.slot / 'schema-reference-intent.json').is_file())
        self.assertFalse((self.slot / 'schema-reference.json').exists())
        with patch.object(helper, 'run', side_effect=AssertionError('uncertain creation must not repeat')):
            with self.assertRaisesRegex(ValueError, 'schema_reference_pending'): self.invoke()
        self.assertEqual(sum(argv[:2] == ['docker', 'create'] for argv in commands), 1)

    def test_existing_reference_name_and_structural_mismatch_refuse_before_creation(self):
        with patch.object(helper, 'run', return_value=b'foreign-container') as command:
            with self.assertRaisesRegex(ValueError, 'schema_reference_pending'): self.invoke()
        self.assertFalse(any(call.args[0][:2] == ['docker', 'create'] for call in command.call_args_list))
        self.assertFalse((self.slot / 'schema-reference-intent.json').exists())
        with patch.object(helper, '_captured_structure', return_value=('sha256:' + 'f' * 64, 'capture-v2')), \
                patch.object(helper, 'run', side_effect=AssertionError('no runtime action')):
            with self.assertRaisesRegex(ValueError, 'restore_verification_failed'): self.invoke()

    def test_cleanup_failure_cannot_leave_a_completed_reference(self):
        def command(argv, **kwargs):
            return b'b' * 64 if argv[:2] == ['docker', 'create'] else b''
        with patch.object(helper, 'run', side_effect=command), \
                patch.object(helper, '_reference_container'), patch.object(helper, 'sql', return_value='1'), \
                patch.object(helper, 'schema_records', return_value=schema_records()), \
                patch.object(helper.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                patch.object(helper, '_reference_cleanup', side_effect=ValueError('schema_reference_changed')):
            with self.assertRaisesRegex(ValueError, 'schema_reference_cleanup_failed'): self.invoke()
        self.assertTrue((self.slot / 'schema-reference-intent.json').exists())
        self.assertFalse((self.slot / 'schema-reference.json').exists())

    def test_target_change_during_reference_work_is_rechecked_before_acceptance(self):
        actual = {key: value for key, value in self.evidence.items() if key != 'dump_digest'}
        actual['schema_digest'] = 'sha256:' + 'f' * 64
        archive = self.slot / 'capture.tar'; helper.private_write(archive, b'synthetic archive')
        with patch.object(helper, '_schema_reference', return_value=copy.deepcopy(self.reference)), \
                patch.object(helper, 'verification_checkpoint',
                             side_effect=AssertionError('changed target must not be queried')):
            def changed(): raise ValueError('restore_target_changed')
            with self.assertRaisesRegex(ValueError, 'restore_target_changed'):
                helper.verify_observation(self.source, self.evidence, actual, ['target'],
                    'lenzora', 'postgres', archive, self.dump, self.slot, recheck=changed)


if __name__ == '__main__':
    unittest.main()
