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
        self.assertEqual(sorted(k for k in recorded if not k.startswith("receipt:")),
                         sorted(shapes.SHAPE_SOURCES))
        for path in shapes.SHAPE_SOURCES:
            self.assertTrue((ROOT / path).is_file(), path)

    def test_keys_cover_literals_subscripts_and_lookups(self):
        source = ("p = {'a': 1, **x}\nq = r['b']\ns = r.get('c')\nt = r.pop('d', 0)\n"
                  "u = r.setdefault('e', [])\nv = r[0]\nw = f('ignored')\n")
        self.assertEqual(shapes.payload_keys(source), [":p={a}", ":r[e]=", ":read[b]", ":read[c]", ":read[d]", "a", "b", "c", "d", "e"])

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

    def test_subscript_field_move_requires_protocol_bump(self):
        """Sol R5-3: a subscript store moved into another payload counts."""
        before = ("def f(request, other):\n    request = {'a': 1}\n    other = {'b': 2}\n"
                  "    request['resume_capture'] = True\n")
        after = before.replace("request['resume_capture']", "other['resume_capture']")
        self.assertEqual(self._diff(before, after), {"m.py": {
            "added": ["f:other[resume_capture]="], "removed": ["f:request[resume_capture]="]}})
        moved = before.replace("request['resume_capture'] = True",
                               "request.setdefault('resume_capture', True)")
        self.assertEqual(self._diff(before, moved), {})
        twice = before + "    request['resume_capture'] = False\n"
        self.assertEqual(self._diff(before, twice)["m.py"]["added"],
                         ["f:request[resume_capture]=*2"])
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "shapes.json"
            shapes.write_manifest(manifest, spoken=3, shapes={"m.py": shapes.payload_keys(before)})
            with self.assertRaisesRegex(ValueError, "bump CONTROL_PROTOCOL_SPOKEN"):
                shapes.write_manifest(manifest, spoken=3,
                                      shapes={"m.py": shapes.payload_keys(after)})

    def test_response_read_path_move_requires_protocol_bump(self):
        """Sol R6: a read moved from a nested to a root response path counts."""
        before = ("def publish(evidence):\n    gen = evidence['index']['generation']\n"
                  "    owner = evidence.get('owner')\n    return {'generation': gen}\n")
        nested_to_root = before.replace("evidence['index']['generation']",
                                        "evidence['generation']")
        self.assertEqual(self._diff(before, nested_to_root), {"m.py": {
            "added": ["publish:read[generation]"],
            "removed": ["index", "publish:read[index][generation]"]}})
        get_moved = before.replace("evidence.get('owner')", "evidence['index'].get('owner')")
        self.assertEqual(self._diff(before, get_moved), {"m.py": {
            "added": ["publish:read[index][owner]"], "removed": ["publish:read[owner]"]}})
        renamed = before.replace("evidence", "payload")
        self.assertEqual(self._diff(before, renamed), {})
        repeated = before + "    again = evidence['index']['generation']\n"
        self.assertEqual(self._diff(before, repeated), {})

    def test_sync_generation_read_mutation_is_detected(self):
        """The concrete remote_sync.py mutation from Sol round 6."""
        path = ROOT / "sandbox/transports/remote_sync.py"
        source = path.read_text(encoding="utf-8")
        self.assertIn('evidence["index"]["generation"]', source)
        mutated = source.replace('evidence["index"]["generation"]', 'evidence["generation"]', 1)
        self.assertTrue(shapes.shape_diff({"s.py": shapes.payload_keys(source)},
                                          {"s.py": shapes.payload_keys(mutated)}))

    def test_runtime_receipt_shape_mutation_requires_protocol_bump(self):
        """The installed runtime's response producers are sources too (Sol R7-1)."""
        producer = "sandbox/application/workspace_service.py"
        self.assertIn(producer, shapes.SHAPE_SOURCES)
        source = (ROOT / producer).read_text(encoding="utf-8")
        original = '"index": {"generation": generation, "complete": complete},'
        self.assertIn(original, source)
        mutated = source.replace(
            original, '"index": {"index_generation": generation, "complete": complete},', 1)
        recorded = shapes.read_manifest()["shapes"]
        current = shapes.current_shapes()
        current[producer] = shapes.payload_keys(mutated)
        self.assertIn(producer, shapes.shape_diff(recorded, current))

    def test_runtime_producers_of_sb_responses_are_sources(self):
        for producer in shapes.RUNTIME_PRODUCERS:
            self.assertIn(producer, shapes.SHAPE_SOURCES)
            self.assertTrue((ROOT / producer).is_file(), producer)

    def test_every_remote_entry_point_is_a_runtime_producer(self):
        """Derived, not listed: a new remote module or sb command fails here (Sol R8-1)."""
        import re
        from sandbox.commands.manifest import LEGACY_BRIDGE_COMMANDS
        transports = [path for path in shapes.SHAPE_SOURCES
                      if path not in shapes.RUNTIME_PRODUCERS]
        modules, commands = set(), set()
        for path in transports:
            text = (ROOT / path).read_text(encoding="utf-8")
            modules |= set(re.findall(r'"-m",\s*"(sandbox(?:\.[a-z_]+)+)"', text))
            modules |= set(re.findall(r"-m (sandbox(?:\.[a-z_]+)+)", text))
            commands |= set(re.findall(r'\[\s*"((?:job-[a-z-]+)|workspace)"', text))
        self.assertIn("sandbox.workspaces.checkout", modules)
        self.assertIn("job-status", commands)
        for module in modules:
            self.assertIn(module.replace(".", "/") + ".py", shapes.RUNTIME_PRODUCERS, module)
        for command in commands:
            handler = f"sandbox/commands/{LEGACY_BRIDGE_COMMANDS[command]}.py"
            self.assertIn(handler, shapes.RUNTIME_PRODUCERS, command)

    def _receipt_diff(self):
        from sandbox.remote_runtime import receipts
        recorded = shapes.read_manifest()["shapes"]
        current = {**recorded, **receipts.receipt_shapes()}
        return shapes.shape_diff(recorded, current)

    def test_recorded_receipts_match_the_runtime_producers(self):
        self.assertEqual(self._receipt_diff(), {})

    def test_job_snapshot_receipt_mutation_requires_protocol_bump(self):
        """A registry-side key rename changes the job-status receipt (Sol R8-1)."""
        from unittest.mock import patch
        from sandbox.jobs.registry import JobRepository
        original = JobRepository.snapshot

        def renamed(self, job_id):
            value = original(self, job_id)
            value["heart_beat"] = value.pop("heartbeat")
            return value

        with patch.object(JobRepository, "snapshot", renamed):
            self.assertIn("receipt:job_snapshot", self._receipt_diff())

    def test_snapshot_receipts_cover_populated_child_records(self):
        """Every SQL-derived child record is sampled populated (Sol R9-1)."""
        recorded = shapes.read_manifest()["shapes"]
        snapshot = set(recorded["receipt:job_snapshot"])
        for path in ("process.supervisor_pid", "heartbeat.supervisor_at",
                     "heartbeat.health_evidence", "output[].bytes_stored",
                     "metrics.samples", "artifacts[].size_bytes",
                     "artifacts[].stored_relative_path",
                     "compatibility_differences[].severity"):
            self.assertIn(path, snapshot)
        self.assertIn("artifacts[].size_bytes", recorded["receipt:job_artifacts"])

    def test_artifact_metadata_sql_alias_requires_protocol_bump(self):
        """``size_bytes AS byte_count`` in the producer's projection is caught (Sol R9-1)."""
        from unittest.mock import patch
        from sandbox.jobs.registry import JobRepository
        original = JobRepository.snapshot

        def aliased(self, job_id):
            value = original(self, job_id)
            for row in value["artifacts"]:
                row["byte_count"] = row.pop("size_bytes")
            return value

        with patch.object(JobRepository, "snapshot", aliased):
            diff = self._receipt_diff()
        self.assertIn("receipt:job_snapshot", diff)
        self.assertIn("receipt:job_artifacts", diff)

    def test_materialization_receipt_mutation_requires_protocol_bump(self):
        """Renaming the refusal's ``errno`` breaks the ownership repair (Sol R8-1)."""
        from unittest.mock import patch
        from sandbox.workspaces import checkout
        original = checkout._failure_detail

        def renamed(*args, **kwargs):
            detail = original(*args, **kwargs)
            detail["error_number"] = detail.pop("errno")
            return detail

        with patch.object(checkout, "_failure_detail", renamed):
            self.assertIn("receipt:materialization_refusal", self._receipt_diff())

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
