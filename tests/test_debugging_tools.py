import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlsplit


class TestDebuggingTools(unittest.TestCase):
    def test_capture_url_keeps_path_query_and_adds_correlation_id(self):
        from sandbox.commands.debug import _qm_capture_url

        result = _qm_capture_url("https://example.test/wp-json/demo?keep=value#ignored", "a" * 32)
        parsed = urlsplit(result)
        self.assertEqual(parsed.path, "/wp-json/demo")
        self.assertEqual(parse_qs(parsed.query), {
            "keep": ["value"], "sandbox_qm_capture": ["a" * 32],
        })
        self.assertFalse(parsed.fragment)

    def test_record_lookup_never_returns_a_stale_last_line(self):
        from sandbox.commands.debug import _qm_record_for_capture

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qm.jsonl"
            path.write_text(json.dumps({"capture_id": "old", "data": {}}) + "\n")
            start = path.stat().st_size
            with path.open("a") as output:
                output.write(json.dumps({"capture_id": "other", "data": {}}) + "\n")
                output.write(json.dumps({"capture_id": "wanted", "data": {"timing": []}}) + "\n")

            self.assertEqual(
                _qm_record_for_capture(path, start, "wanted"),
                {"capture_id": "wanted", "data": {"timing": []}},
            )
            self.assertIsNone(_qm_record_for_capture(path, start, "old"))

    def test_collector_filter_omits_hooks_by_default_and_honors_request(self):
        from sandbox.commands.debug import _qm_filter_collectors

        payload = {"data": {"hooks": [1], "timing": [2], "php_errors": [3]}}
        self.assertEqual(
            _qm_filter_collectors(payload, None)["data"],
            {"timing": [2], "php_errors": [3]},
        )
        self.assertEqual(
            _qm_filter_collectors(payload, "timing, missing")["data"],
            {"timing": [2]},
        )

    def test_qm_command_uses_the_fresh_tagged_record(self):
        from sandbox.commands import debug

        with tempfile.TemporaryDirectory() as directory:
            content = Path(directory) / "wp-content"
            content.mkdir()
            log = content / "qm.jsonl"
            log.write_text(json.dumps({"capture_id": "stale", "data": {"timing": [0]}}) + "\n")

            def write_capture(_instance, path):
                capture_id = parse_qs(urlsplit(path).query)["sandbox_qm_capture"][0]
                with log.open("a") as output:
                    output.write(json.dumps({
                        "capture_id": capture_id,
                        "data": {"timing": [1], "hooks": [2]},
                    }) + "\n")

            args = SimpleNamespace(resolved_instance="demo", url="/", clear=False,
                                   collectors="timing")
            output = io.StringIO()
            result = SimpleNamespace(returncode=0, stdout="")
            with mock.patch.object(debug, "wp_dir", return_value=Path(directory)), \
                 mock.patch.object(debug, "wpcli", return_value=result), \
                 mock.patch.object(debug, "_is_herd_instance", return_value=False), \
                 mock.patch.object(debug, "_qm_fetch_docker", side_effect=write_capture), \
                 mock.patch("sys.stdout", output):
                debug.cmd_qm({}, args)

            self.assertEqual(json.loads(output.getvalue()), {
                "capture_id": mock.ANY,
                "data": {"timing": [1]},
            })

    def test_first_qm_capture_activates_installed_inactive_plugin_before_fetch(self):
        from sandbox.commands import debug

        with tempfile.TemporaryDirectory() as directory:
            content = Path(directory) / "wp-content"
            content.mkdir()
            log = content / "qm.jsonl"
            events = []

            def wpcli(args, **_kwargs):
                events.append(args)
                if args[:3] == ["plugin", "is-installed", "query-monitor"]:
                    return SimpleNamespace(returncode=0, stdout="")
                if args[:3] == ["plugin", "is-active", "query-monitor"]:
                    return SimpleNamespace(returncode=1, stdout="")
                if args[:3] == ["plugin", "activate", "query-monitor"]:
                    return SimpleNamespace(returncode=0, stdout="")
                self.fail(f"unexpected wp-cli call: {args}")

            def write_capture(_instance, path):
                events.append("anonymous-fetch")
                capture_id = parse_qs(urlsplit(path).query)["sandbox_qm_capture"][0]
                log.write_text(json.dumps({
                    "capture_id": capture_id,
                    "data": {"timing": [1]},
                }) + "\n")

            args = SimpleNamespace(resolved_instance="demo", url="/", clear=False,
                                   collectors="timing")
            with mock.patch.object(debug, "wp_dir", return_value=Path(directory)), \
                 mock.patch.object(debug, "wpcli", side_effect=wpcli), \
                 mock.patch.object(debug, "_is_herd_instance", return_value=False), \
                 mock.patch.object(debug, "_qm_fetch_docker", side_effect=write_capture), \
                 mock.patch("sys.stdout", io.StringIO()):
                debug.cmd_qm({}, args)

            self.assertEqual(events, [
                ["plugin", "is-installed", "query-monitor"],
                ["plugin", "is-active", "query-monitor"],
                ["plugin", "activate", "query-monitor"],
                "anonymous-fetch",
            ])
            self.assertFalse(any(
                isinstance(event, list) and event[:2] == ["plugin", "install"]
                for event in events
            ))

    def test_herd_xdebug_reports_actual_host_extension_state(self):
        from sandbox.commands import debug

        output = io.StringIO()
        args = SimpleNamespace(resolved_instance="herd-demo", state="status")
        with mock.patch.object(debug, "_is_herd_instance", return_value=True), \
             mock.patch.object(debug, "wpcli", return_value=SimpleNamespace(returncode=0, stdout="on\n")), \
             mock.patch("sys.stdout", output):
            debug.cmd_xdebug({}, args)

        self.assertTrue(output.getvalue().startswith("on\n"))
        self.assertIn("Per-instance toggling is unsupported", output.getvalue())


if __name__ == "__main__":
    unittest.main()
