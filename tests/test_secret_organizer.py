from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sandbox.secrets.audit import SecretAudit
from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.organizer import classify, organize
from sandbox.secrets.parser import SecretParseError, parse_document
from sandbox.secrets.service import SecretService
from sandbox.secrets.sources import SourceRegistry


FIXTURE = (
    "export CLOUDFLARE_API_TOKEN='cf-token'\n"
    "export TEMPLATELY_API_KEY_DEV='dev-key'\n"
    "# keeps its own note\n"
    "export WP_ORG_SVN_PASS='svn-pass'\n"
    "export TEMPLATELY_API_KEY='prod-key'\n"
    "export SOME_UNKNOWN_THING='unknown'\n"
)


def _organize(text: str):
    return organize(parse_document(text.encode("utf-8")))


class TestOrganizer(unittest.TestCase):
    def test_groups_keys_by_owner_and_keeps_unknown_keys(self):
        report = _organize(FIXTURE)
        titles = [title for title, _ in report.groups]
        self.assertEqual(titles, [
            "Templately API keys", "Cloudflare and tunnels",
            "Code and package publishing", "Ungrouped",
        ])
        self.assertEqual(dict(report.groups)["Ungrouped"], ["SOME_UNKNOWN_THING"])
        self.assertEqual(report.count, 5)
        self.assertTrue(report.changed)

    def test_every_assignment_line_survives_verbatim(self):
        report = _organize(FIXTURE)
        rendered = report.content.decode("utf-8")
        for line in FIXTURE.splitlines():
            if line.startswith("export "):
                self.assertIn(line, rendered)

    def test_own_comment_travels_with_its_key(self):
        rendered = _organize(FIXTURE).content.decode("utf-8").splitlines()
        note = rendered.index("# keeps its own note")
        self.assertEqual(rendered[note + 1], "export WP_ORG_SVN_PASS='svn-pass'")

    def test_rerun_is_stable_and_does_not_duplicate_banners(self):
        once = _organize(FIXTURE).content.decode("utf-8")
        twice = _organize(once)
        self.assertFalse(twice.changed)
        self.assertEqual(twice.content.decode("utf-8"), once)

    def test_mixed_newlines_refuse_rather_than_normalize(self):
        with self.assertRaises(SecretParseError):
            _organize("A_TOKEN='one'\r\nB_TOKEN='two'\n")

    def test_report_never_contains_a_value(self):
        report = _organize(FIXTURE)
        for _, keys in report.groups:
            for key in keys:
                self.assertNotIn("key", key.lower().replace("_key", ""))
        self.assertNotIn("prod-key", repr(report.groups))

    def test_exact_key_wins_over_prefix(self):
        self.assertEqual(classify("BASIC_AUTH_PASSWORD").identifier, "personal-sites")
        self.assertEqual(classify("LENZORA_PRODUCTION_NEXTAUTH_SECRET").identifier,
                         "lenzora-production")
        self.assertIsNone(classify("SOME_UNKNOWN_THING"))


class TestOrganizeService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.personal = self.root / "personal"
        self.personal.write_text(FIXTURE)
        self.personal.chmod(0o600)
        self.json_source = self.root / "creds.json"
        self.json_source.write_text('{"token": "value"}')
        self.json_source.chmod(0o600)
        self.registry = SourceRegistry(
            self.root,
            {"creds": {"path": "creds.json", "format": "json"}},
            personal_path=self.personal,
        )
        self.service = SecretService(
            self.registry, SecretAudit(self.root / "audit.log"),
            revision_key_path=self.root / "revision.key",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_report_only_by_default(self):
        result = self.service.organize("personal")
        self.assertTrue(result["changed"])
        self.assertFalse(result["applied"])
        self.assertEqual(self.personal.read_text(), FIXTURE)

    def test_apply_rewrites_and_preserves_permissions(self):
        result = self.service.organize("personal", apply=True)
        self.assertTrue(result["applied"])
        content = self.personal.read_text()
        self.assertIn("# Cloudflare and tunnels", content)
        self.assertIn("export TEMPLATELY_API_KEY='prod-key'", content)
        self.assertEqual(self.personal.stat().st_mode & 0o777, 0o600)
        again = self.service.organize("personal", apply=True)
        self.assertFalse(again["changed"])

    def test_stale_revision_refuses_to_write(self):
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.organize("personal", apply=True, expected_revision="r1_stale")
        self.assertEqual(caught.exception.code, "revision_conflict")
        self.assertEqual(self.personal.read_text(), FIXTURE)

    def test_non_dotenv_source_is_refused(self):
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.organize("creds")
        self.assertEqual(caught.exception.code, "organize_unsupported")

    def test_mcp_surface_is_denied(self):
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.organize("personal", surface="mcp")
        self.assertEqual(caught.exception.code, "organize_denied")

    def test_targeted_set_preserves_group_structure(self):
        from sandbox.core import _secrets

        self.service.organize("personal", apply=True)
        _secrets.write_secret("CLOUDFLARE_ACCOUNT_ID", "account-id", self.personal)
        _secrets.write_secret("TEMPLATELY_API_KEY", "rotated", self.personal)
        content = self.personal.read_text()
        self.assertIn("# Cloudflare and tunnels", content)
        self.assertIn("export TEMPLATELY_API_KEY=rotated", content)
        self.assertEqual(_secrets.read_secret_file(self.personal)["CLOUDFLARE_ACCOUNT_ID"],
                         "account-id")


if __name__ == "__main__":
    unittest.main()
