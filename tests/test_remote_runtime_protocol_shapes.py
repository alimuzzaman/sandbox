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
        self.assertEqual(shapes.payload_keys(source), ["a", "b", "c", "d", "e", "{a}"])

    def test_keys_inside_an_embedded_program_count(self):
        """A program sent over SSH as a string is part of the payload contract."""
        before = "PROGRAM = r\'\'\'\nimport json\nprint(json.dumps({'ok': True}))\n\'\'\'\n"
        after = before.replace("{'ok': True}", "{'ok': True, 'extra': 1}")
        self.assertIn("ok", shapes.payload_keys(before))
        diff = shapes.shape_diff({"m.py": shapes.payload_keys(before)},
                                 {"m.py": shapes.payload_keys(after)})
        self.assertEqual(diff["m.py"]["added"], ["extra", "{extra,ok}"])
        prose = 'DOC = """Spec text\nthat is not a program."""\n'
        self.assertEqual(shapes.payload_keys(prose), [])

    def test_moving_an_existing_key_into_another_payload_counts(self):
        before = "a = {'x': 1}\nb = {'y': 2}\n"
        after = "a = {'x': 1}\nb = {'x': 1, 'y': 2}\n"
        diff = shapes.shape_diff({"m.py": shapes.payload_keys(before)},
                                 {"m.py": shapes.payload_keys(after)})
        self.assertEqual(diff, {"m.py": {"added": ["{x,y}"], "removed": ["{y}"]}})

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
