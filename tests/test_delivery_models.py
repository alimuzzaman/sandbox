"""Closed delivery documents and semantic request identity."""
from copy import deepcopy
import unittest
import uuid

from sandbox.delivery.models import (
    DeliveryError, intent_digest, new_operation, now, target_digest,
    validate_target,
)
from sandbox.hosting.recovery.models import (
    deployed_source_revision, validate_source_artifact,
)


def make_target(environment='qa'):
    return {
        'schema_version': 1, 'project_identity': 'fixture-project',
        'project_root_digest': 'sha256:' + '1' * 64,
        'remote_name': 'fixture-remote',
        'registered_host_digest': 'sha256:' + '2' * 64,
        'machine_identity': 'fixture-machine', 'target_kind': 'hosted',
        'environment': environment, 'label': None, 'instance_id': None,
        'instance_incarnation_id': None, 'workspace_id': None,
        'runtime_identity': 'fixture-runtime',
    }


def make_operation(key, *, stamp=None, environment='qa'):
    target = make_target(environment)
    outcome = {
        'kind': 'hosted_apply', 'target_digest': target_digest(target),
        'application': {
            'source_identity': 'fixture-app', 'commit': 'a' * 40,
            'dirty_policy': 'clean_required', 'dirty_digest': None,
            'artifact_digest': None, 'config_digest': None,
            'plan_digest': None, 'proof_digest': None,
        },
        'control': {
            'source_commit': 'b' * 40, 'source_runtime_revision': 'c' * 24,
            'installed_controller_runtime_revision': 'd' * 24,
            'capability_versions': {'delivery': 1},
        },
        'configuration_digest': None, 'route_contract_digest': None,
        'requirements': [], 'requested_at': stamp or now(),
    }
    return new_operation(target, outcome, str(uuid.uuid4()), key)


def make_terminal(operation, succeeded=True):
    result = deepcopy(operation)
    result.update(
        execution_state='succeeded' if succeeded else 'failed',
        delivery_state='succeeded' if succeeded else 'failed',
        delivery_succeeded=succeeded, evidence_completeness='complete',
        phase='terminal', finished_at=result['updated_at'],
    )
    return result


class DeliveryModelsTests(unittest.TestCase):
    def test_source_artifact_keeps_identity_and_subtree_revisions_closed(self):
        source_commit = 'a' * 40
        deployed_commit = 'b' * 40
        identity = {
            'schema_version': 1, 'kind': 'git_commit', 'root_relative': '.',
            'revision': source_commit,
        }
        subtree = {
            'schema_version': 1, 'kind': 'git_subtree', 'root_relative': 'site',
            'revision': deployed_commit,
        }

        self.assertEqual(validate_source_artifact(identity, source_commit), identity)
        self.assertEqual(validate_source_artifact(subtree, source_commit), subtree)
        self.assertEqual(deployed_source_revision({'commit': source_commit}), source_commit)
        self.assertEqual(deployed_source_revision({'commit': source_commit, 'artifact': subtree}), deployed_commit)
        original = make_operation('artifact-intent')['requested_outcome']
        nested = deepcopy(original)
        nested['application']['source_artifact'] = subtree
        self.assertNotEqual(intent_digest(original), intent_digest(nested))
        changed = deepcopy(nested)
        changed['application']['source_artifact']['revision'] = 'c' * 40
        self.assertNotEqual(intent_digest(nested), intent_digest(changed))

    def test_source_artifact_rejects_unsafe_shape_or_revision(self):
        source_commit = 'a' * 40
        valid = {
            'schema_version': 1, 'kind': 'git_subtree', 'root_relative': 'site',
            'revision': 'b' * 40,
        }
        invalid = (
            {**valid, 'root_relative': '../site'},
            {**valid, 'root_relative': '/site'},
            {**valid, 'root_relative': 'site//assets'},
            {**valid, 'root_relative': 'site/../assets'},
            {**valid, 'extra': 'rejected'},
            {key: value for key, value in valid.items() if key != 'revision'},
            {**valid, 'schema_version': True},
            {**valid, 'schema_version': 2},
            {**valid, 'revision': 'B' * 40},
            {**valid, 'revision': 'b' * 39},
            {**valid, 'kind': 'unknown'},
            {'schema_version': 1, 'kind': 'git_commit', 'root_relative': 'site',
             'revision': source_commit},
            {'schema_version': 1, 'kind': 'git_commit', 'root_relative': '.',
             'revision': valid['revision']},
        )
        for artifact in invalid:
            with self.subTest(artifact=artifact):
                with self.assertRaisesRegex(ValueError, 'source_artifact_invalid'):
                    validate_source_artifact(artifact, source_commit)

    def test_legacy_application_shape_has_no_invented_artifact_or_digest(self):
        operation = make_operation('legacy-request')
        application = operation['requested_outcome']['application']
        self.assertNotIn('source_artifact', application)
        self.assertEqual(set(application), {
            'source_identity', 'commit', 'dirty_policy', 'dirty_digest',
            'artifact_digest', 'config_digest', 'plan_digest', 'proof_digest',
        })
        self.assertEqual(deployed_source_revision({'commit': application['commit']}), application['commit'])

    def test_retry_clock_does_not_change_semantic_intent(self):
        first = make_operation('request', stamp='2026-01-01T00:00:00Z')
        later = make_operation('request', stamp='2026-01-02T00:00:00Z')
        self.assertEqual(first['intent_digest'], later['intent_digest'])
        self.assertNotEqual(first['accepted_at'], later['accepted_at'])
        changed = deepcopy(later['requested_outcome'])
        changed['application']['commit'] = 'e' * 40
        self.assertNotEqual(first['intent_digest'], intent_digest(changed))

    def test_schema_requires_an_integer_version(self):
        for version in (True, 1.0):
            with self.subTest(version=version):
                target = make_target()
                target['schema_version'] = version
                with self.assertRaises(DeliveryError) as error:
                    validate_target(target)
                self.assertEqual(error.exception.code, 'unsupported_capability')

    def test_identifier_bounds_and_credential_patterns_fail_closed(self):
        for identity in ('x' * 129, 'ghp_' + 'x' * 30):
            with self.subTest(identity_length=len(identity)):
                target = make_target()
                target['project_identity'] = identity
                with self.assertRaises(DeliveryError) as error:
                    validate_target(target)
                self.assertEqual(error.exception.code, 'delivery_contract_invalid')

    def test_owner_request_id_bounds_do_not_allow_unbounded_or_unsafe_text(self):
        for request_id in ('a' * 257, '/absolute-path', 'request with spaces', 'request\nnext',
                           'ghp_' + 'x' * 30):
            with self.subTest(request_length=len(request_id)):
                with self.assertRaises(DeliveryError):
                    make_operation(request_id)


if __name__ == '__main__':
    unittest.main()
