"""Large schemas and the closed failure diagnostic of the PostgreSQL helper."""
import io
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sandbox.recovery import postgres_helper as helper
from sandbox.recovery.errors import RecoveryError
from tests.test_recovery_infisical_profile import infisical
from tests.test_remote_postgres_recovery import RemotePostgresRecoveryTests, source

CANARY = 'private-row-canary'


def partitioned_schema(columns):
    """A schema shaped like Infisical's partitioned audit log."""
    return {
        'constraints': [{'table': 'audit_logs_%04d' % (index // 20), 'name': 'audit_logs_%04d_pkey' % (index // 20),
                         'definition': 'PRIMARY KEY (id, "createdAt")', 'validated': True}
                        for index in range(0, columns, 20)],
        'columns': [{'table': 'audit_logs_%04d' % (index // 20), 'column': 'column_%02d' % (index % 20),
                     'type': 'timestamp with time zone', 'nullable': 'YES'} for index in range(columns)],
    }


class LargeSchemaTests(unittest.TestCase):
    def setUp(self):
        helper._FAILURE_DETAIL = None

    def test_observation_accepts_more_than_ten_thousand_columns(self):
        raw = {'major': 16, 'database_identity': 'a' * 32,
               'tables': [{'name': 'audit_logs_0000', 'count': 3}], **partitioned_schema(15380)}
        with patch.object(helper, 'sql', side_effect=[json.dumps(raw), 'absent']):
            evidence = helper.observation([], 'infisical')
        self.assertEqual(evidence['major'], 16)
        self.assertTrue(evidence['constraints_valid'])
        self.assertEqual(evidence['migration_checksum'], 'absent')
        self.assertRegex(evidence['schema_structure_digest'], r'^sha256:[a-f0-9]{64}$')

    def test_row_and_byte_bounds_still_refuse_and_name_the_check(self):
        too_many = partitioned_schema(1)
        too_many['columns'] = too_many['columns'] * (helper.MAX_SCHEMA_ROWS + 1)
        duplicate = partitioned_schema(2)
        duplicate['columns'].append(duplicate['columns'][0])
        oversized = partitioned_schema(2)
        oversized['constraints'] = [dict(oversized['constraints'][0], name='c%05d' % index,
                                         definition='CHECK (' + 'x' * 60000 + ')') for index in range(150)]
        for value, check in ((too_many, 'columns.row_bound'), (duplicate, 'columns.duplicate'),
                             (oversized, 'schema_size_bound')):
            with self.subTest(check=check):
                with self.assertRaisesRegex(ValueError, '^schema_metadata_invalid$'):
                    helper._schema_projection(value)
                self.assertEqual(helper._FAILURE_DETAIL, check)


class HelperFailureLineTests(unittest.TestCase):
    def run_safe_main(self, main):
        stderr, stdout = io.StringIO(), io.BytesIO()
        with patch.object(helper, 'main', main), patch.object(helper.sys, 'stderr', stderr), \
                patch.object(helper.sys, 'stdout', SimpleNamespace(buffer=stdout)):
            exit_code = helper.safe_main()
        return exit_code, stderr.getvalue(), stdout.getvalue()

    def test_failed_command_names_step_tool_and_exit_without_output(self):
        def main():
            helper._set_failure_step('database_observation')
            with patch.object(helper.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=2, stdout=CANARY.encode())):
                helper.sql(helper.local_client(infisical()), 'infisical', 'SELECT 1;')
        exit_code, stderr, stdout = self.run_safe_main(main)
        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr, 'postgres_recovery_failed code=operation_failed '
                                 'step=database_observation command=psql exit=2\n')
        self.assertEqual(stdout, b'')

    def test_schema_refusal_names_its_check(self):
        def main():
            helper._set_failure_step('database_observation')
            value = partitioned_schema(1)
            value['columns'] *= helper.MAX_SCHEMA_ROWS + 1
            helper._schema_projection(value)
        self.assertEqual(self.run_safe_main(main)[1],
            'postgres_recovery_failed code=schema_metadata_invalid step=database_observation '
            'detail=columns.row_bound\n')

    def test_unexpected_errors_map_to_closed_reasons_without_their_text(self):
        for error, reason in ((KeyError(CANARY), 'output_invalid'), (ValueError(CANARY), 'output_invalid'),
                              (PermissionError(CANARY), 'io_failed'), (RuntimeError(CANARY), 'internal_error')):
            def main(error=error):
                raise error
            with self.subTest(reason=reason):
                exit_code, stderr, _stdout = self.run_safe_main(main)
                self.assertEqual(exit_code, 1)
                self.assertEqual(stderr, 'postgres_recovery_failed code=%s step=request_validation\n' % reason)
                self.assertNotIn(CANARY, stderr)

    def test_each_run_starts_without_a_previous_failure(self):
        def first():
            helper._set_failure_step('dump')
            with patch.object(helper.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout=b'')):
                helper.run(['docker', 'exec', 'x', 'pg_dump'])
        self.run_safe_main(first)
        def second():
            raise ValueError('operation_failed')
        self.assertEqual(self.run_safe_main(second)[1],
            'postgres_recovery_failed code=operation_failed step=request_validation\n')


class TransportFailureTests(unittest.TestCase):
    def invoke(self, **result):
        process = lambda *_args, **_kwargs: SimpleNamespace(**{'stdout': b'', 'stderr': b'', **result})
        transport = RemotePostgresRecoveryTests._transport(self, process=process)
        with self.assertRaises(RecoveryError) as raised:
            transport.invoke(source(), 'observe', 'a' * 64)
        return raised.exception

    def test_helper_failure_line_becomes_message_and_detail(self):
        error = self.invoke(returncode=1, stderr=b'Welcome banner\npostgres_recovery_failed code=schema_metadata_invalid '
                                                 b'step=database_observation detail=columns.row_bound\n')
        self.assertEqual(error.code, 'acceptance_unknown')
        self.assertEqual(error.detail, {'helper_code': 'schema_metadata_invalid', 'step': 'database_observation',
                                        'check': 'columns.row_bound'})
        self.assertEqual(str(error), 'PostgreSQL helper stopped at database_observation '
                                     '(schema_metadata_invalid; check columns.row_bound); the retained request is preserved')
        error = self.invoke(returncode=1, stderr='postgres_recovery_failed code=operation_failed step=dump '
                                                 'command=pg_dump exit=1\n')
        self.assertEqual(error.detail, {'helper_code': 'operation_failed', 'step': 'dump',
                                        'command': 'pg_dump', 'exit': 1})

    def test_unrecognised_stderr_stays_generic_and_private(self):
        for result, detail in (({'returncode': 1, 'stderr': ('postgres_recovery_failed code=x step=y ' + CANARY).encode()},
                                {'helper_code': 'helper_exited', 'exit': 1}),
                               ({'returncode': 255, 'stderr': b'ssh: connect to host failed'},
                                {'helper_code': 'helper_exited', 'exit': 255}),
                               ({'returncode': 0, 'stdout': b''}, {'helper_code': 'empty_response'})):
            with self.subTest(result=result):
                error = self.invoke(**result)
                self.assertEqual(error.code, 'acceptance_unknown')
                self.assertEqual(str(error), 'PostgreSQL operation requires retained-request inspection')
                self.assertEqual(error.detail, detail)
                self.assertNotIn(CANARY, str(error) + json.dumps(error.detail))


class CliFailureMessageTests(unittest.TestCase):
    def payload(self, side_effect):
        from sandbox.commands.recovery import _postgres
        recovery = Mock()
        recovery.observe.side_effect = side_effect
        args = SimpleNamespace(postgres_operation='observe', reopen_plan=None, project_dir=None,
            resume_capture=False, source_binding=None, restore_plan=None, remote='xcloud-london',
            profile=['infisical'], request_id='observe-a', backup_id=None, target_volume=None, confirm=False)
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'SANDBOX_HOME': directory}), \
                patch('sandbox.recovery.postgres.PostgresRecovery', return_value=recovery), \
                patch('sandbox.transports.remote_postgres_recovery.RegisteredPostgresRecoveryTransport'):
            return _postgres({}, args, SimpleNamespace(capture=None, catalog=None))

    def test_cli_reports_the_helper_step_and_reason(self):
        error = RecoveryError('PostgreSQL helper stopped at database_observation (schema_metadata_invalid; '
                              'check columns.row_bound); the retained request is preserved', 'acceptance_unknown')
        error.detail = {'helper_code': 'schema_metadata_invalid', 'step': 'database_observation',
                        'check': 'columns.row_bound'}
        payload = self.payload(error)
        self.assertEqual(payload['error']['code'], 'acceptance_unknown')
        self.assertEqual(payload['error']['message'], str(error))
        self.assertEqual(payload['error']['detail'], error.detail)

    def test_other_messages_stay_fixed(self):
        for error, code in ((RecoveryError('private ' + CANARY, 'observation_invalid'), 'observation_invalid'),
                            (OSError(CANARY), 'postgres_recovery_failed')):
            with self.subTest(code=code):
                payload = self.payload(error)
                self.assertEqual(payload['error']['code'], code)
                self.assertEqual(payload['error']['message'],
                                 'PostgreSQL recovery requires inspection; retained requests are preserved')
                self.assertNotIn('detail', payload['error'])
                self.assertNotIn(CANARY, json.dumps(payload))


if __name__ == '__main__':
    unittest.main()
