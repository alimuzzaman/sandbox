"""Pure declarations only: no authenticated binding or operational replay proof."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import unittest

from sandbox.hosting.credential_selection import (
    CredentialMapping, DeclarationError, PersonalLocalSelection, ServerSelection,
    StaticValidationResult, decode_mappings, decode_selection, parse_declarations,
)


def server_fields():
    return dict(credential_scope="server", remote="fixture-server",
                credential_project="fixture-project", credential_source="fixture-source",
                credential_source_revision="source-r1", credential_profile="fixture-profile",
                credential_profile_revision="profile-r1")


def declarations():
    return {"environments": {"fixture": {"compose": {"service": "web"}, "secrets": {
        "required": {"TOKEN": "PROVIDER_TOKEN"},
        "generated": {"PASSWORD": "GENERATED_PASSWORD"}}}}}


class CredentialSelectionTests(unittest.TestCase):
    def test_personal_default_explicit_and_existing_remote(self):
        for raw in ({}, {"credential_scope": "personal-local"}, {"remote": "fixture-server"},
                    {"credential_scope": "personal-local", "remote": "fixture-server"}):
            self.assertEqual(decode_selection(raw), PersonalLocalSelection())

    def test_server_requires_every_explicit_reference(self):
        original = server_fields()
        self.assertIsInstance(decode_selection(original), ServerSelection)
        for key in original:
            raw = original.copy()
            del raw[key]
            with self.subTest(missing=key), self.assertRaises(DeclarationError):
                decode_selection(raw)
        for scope in (None, "personal-local", "invented"):
            with self.subTest(scope=scope), self.assertRaises(DeclarationError):
                decode_selection({**original, "credential_scope": scope})

    def test_closed_bounded_identifiers(self):
        for key in server_fields():
            if key == "credential_scope":
                continue
            for bad in (None, "", "x" * 129, "../sentinel", "x\n", "é", 1, True, [], {}):
                with self.subTest(key=key, bad=bad), self.assertRaises(DeclarationError):
                    decode_selection({**server_fields(), key: bad})
            self.assertIsInstance(decode_selection({**server_fields(), key: "x" * 128}), ServerSelection)
        for extra in ("actor", "grant", "owner_context", "admission", "capability"):
            with self.subTest(extra=extra), self.assertRaises(DeclarationError):
                decode_selection({**server_fields(), extra: "SYNTHETIC_SENTINEL"})
        for raw in (None, [], "server"):
            with self.assertRaises(DeclarationError):
                decode_selection(raw)

    def test_mapping_preserves_kind_source_destination_and_consumer(self):
        rows = parse_declarations(declarations(), None)
        self.assertEqual(rows, (CredentialMapping("generated", "GENERATED_PASSWORD", "PASSWORD", "web"),
                                CredentialMapping("provider", "PROVIDER_TOKEN", "TOKEN", "web")))
        self.assertEqual(decode_mappings([vars(row) for row in reversed(rows)]), rows)
        result = StaticValidationResult(decode_selection(server_fields()), rows)
        self.assertEqual((result.declarations, result.availability, result.execution),
                         ("valid", "unverified", "disabled"))
        with self.assertRaises(FrozenInstanceError):
            rows[0].consumer = "other"
        with self.assertRaises(FrozenInstanceError):
            result.selection.remote = "other"

    def test_each_selector_and_mapping_change_is_distinct(self):
        selected = decode_selection(server_fields())
        for field in ("remote", "project", "source", "source_revision", "profile", "profile_revision"):
            self.assertNotEqual(selected, replace(selected, **{field: "changed"}))
        mapping = CredentialMapping("provider", "TOKEN_A", "TOKEN", "web")
        for field, value in (("kind", "generated"), ("source_key", "TOKEN_B"),
                             ("destination", "OTHER_TOKEN"), ("consumer", "worker")):
            changed = replace(mapping, **{field: value})
            self.assertNotEqual(mapping, changed)
            if field != "destination":
                self.assertEqual(mapping.destination, changed.destination)

    def test_mapping_bounds_closed_rows_and_ambiguous_destinations(self):
        row = dict(kind="provider", source_key="SRC", destination="DEST", consumer="web")
        bad_rows = [None, {}, [None], [{**row, "grant": "fake"}], [row, row],
                    [row, {**row, "kind": "generated"}], [row] * 257]
        for field, values in (("kind", ("inline", None)), ("source_key", ("", "a-b", "x" * 129)),
                              ("destination", ("1BAD", "x\n")), ("consumer", ("", "../web"))):
            bad_rows.extend([{**row, field: value}] for value in values)
        for rows in bad_rows:
            with self.subTest(rows=rows), self.assertRaises(DeclarationError):
                decode_mappings(rows)
        self.assertEqual(len(decode_mappings([{**row, "destination": f"KEY_{n}"} for n in range(256)])), 256)

    def test_inline_overlap_missing_environment_and_fake_authority_refuse(self):
        cases = []
        for values in ({}, {"TOKEN": "SYNTHETIC_SENTINEL"}):
            raw = declarations()
            raw["environments"]["fixture"]["secrets"]["values"] = values
            cases.append(raw)
        raw = declarations()
        raw["environments"]["fixture"]["secrets"]["generated"]["TOKEN"] = "OTHER"
        cases.append(raw)
        for key in ("actor", "grant", "owner_context", "owner-context", "admission", "capability"):
            for top in (True, False):
                raw = declarations()
                (raw if top else raw["environments"]["fixture"])[key] = "SYNTHETIC_SENTINEL"
                cases.append(raw)
        cases.extend([{}, {"environments": {}}, {"environments": {"fixture": {}}}])
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(DeclarationError) as caught:
                parse_declarations(raw, None)
            self.assertEqual(str(caught.exception), "Invalid server credential selection or nonsecret declaration.")
        with self.assertRaises(DeclarationError):
            parse_declarations(declarations(), "absent")

    def test_concurrent_repeated_decode_has_no_shared_mutable_results(self):
        def decode(index):
            raw = declarations()
            raw["environments"]["fixture"]["compose"]["service"] = f"web-{index}"
            selection = decode_selection({**server_fields(), "credential_source_revision": f"rev-{index}"})
            rows = parse_declarations(raw, "fixture")
            snapshot = deepcopy(raw)
            raw["environments"].clear()
            self.assertEqual(rows, parse_declarations(snapshot, "fixture"))
            return selection, rows
        with ThreadPoolExecutor(max_workers=4) as pool:
            first = list(pool.map(decode, range(24)))
            second = list(pool.map(decode, range(24)))
        self.assertEqual(first, second)
        self.assertEqual(len({selection for selection, _ in first}), 24)
        self.assertEqual(len({rows for _, rows in first}), 24)
