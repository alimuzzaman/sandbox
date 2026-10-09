"""Payload shape changes require a control-protocol bump (spec 061 FR-006)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.remote_runtime import shapes  # noqa: E402
from sandbox.remote_runtime.protocol import CONTROL_PROTOCOL_SPOKEN  # noqa: E402


class ShapeGuardTests(unittest.TestCase):
    def test_recorded_shapes_match_the_spoken_protocol(self):
        recorded = shapes.read_manifest()
        diff = shapes.shape_diff(recorded["shapes"], shapes.current_shapes())
        self.assertEqual(
            diff, {},
            "control payload keys changed without a protocol bump; bump "
            "CONTROL_PROTOCOL_SPOKEN in sandbox/remote_runtime/protocol.py, then run "
            "`python -m sandbox.remote_runtime.shapes --write`. Changed keys: "
            + json.dumps(diff))
        self.assertEqual(
            recorded["spoken"], CONTROL_PROTOCOL_SPOKEN,
            "protocol bumped without recording shapes; run "
            "`python -m sandbox.remote_runtime.shapes --write`")
        self.assertEqual(recorded.get("format"), shapes.FORMAT,
                         "fingerprint format changed; run "
                         "`python -m sandbox.remote_runtime.shapes --write`")

    def test_every_shape_source_exists_and_is_recorded(self):
        recorded = shapes.read_manifest()["shapes"]
        self.assertEqual(sorted(recorded), sorted(shapes.SHAPE_SOURCES))
        for path in shapes.SHAPE_SOURCES:
            self.assertTrue((ROOT / path).is_file(), path)

    def test_keys_cover_literals_subscripts_and_lookups(self):
        source = ("p = {'a': 1, **x}\nq = r['b']\ns = r.get('c')\nt = r.pop('d', 0)\n"
                  "u = r.setdefault('e', [])\nv = r[0]\nw = f('ignored')\n")
        self.assertEqual(shapes.payload_keys(source), [":p={a}", "a", "b", "c", "d", "e"])

    def test_keys_inside_an_embedded_program_count(self):
        """A program sent over SSH as a string is part of the payload contract."""
        before = "PROGRAM = r\'\'\'\nimport json\nprint(json.dumps({'ok': True}))\n\'\'\'\n"
        after = before.replace("{'ok': True}", "{'ok': True, 'extra': 1}")
        self.assertIn("ok", shapes.payload_keys(before))
        diff = shapes.shape_diff({"m.py": shapes.payload_keys(before)},
                                 {"m.py": shapes.payload_keys(after)})
        self.assertEqual(diff["m.py"]["added"], ["<:PROGRAM>:json.dumps(0)={extra,ok}", "extra"])
        prose = 'DOC = """Spec text\nthat is not a program."""\n'
        self.assertEqual(shapes.payload_keys(prose), [])

    def _diff(self, before, after):
        return shapes.shape_diff({"m.py": shapes.payload_keys(before)},
                                 {"m.py": shapes.payload_keys(after)})

    def test_moving_an_existing_key_into_another_payload_counts(self):
        diff = self._diff("a = {'x': 1}\nb = {'y': 2}\n", "a = {'x': 1}\nb = {'x': 1, 'y': 2}\n")
        self.assertEqual(diff, {"m.py": {"added": [":b={x,y}"], "removed": [":b={y}"]}})

    def test_exchanging_keys_between_payloads_counts(self):
        before = "def send():\n    a = {'x': 1}\n    b = {'y': 2}\n"
        after = "def send():\n    a = {'y': 1}\n    b = {'x': 2}\n"
        self.assertEqual(self._diff(before, after), {"m.py": {
            "added": ["send:a={y}", "send:b={x}"], "removed": ["send:a={x}", "send:b={y}"]}})

    def test_removing_a_field_from_one_of_several_identical_payloads_counts(self):
        before = "rows = [{'a': 1, 'b': 2}, {'a': 3, 'b': 4}]\n"
        after = "rows = [{'a': 1, 'b': 2}, {'a': 3}]\n"
        self.assertEqual(self._diff(before, after), {"m.py": {
            "added": [":rows={a,b}", ":rows={a}"], "removed": [":rows={a,b}*2"]}})

    def test_single_line_embedded_programs_count(self):
        before = "P = \"import json; print(json.dumps({'k': 1}))\"\n"
        after = before.replace("{'k': 1}", "{'k': 1, 'z': 2}")
        self.assertEqual(self._diff(before, after)["m.py"]["added"], ["<:P>:json.dumps(0)={k,z}", "z"])
        self.assertEqual(shapes.payload_keys("S = 'ok'\nT = 'set -eu; echo hi'\n"), [])

    def test_keyword_return_and_nested_bindings_are_named(self):
        source = ("def f(jobs):\n    g(body={'a': 1})\n    return {'b': {'c': 1}}\n"
                  "def h(jobs):\n    jobs.append({'s': {'d': 1}})\n")
        self.assertEqual(shapes.payload_keys(source),
                         ["a", "b", "c", "d", "f:g(body=)={a}", "f:return={b}", "f:return[b]={c}",
                          "h:jobs.append(0)={s}", "h:jobs.append(0)[s]={d}", "s"])

    def test_nested_payload_field_exchange_counts(self):
        """Sol R3-1: a request and a response both carry a nested ``source``."""
        before = ("def submit(jobs):\n"
                  "    jobs.append({'source': {'identity': 1, 'commit': 2}})\n"
                  "    return {'source': {'identity': 1, 'commit': 2, 'dirty': 3}}\n")
        after = ("def submit(jobs):\n"
                 "    jobs.append({'source': {'identity': 1, 'commit': 2, 'dirty': 3}})\n"
                 "    return {'source': {'identity': 1, 'commit': 2}}\n")
        self.assertEqual(self._diff(before, after), {"m.py": {
            "added": ["submit:jobs.append(0)[source]={commit,dirty,identity}",
                      "submit:return[source]={commit,identity}"],
            "removed": ["submit:jobs.append(0)[source]={commit,identity}",
                        "submit:return[source]={commit,dirty,identity}"]}})

    def test_separate_calls_with_the_same_keyword_are_distinct_sites(self):
        before = "def f():\n    g(body={'a': 1, 'b': 2})\n    g(body={'a': 1})\n"
        after = "def f():\n    g(body={'a': 1})\n    g(body={'a': 1, 'b': 2})\n"
        self.assertEqual(self._diff(before, after), {"m.py": {
            "added": ["f:g(body=)#2={a,b}", "f:g(body=)={a}"],
            "removed": ["f:g(body=)#2={a}", "f:g(body=)={a,b}"]}})

    def test_separate_return_branches_are_distinct_sites(self):
        before = "def f(c):\n    if c:\n        return {'a': 1, 'b': 2}\n    return {'a': 1}\n"
        after = "def f(c):\n    if c:\n        return {'a': 1}\n    return {'a': 1, 'b': 2}\n"
        self.assertEqual(self._diff(before, after), {"m.py": {
            "added": ["f:return#2={a,b}", "f:return={a}"],
            "removed": ["f:return#2={a}", "f:return={a,b}"]}})

    def test_writer_refuses_changed_keys_under_the_same_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "shapes.json"
            shapes.write_manifest(manifest, spoken=3, shapes={"m.py": ["a"]})
            shapes.write_manifest(manifest, spoken=3, shapes={"m.py": ["a"]})
            with self.assertRaisesRegex(ValueError, "bump CONTROL_PROTOCOL_SPOKEN"):
                shapes.write_manifest(manifest, spoken=3, shapes={"m.py": ["a", "b"]})
            with self.assertRaisesRegex(ValueError, "older"):
                shapes.write_manifest(manifest, spoken=2, shapes={"m.py": ["a"]})
            shapes.write_manifest(manifest, spoken=4, shapes={"m.py": ["a", "b"]})
            self.assertEqual(shapes.read_manifest(manifest),
                             {"format": shapes.FORMAT, "spoken": 4,
                              "shapes": {"m.py": ["a", "b"]}})

    def test_writer_allows_a_fingerprint_format_change_under_the_same_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "shapes.json"
            manifest.write_text(json.dumps({"spoken": 3, "shapes": {"m.py": ["a"]}}))
            shapes.write_manifest(manifest, spoken=3, shapes={"m.py": ["a", "{a}"]})
            with self.assertRaisesRegex(ValueError, "bump CONTROL_PROTOCOL_SPOKEN"):
                shapes.write_manifest(manifest, spoken=3, shapes={"m.py": ["a", "b"]})

    def test_diff_reports_added_and_removed_keys(self):
        self.assertEqual(shapes.shape_diff({"m.py": ["a", "b"]}, {"m.py": ["b", "c"]}),
                         {"m.py": {"added": ["c"], "removed": ["a"]}})


if __name__ == "__main__":
    unittest.main()
