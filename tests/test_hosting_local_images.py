"""Focused regressions for locally built hosted-image receipts."""
from __future__ import annotations

import json
import shlex
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands import hosting as hosting_cmd
from sandbox.hosting import local_images
from tests.subprocess_support import synthetic_environment


def _receipt():
    service_digest = local_images.canonical_digest({"image": "app:test", "build": {}})
    body = {
        "schema": local_images.RECEIPT_SCHEMA,
        "project": "app", "environment": "development",
        "compose_project": "sandbox-host-app-development",
        "commit": "a" * 40, "source_revision": "a" * 40,
        "source_root_relative": ".", "platform": "linux/amd64",
        "built_at": "2026-10-16T00:00:00Z", "builder": {
            "engine_platform": "linux/amd64", "engine_version": None,
            "compose_version": None},
        "build_config_digest": local_images.canonical_digest({"web": service_digest}),
        "services": {"web": {"role": "primary", "image": "app:test",
                    "build_digest": service_digest, "source": "built",
                    "artifact": "image-1"}},
        "artifacts": {"image-1": {"refs": ["app:test"],
                    "image_id": "sha256:" + "1" * 64,
                    "builder_image_id": "sha256:" + "3" * 64,
                    "manifest_digest": "sha256:" + "2" * 64,
                    "config_digest": "sha256:" + "1" * 64,
                    "layers_digest": "sha256:" + "4" * 64,
                    "platform": "linux/amd64", "archive": "image-1.tar",
                    "archive_sha256": "sha256:" + "5" * 64,
                    "archive_bytes": 10}},
    }
    return local_images.seal_receipt(body)


class LocalImageReceiptTests(unittest.TestCase):
    def test_receipt_accepts_exact_portable_engine_id(self):
        receipt = _receipt()
        self.assertEqual(local_images.validate_receipt(receipt), receipt)
        observation = {"images": {"app:test": {"present": True,
            "id": "sha256:" + "1" * 64, "platform": "linux/amd64",
            "layers_digest": "sha256:" + "4" * 64, "repo_digests": []}}}
        mismatches, verified = local_images.compare_images(receipt, observation)
        self.assertEqual(mismatches, [])
        self.assertEqual(verified["app:test"]["id"], "sha256:" + "1" * 64)

    def test_manifest_or_config_alias_does_not_replace_exact_engine_id(self):
        receipt = _receipt()
        for observed_id in ("sha256:" + "2" * 64, "sha256:" + "3" * 64):
            observation = {"images": {"app:test": {"present": True,
                "id": observed_id, "platform": "linux/amd64",
                "layers_digest": "sha256:" + "4" * 64, "repo_digests": []}}}
            mismatches, _ = local_images.compare_images(receipt, observation)
            self.assertEqual(mismatches[0]["code"], "image_id_mismatch")

    def test_receipt_retains_distinct_store_specific_builder_id(self):
        receipt = _receipt()
        self.assertNotEqual(receipt["artifacts"]["image-1"]["builder_image_id"],
                            receipt["artifacts"]["image-1"]["image_id"])
        self.assertEqual(local_images.validate_receipt(receipt), receipt)

    def test_tagged_digest_reference_matches_normalized_repo_digest(self):
        receipt = _receipt()
        receipt["services"]["web"].update({
            "image": "docker.io/library/mysql:8.0@sha256:" + "a" * 64,
            "source": "pinned", "artifact": None})
        receipt["artifacts"] = {}
        receipt["build_config_digest"] = local_images.canonical_digest(
            {"web": receipt["services"]["web"]["build_digest"]})
        receipt = local_images.seal_receipt({
            key: value for key, value in receipt.items() if key != "receipt_digest"})
        observation = {"images": {receipt["services"]["web"]["image"]: {
            "present": True, "id": "sha256:" + "b" * 64,
            "platform": "linux/amd64", "layers_digest": "sha256:" + "c" * 64,
            "repo_digests": ["mysql@sha256:" + "a" * 64]}}}
        mismatches, verified = local_images.compare_images(receipt, observation)
        self.assertEqual(mismatches, [])
        self.assertIn(receipt["services"]["web"]["image"], verified)

    def test_secret_value_in_build_arg_or_label_refuses_without_echo(self):
        secret = "fake-secret-one"
        for build in (
                {"context": ".", "args": {"TOKEN": secret}},
                {"context": ".", "labels": {"unsafe": "prefix-" + secret}}):
            config = {"services": {"web": {"image": "app:test", "build": build}}}
            command = "printf %s " + shlex.quote(json.dumps(config))
            observation = local_images.observe_local_model(
                config_command=command, services=["web"], roots={},
                environment=synthetic_environment({"GITHUB_TOKEN": secret}),
                symbolic_secrets={"GITHUB_TOKEN"})
            self.assertEqual(observation["config_error"], "secret_exposed")
            self.assertNotIn(secret, json.dumps(observation))

    def test_declared_secret_name_cannot_be_a_build_arg(self):
        config = {"services": {"web": {"image": "app:test", "build": {
            "context": ".", "args": {"SENTRY_AUTH_TOKEN": "unused"}}}}}
        observation = local_images.observe_local_model(
            config_command="printf %s " + shlex.quote(json.dumps(config)),
            services=["web"], roots={}, environment=synthetic_environment(),
            symbolic_secrets={"SENTRY_AUTH_TOKEN"})
        self.assertEqual(observation["config_error"], "secret_exposed")

    def test_nonbuilt_unpinned_image_refuses_before_pull_or_archive(self):
        validated = {"project": "app", "environment": "development",
                     "compose": {"service": "web"}}
        observation = {"schema": 1, "engine": "linux/amd64",
                       "config_error": None, "images": {},
                       "remote_receipt": None,
                       "services": {"web": {"present": True,
                           "image": "redis:7", "pull_policy": None,
                           "build": False,
                           "build_digest": local_images.canonical_digest(
                               {"image": "redis:7", "build": None})}}}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(local_images, "_probe_local",
                          return_value=json.dumps(observation)):
            with self.assertRaisesRegex(local_images.LocalImageError,
                                        "local_images_image_unpinned"):
                local_images.build_local_images(
                    validated=validated, commit="a" * 40,
                    source_revision="a" * 40, config_command="unused",
                    build_command=lambda _services: "unused",
                    compose_project="sandbox-host-app-development",
                    source_dir=directory, runtime_dir=directory,
                    output_dir=Path(directory, "output"), platform="linux/amd64",
                    environment=synthetic_environment(), timeout=1)

    def test_receipt_load_is_bounded_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "receipt.json")
            receipt = _receipt()
            path.write_text(json.dumps(receipt))
            path.chmod(0o600)
            self.assertEqual(local_images.load_receipt(path)[0], receipt)
            receipt["project"] = "other"
            path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(local_images.LocalImageError, "receipt digest"):
                local_images.load_receipt(path)

    def test_receipt_target_components_refuse_before_path_use(self):
        for key, value in (
                ("project", "../../victim/deploy-src/app"),
                ("project", "/absolute"), ("environment", "../production"),
                ("environment", "production/child"),
                ("compose_project", "../sandbox-host-app-development"),
                ("compose_project", "sandbox-host-other-development"),
                ("compose_project", "UPPERCASE")):
            with self.subTest(key=key, value=value):
                receipt = _receipt()
                receipt[key] = value
                receipt = local_images.seal_receipt(receipt)
                with self.assertRaises(local_images.LocalImageError):
                    local_images.validate_receipt(receipt)

    def test_docker_client_selection_cannot_change_service_image(self):
        for key in local_images._LOCAL_ENVIRONMENT_KEYS:
            with self.subTest(key=key):
                # A shell-only stand-in for Compose's variable interpolation.
                command = "printf '%s' \"{\\\"services\\\":{\\\"web\\\":{\\\"image\\\":\\\"app:${" + key + ":-default}\\\"}}}\""
                observation = local_images.observe_local_model(
                    config_command=command, services=["web"], roots={},
                    environment=synthetic_environment({key: "desktop-linux"}))
                self.assertEqual(observation["config_error"],
                                 "docker_client_interpolation")
                self.assertTrue(local_images.compare_compose_model(_receipt(), observation))

    def test_docker_client_selection_can_still_select_builder_for_literal_images(self):
        config = {"services": {"web": {"image": "app:test"}}}
        observation = local_images.observe_local_model(
            config_command="printf %s " + shlex.quote(json.dumps(config)),
            services=["web"], roots={},
            environment=synthetic_environment({"DOCKER_CONTEXT": "desktop-linux"}))
        self.assertIsNone(observation["config_error"])
        self.assertEqual(observation["services"]["web"]["image"], "app:test")

    def test_parent_environment_is_not_enumerated(self):
        from unittest.mock import patch
        with patch.object(local_images.os, "environ", {
                "UNDECLARED_SECRET": "must-not-pass", "DOCKER_HOST": "unix:///safe"}):
            result = local_images.local_environment({"EXPLICIT": "yes"})
        self.assertNotIn("UNDECLARED_SECRET", result)
        self.assertEqual(result["DOCKER_HOST"], "unix:///safe")
        self.assertEqual(result["EXPLICIT"], "yes")

    def test_build_local_uses_validated_source_root_and_all_profiles(self):
        commit = "a" * 40
        published = "b" * 40
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory, "checkout")
            checkout.mkdir()
            output = Path(directory, "output")
            captured = {}

            def materialize(_checkout, _commit, destination):
                (destination / "config").mkdir(parents=True)
                (destination / "site").mkdir()

            def validate(selected, environment):
                source = Path(selected).parent / "site"
                return {
                    "project": "app", "environment": environment,
                    "project_root": str(source), "source_root": str(source),
                    "compose": {"build": False, "files": ["compose.yml"],
                                "service": "web", "background_services": [],
                                "init_services": ["migrate"]},
                    "secrets": {"required": {}, "generated": {}},
                }

            def build(**kwargs):
                captured.update(kwargs)
                return {"project": "app", "environment": "development",
                        "commit": commit, "platform": "linux/amd64",
                        "receipt_digest": "sha256:" + "c" * 64,
                        "services": {"web": {}, "migrate": {}},
                        "artifacts": {"image-1": {}}}

            args = SimpleNamespace(project_dir=str(checkout), environment="development",
                                   revision=commit, timeout=60, json=True)
            clean = SimpleNamespace(returncode=0, stdout="")
            with patch.object(hosting_cmd.subprocess, "run", return_value=clean), \
                 patch.object(local_images, "resolve_commit",
                              return_value=(commit, checkout, "config")), \
                 patch.object(local_images, "materialize_checkout",
                              side_effect=materialize), \
                 patch.object(hosting_cmd.hosting, "validate_manifest",
                              side_effect=validate), \
                 patch("sandbox.config.descriptors.primary_config", return_value=None), \
                 patch.object(hosting_cmd.remote, "_source_tree_commit",
                              return_value=(published, checkout)) as source_commit, \
                 patch.object(hosting_cmd, "_secret_status", return_value=({}, [])), \
                 patch.object(hosting_cmd.hosting, "render_env_file", return_value=""), \
                 patch.object(hosting_cmd.hosting, "compose_project_name",
                              return_value="sandbox-host-app-development"), \
                 patch.object(local_images, "default_output_directory",
                              return_value=output), \
                 patch.object(local_images, "build_local_images", side_effect=build), \
                 patch("builtins.print"):
                hosting_cmd._cmd_host_image_build_local(args)

            source = source_commit.call_args.args[0]
            self.assertEqual(source.name, "site")
            self.assertEqual(source_commit.call_args.args[1], source)
            self.assertEqual(captured["source_dir"], str(source))
            self.assertEqual(captured["source_root_relative"], "site")
            self.assertIn("--profile '*' config --format json",
                          captured["config_command"])
            self.assertIn(str(source / "compose.yml"), captured["config_command"])


if __name__ == "__main__":
    unittest.main()

class SharedBundleReceiptTests(unittest.TestCase):
    @staticmethod
    def receipt():
        value = _receipt()
        artifact = value['artifacts']['image-1']
        bundle = {k: artifact.pop(k) for k in ('archive', 'archive_sha256', 'archive_bytes')}
        bundle.update(archive='bundle-1.tar', members=['image-1'])
        return local_images.seal_receipt({**value, 'schema': local_images.RECEIPT_SCHEMA_V2,
                                          'bundles': {'bundle-1': bundle}})

    def test_v2_separates_transport_and_preserves_exact_identity_comparison(self):
        value = self.receipt()
        self.assertEqual(local_images.validate_receipt(value), value)
        self.assertEqual(local_images.accepted_ids(value['artifacts']['image-1']),
                         {'sha256:' + '1' * 64})
        remote = local_images.remote_receipt_document(value, {}, verified_at='synthetic')
        self.assertEqual(remote['schema'], local_images.REMOTE_RECEIPT_SCHEMA_V2)
        self.assertEqual(remote['local_receipt'], value)

    def test_v2_rejects_incomplete_duplicate_extra_or_unbounded_members(self):
        import copy
        for field, bad in (('members', []), ('members', ['image-1', 'image-1']),
                           ('members', ['image-1', 'image-2']), ('archive', '../bundle-1.tar'),
                           ('archive_bytes', True), ('archive_bytes', 2**50),
                           ('extra', 'field')):
            value = copy.deepcopy(self.receipt())
            value['bundles']['bundle-1'][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(local_images.LocalImageError):
                local_images.validate_receipt(local_images.seal_receipt(value))

    def test_v2_rejects_extra_image_refs_and_digest_tampering(self):
        value = self.receipt()
        value['artifacts']['image-1']['refs'].append('unreceipted:tag')
        with self.assertRaises(local_images.LocalImageError):
            local_images.validate_receipt(local_images.seal_receipt(value))
        value = self.receipt()
        value['bundles']['bundle-1']['archive_bytes'] += 1
        with self.assertRaises(local_images.LocalImageError):
            local_images.validate_receipt(value)

    def test_bundle_resolves_configs_and_refuses_extra_tags_platform_and_members(self):
        import hashlib
        import io
        import tarfile
        config = json.dumps({'os':'linux','architecture':'amd64',
                             'rootfs':{'diff_ids':[]}}).encode()
        digest = hashlib.sha256(config).hexdigest()
        image = {'refs':['app:test'], 'config_digest':'sha256:'+digest,
                 'layers_digest':local_images.canonical_digest([]),
                 'manifest_digest':None, 'platform':'linux/amd64'}
        with tempfile.TemporaryDirectory() as directory:
            for change in ('valid', 'extra-tag', 'extra-member', 'wrong-platform', 'missing-config'):
                path = Path(directory, change+'.tar')
                manifest = [{'Config':digest+'.json', 'RepoTags':['app:test'], 'Layers':[]}]
                selected = dict(image)
                if change == 'extra-tag': manifest[0]['RepoTags'].append('extra:test')
                if change == 'wrong-platform': selected['platform'] = 'linux/arm64'
                entries = {'manifest.json':json.dumps(manifest).encode(), digest+'.json':config}
                if change == 'extra-member': entries['unbound.json'] = b'{}'
                if change == 'missing-config': del entries[digest+'.json']
                with tarfile.open(path, 'w') as archive:
                    for name, data in entries.items():
                        member = tarfile.TarInfo(name); member.size = len(data)
                        archive.addfile(member, io.BytesIO(data))
                if change == 'valid':
                    self.assertEqual(local_images.bundle_identities(path, {'image-1':selected})
                                     ['image-1']['config_digest'], 'sha256:'+digest)
                else:
                    with self.subTest(change=change), self.assertRaises(local_images.LocalImageError):
                        local_images.bundle_identities(path, {'image-1':selected})

    def test_v2_rejects_repeated_config_identity_even_with_different_refs(self):
        import copy
        value = self.receipt()
        second = copy.deepcopy(value['artifacts']['image-1'])
        second['refs'] = ['second:test']
        value['artifacts']['image-2'] = second
        value['services']['second'] = {**value['services']['web'], 'role':'background',
                                       'image':'second:test', 'artifact':'image-2'}
        value['bundles']['bundle-1']['members'].append('image-2')
        value['build_config_digest'] = local_images.canonical_digest(
            {k:v['build_digest'] for k,v in value['services'].items()})
        with self.assertRaises(local_images.LocalImageError):
            local_images.validate_receipt(local_images.seal_receipt(value))

    def test_v2_malformed_service_reference_refuses_with_receipt_error(self):
        value = self.receipt()
        value['services']['web']['artifact'] = []
        with self.assertRaises(local_images.LocalImageError) as raised:
            local_images.validate_receipt(local_images.seal_receipt(value))
        self.assertEqual(raised.exception.code, 'local_images_receipt_invalid')

    def test_oversized_local_receipt_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'receipt.json')
            path.write_bytes(b' ' * (local_images.MAX_RECEIPT_BYTES + 1))
            path.chmod(0o600)
            with self.assertRaises(local_images.LocalImageError) as raised:
                local_images.load_receipt(path)
            self.assertEqual(raised.exception.code, 'local_images_receipt_invalid')
