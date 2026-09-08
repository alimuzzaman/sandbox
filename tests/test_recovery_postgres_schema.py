import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from sandbox.recovery import postgres_helper as helper
from tests.test_recovery_postgres_helper import source


def records():
    return {
        'constraints': [
            {'table': 'alpha', 'name': 'alpha_check', 'definition': 'CHECK (value > 0)', 'validated': False},
            {'table': 'Beta', 'name': 'Beta_pkey', 'definition': 'PRIMARY KEY (id)', 'validated': True},
        ],
        'columns': [
            {'table': 'alpha', 'column': 'id', 'type': 'integer', 'nullable': 'NO'},
            {'table': 'alpha', 'column': 'value', 'type': 'text', 'nullable': 'YES'},
            {'table': 'Beta', 'column': 'id', 'type': 'integer', 'nullable': 'NO'},
        ],
    }


def digest(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class PostgresSchemaTests(unittest.TestCase):
    def compare(self, original, restored, *, captured=None, observed_target=None):
        calls = []
        def sql(client, database, query):
            calls.append((client, database, query))
            return json.dumps(restored if client == ['target'] else original)
        with patch.object(helper, 'sql', side_effect=sql), patch.object(helper, 'inspect_source') as inspect:
            result = helper.schema_diagnostic(source(), ['target'],
                captured or digest(original), observed_target or digest(restored))
        self.assertEqual(inspect.call_count, 2)
        self.assertTrue(all('pg_get_constraintdef' in call[2] for call in calls))
        return result

    def test_same_records_match_and_legacy_digest_keeps_record_order(self):
        value = records()
        result = self.compare(value, copy.deepcopy(value))
        self.assertEqual(result['code'], 'schema_compared')
        self.assertTrue(result['record_sets_equal'])
        self.assertTrue(result['column_order_equal'])
        self.assertFalse(result['ordering_only'])
        self.assertEqual(result['source_digest'], digest(value))
        self.assertEqual(result['target_digest'], digest(value))
        self.assertEqual(result['difference_count'], 0)

    def test_cross_table_order_is_distinguished_from_column_order(self):
        original = records(); restored = copy.deepcopy(original)
        restored['constraints'].reverse()
        restored['columns'] = restored['columns'][2:] + restored['columns'][:2]
        result = self.compare(original, restored)
        self.assertTrue(result['ordering_only'])
        self.assertTrue(result['column_order_equal'])
        self.assertNotEqual(result['source_digest'], result['target_digest'])
        self.assertFalse(result['components']['columns']['record_order_equal'])

        restored = copy.deepcopy(original)
        restored['columns'][0], restored['columns'][1] = restored['columns'][1], restored['columns'][0]
        result = self.compare(original, restored)
        self.assertTrue(result['record_sets_equal'])
        self.assertFalse(result['column_order_equal'])
        self.assertFalse(result['ordering_only'])
        self.assertEqual(result['differences'][0]['fields'], ['order'])

    def test_every_schema_field_change_is_reported_without_its_value(self):
        for kind, field, value in (
                ('constraints', 'definition', 'CHECK (value = SECRET_CANARY_84)'),
                ('constraints', 'validated', True),
                ('columns', 'type', 'text'), ('columns', 'nullable', 'YES')):
            with self.subTest(kind=kind, field=field):
                original = records(); restored = copy.deepcopy(original)
                restored[kind][0][field] = value
                result = self.compare(original, restored)
                self.assertFalse(result['record_sets_equal'])
                self.assertFalse(result['ordering_only'])
                self.assertEqual(result['difference_count'], 1)
                self.assertEqual(result['differences'][0]['fields'], [field])
                self.assertNotIn('SECRET_CANARY_84', json.dumps(result))
                self.assertNotIn('CHECK (', json.dumps(result))

    def test_definition_shape_redacts_identifiers_strings_and_numbers(self):
        value = 'CHECK ("private_identifier" = \'private_literal\' AND other_private_name > 987654)'
        shape = helper._definition_shape(value)
        rendered = json.dumps(shape)
        for private in ('private_identifier', 'private_literal', 'other_private_name', '987654'):
            self.assertNotIn(private, rendered)
        self.assertIn('AND', shape['syntax_labels'])
        self.assertIn('string', shape['syntax_labels'])
        self.assertIn('number', shape['syntax_labels'])
        self.assertTrue(helper._definition_shape('a ' * 600)['truncated'])
        from sandbox.recovery.errors import redact
        from sandbox.services.redaction import redact_structure
        self.assertEqual(redact_structure(redact(shape)), shape)

    def test_missing_and_extra_records_are_reported_and_examples_are_bounded(self):
        original = records(); restored = copy.deepcopy(original)
        restored['constraints'].pop(0)
        restored['columns'][0]['column'] = 'new_id'
        result = self.compare(original, restored)
        self.assertEqual(result['difference_count'], 3)
        self.assertEqual({item['change'] for item in result['differences']}, {'missing', 'extra'})

        restored = copy.deepcopy(original)
        restored['columns'] += [dict(restored['columns'][0], column='extra_' + str(index)) for index in range(40)]
        result = self.compare(original, restored)
        self.assertEqual(result['difference_count'], 40)
        self.assertEqual(len(result['differences']), 32)
        self.assertTrue(result['truncated'])

    def test_source_or_target_change_cannot_be_claimed_as_capture_comparison(self):
        original = records()
        changed = 'sha256:' + 'f' * 64
        result = self.compare(original, original, captured=changed)
        self.assertEqual(result['code'], 'source_schema_changed')
        self.assertFalse(result['source_matches_capture'])
        self.assertNotIn('record_sets_equal', result)
        result = self.compare(original, original, observed_target=changed)
        self.assertEqual(result['code'], 'target_schema_changed')
        self.assertNotIn('record_sets_equal', result)

    def test_schema_collection_rejects_duplicate_malformed_and_oversized_records(self):
        invalid = []
        duplicate = records(); duplicate['columns'].append(duplicate['columns'][0]); invalid.append(duplicate)
        malformed = records(); malformed['constraints'][0]['validated'] = 'false'; invalid.append(malformed)
        oversized = records(); oversized['columns'] *= 10001; invalid.append(oversized)
        for value in invalid:
            with self.subTest(value_type=type(value).__name__), patch.object(helper, 'sql', return_value=json.dumps(value)):
                with self.assertRaisesRegex(ValueError, 'schema_metadata_invalid'):
                    helper.schema_records([], 'database')
        with patch.object(helper, 'sql', return_value='{"constraints":[],"constraints":[],"columns":[]}'):
            with self.assertRaisesRegex(ValueError, 'schema_metadata_invalid'):
                helper.schema_records([], 'database')


if __name__ == '__main__':
    unittest.main()
