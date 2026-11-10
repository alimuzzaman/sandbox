"""CLI hook for controller auto-update (spec 072, contracts/cli-behavior.md).

The engine and ``execve`` are injected, so these tests never touch git.
"""
import argparse
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.controller_update import hook  # noqa: E402
from sandbox.controller_update.models import (  # noqa: E402
    AlreadyMoved, NoAction, RolledBack, Skip, SkipReason, Stranded, Updated,
)

OLD = "1" * 40
NEW = "2" * 40
ENTRY = Path("/opt/example/sandbox/sb")
PYTHON = "/opt/example/sandbox/.cli-venv/bin/python"


def ns(cmd, **fields):
    return argparse.Namespace(cmd=cmd, **fields)


class Recorder:
    def __init__(self, decision=NoAction(SkipReason.NO_HINT)):
        self.decision = decision
        self.calls = []
        self.enabled_answers = []
        self.execs = []

    def engine(self, root, *, is_enabled):
        self.calls.append(root)
        if self.decision is None:
            raise RuntimeError("boom")
        self.enabled_answers.append(is_enabled())
        return self.decision

    def execve(self, path, argv, env):
        self.execs.append((path, argv, env))


class HookCase(unittest.TestCase):
    def call(self, args, *, decision=NoAction(SkipReason.NO_HINT), environ=None,
             argv=None, config=lambda: True, main_file=str(ENTRY)):
        recorder = Recorder(decision)
        stderr, stdout = io.StringIO(), io.StringIO()
        argv = argv if argv is not None else [str(ENTRY), "remote", "status", "demo"]
        hook.maybe_auto_update(
            args, argv, environ={} if environ is None else environ,
            root=ENTRY.parent, entry=ENTRY, execve=recorder.execve, python=PYTHON,
            engine_run=recorder.engine, config_enabled=config,
            stderr=stderr, stdout=stdout, main_file=main_file,
        )
        return recorder, stderr.getvalue(), stdout.getvalue()


class ClassificationTests(unittest.TestCase):
    def test_remote_capable_commands(self):
        capable = [
            ns("remote", action="list"), ns("deploy", remote="demo"), ns("host", remote=None),
            ns("preview"), ns("hermes", remote="demo"), ns("wp", remote="demo", local=False),
            ns("test", local=False), ns("e2e", local=False), ns("ci", local=False),
            ns("job", local=False), ns("jobs"), ns("ensure", remote="demo"),
        ]
        for args in capable:
            self.assertTrue(hook.is_remote_capable(args), args)

    def test_local_only_commands(self):
        local = [
            ns("wp", remote=None, local=False), ns("test", local=True), ns("e2e", local=True),
            ns("setup"), ns("snapshot"), ns("zip"), ns(None), ns("ensure", remote=""),
            ns("ci", local=True),
        ]
        for args in local:
            self.assertFalse(hook.is_remote_capable(args), args)

    def test_env_policy_values(self):
        for value in ("0", "false", "No", "OFF", " off "):
            self.assertIs(hook.env_policy({"SANDBOX_AUTO_UPDATE": value}), False, value)
        for value in ("1", "true", "YES", "on"):
            self.assertIs(hook.env_policy({"SANDBOX_AUTO_UPDATE": value}), True, value)
        for env in ({}, {"SANDBOX_AUTO_UPDATE": ""}, {"SANDBOX_AUTO_UPDATE": "maybe"}):
            self.assertIsNone(hook.env_policy(env), env)


class GatingTests(HookCase):
    """US5 and US6: opt-outs, machine output, job children, the re-exec guard."""

    def assert_silent_no_engine(self, recorder, err, out):
        self.assertEqual(recorder.calls, [])
        self.assertEqual(recorder.execs, [])
        self.assertEqual(err, "")
        self.assertEqual(out, "")

    def test_json_runs_never_reach_the_engine(self):
        updated = Updated(OLD, NEW, "origin/latest")
        self.assert_silent_no_engine(*self.call(ns("remote", json=True), decision=updated))

    def test_format_json_runs_never_reach_the_engine(self):
        for fmt in ("json", "jsonl"):
            self.assert_silent_no_engine(*self.call(
                ns("remote", format=fmt), decision=Updated(OLD, NEW, "origin/latest")))
        self.assertFalse(hook.is_machine_output(ns("remote", format="text")))

    def test_every_machine_output_option_is_covered(self):
        """A new output-format option on any parser must extend is_machine_output."""
        import re
        sources = [ROOT / "sandbox" / "cli.py",
                   *sorted((ROOT / "sandbox" / "commands").glob("*.py"))]
        option = re.compile(r'add_argument\(\s*"(--[a-z][a-z-]*)"(.*?)\)\s*$', re.S | re.M)
        output_flag = re.compile(r"--(?:json|jsonl|ndjson|porcelain|machine)(?:-output)?$")
        format_choice = re.compile(r'choices=[^\n]*["\'](?:json|jsonl|ndjson)["\']')
        found = set()
        for path in sources:
            for name, rest in option.findall(path.read_text(encoding="utf-8")):
                # Output switches only; "--spec-json FILE" style options take input.
                if output_flag.match(name) and "store_true" in rest:
                    found.add(name)
                elif format_choice.search(rest):
                    found.add(name)
        self.assertIn("--json", found)
        self.assertLessEqual(found, {"--json", "--format"}, sorted(found))

    def test_durable_job_child_never_reaches_the_engine(self):
        self.assert_silent_no_engine(*self.call(
            ns("remote"), decision=Updated(OLD, NEW, "origin/latest"),
            environ={"SANDBOX_DURABLE_JOB_ID": "job-1"}))

    def test_reexec_guard_never_reaches_the_engine(self):
        self.assert_silent_no_engine(*self.call(
            ns("remote"), decision=Updated(OLD, NEW, "origin/latest"),
            environ={"SANDBOX_AUTO_UPDATE_REEXEC": "1"}))

    def test_env_opt_out_never_reaches_the_engine(self):
        for value in ("0", "off", "false", "no"):
            self.assert_silent_no_engine(*self.call(
                ns("remote"), decision=Updated(OLD, NEW, "origin/latest"),
                environ={"SANDBOX_AUTO_UPDATE": value}))

    def test_not_the_sb_entry_never_reaches_the_engine(self):
        self.assert_silent_no_engine(*self.call(
            ns("remote"), decision=Updated(OLD, NEW, "origin/latest"),
            argv=["/usr/bin/python3", "-m", "unittest"]))
        self.assert_silent_no_engine(*self.call(
            ns("remote"), decision=Updated(OLD, NEW, "origin/latest"),
            main_file="/usr/lib/python3/unittest/__main__.py"))

    def test_local_command_never_reaches_the_engine(self):
        self.assert_silent_no_engine(*self.call(
            ns("snapshot"), decision=Updated(OLD, NEW, "origin/latest")))

    def test_symlinked_entry_counts_as_the_sb_entry(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "checkout" / "sb"
            real.parent.mkdir()
            real.write_text("#!/bin/sh\n", encoding="utf-8")
            link = Path(tmp) / "bin-sb"
            os.symlink(real, link)
            self.assertTrue(hook.is_sb_entry([str(link), "remote"], real, str(link)))
            self.assertFalse(hook.is_sb_entry(["/usr/bin/python3"], real, str(real)))
            self.assertFalse(hook.is_sb_entry([], real, str(real)))
            # An sb-shaped argv inside another program (a test runner) is not the CLI.
            self.assertFalse(hook.is_sb_entry([str(real), "remote"], real, "/usr/lib/unittest.py"))
            self.assertFalse(hook.is_sb_entry([str(real), "remote"], real))

    def test_config_is_consulted_only_when_env_is_unset(self):
        recorder, _, _ = self.call(ns("remote"), config=lambda: False)
        self.assertEqual(recorder.enabled_answers, [False])

        def boom():
            raise AssertionError("config must not be read when env decides")

        recorder, _, _ = self.call(ns("remote"), environ={"SANDBOX_AUTO_UPDATE": "1"}, config=boom)
        self.assertEqual(recorder.enabled_answers, [True])

    def test_env_on_beats_config_off(self):
        recorder, _, _ = self.call(ns("remote"), environ={"SANDBOX_AUTO_UPDATE": "on"},
                                   config=lambda: False)
        self.assertEqual(recorder.enabled_answers, [True])

    def test_unreadable_config_defaults_on(self):
        def broken():
            raise SystemExit(1)

        recorder, _, _ = self.call(ns("remote"), config=broken)
        self.assertEqual(recorder.enabled_answers, [True])


class OutcomeTests(HookCase):
    def test_updated_prints_one_line_and_reexecs_with_the_guard(self):
        argv = [str(ENTRY), "deploy", "--remote", "demo", "--ref", "main"]
        decision = Updated(OLD, NEW, "origin/latest")
        recorder, err, out = self.call(ns("deploy", remote="demo"), decision=decision,
                                       argv=argv, environ={"KEEP": "x"})
        self.assertEqual(out, "")
        self.assertEqual(err, "sb: controller updated 1111111 -> 2222222 from origin/latest; "
                              "re-running the command.\n")
        self.assertEqual(len(recorder.execs), 1)
        path, new_argv, env = recorder.execs[0]
        self.assertEqual(path, PYTHON)
        self.assertEqual(new_argv, [PYTHON, str(ENTRY), "deploy", "--remote", "demo", "--ref", "main"])
        self.assertEqual(env["SANDBOX_AUTO_UPDATE_REEXEC"], "1")
        self.assertEqual(env["KEEP"], "x")

    def test_updated_prints_extra_notices_before_the_update_line(self):
        decision = Updated(OLD, NEW, "origin/latest", notices=("sb: MCP note.",))
        _, err, _ = self.call(ns("remote"), decision=decision)
        self.assertEqual(err.splitlines()[0], "sb: MCP note.")
        self.assertTrue(err.splitlines()[1].startswith("sb: controller updated"))

    def test_already_moved_reexecs(self):
        recorder, err, _ = self.call(ns("remote"), decision=AlreadyMoved(OLD, NEW))
        self.assertEqual(len(recorder.execs), 1)
        self.assertIn("2222222", err)

    def test_skip_prints_its_notice_and_continues(self):
        decision = Skip(SkipReason.DIRTY, "sb: controller not updated: dirty.")
        recorder, err, out = self.call(ns("remote"), decision=decision)
        self.assertEqual(err, "sb: controller not updated: dirty.\n")
        self.assertEqual(out, "")
        self.assertEqual(recorder.execs, [])

    def test_no_action_is_silent(self):
        recorder, err, out = self.call(ns("remote"), decision=NoAction(SkipReason.NO_HINT))
        self.assertEqual((err, out, recorder.execs), ("", "", []))

    def test_rolled_back_prints_and_continues_on_old_code(self):
        decision = RolledBack(OLD, NEW, "sb: rolled back.")
        recorder, err, _ = self.call(ns("remote"), decision=decision)
        self.assertEqual(err, "sb: rolled back.\n")
        self.assertEqual(recorder.execs, [])

    def test_stranded_exits_one(self):
        decision = Stranded(NEW, "sb: stranded.")
        with self.assertRaises(SystemExit) as raised:
            self.call(ns("remote"), decision=decision)
        self.assertEqual(raised.exception.code, 1)

    def test_engine_errors_are_swallowed(self):
        recorder, err, out = self.call(ns("remote"), decision=None)
        self.assertEqual((err, out, recorder.execs), ("", "", []))


class CliWiringTests(unittest.TestCase):
    def test_cli_calls_the_hook_right_after_parsing(self):
        source = (ROOT / "sandbox" / "cli.py").read_text(encoding="utf-8")
        parse_at = source.index("args = p.parse_args(raw_argv)")
        hook_at = source.index("maybe_auto_update(args")
        migrate_at = source.index("maybe_auto_migrate()")
        feedback_at = source.index('COMMANDS["feedback"]({}, args)')
        self.assertLess(parse_at, hook_at)
        self.assertLess(hook_at, migrate_at)
        self.assertLess(hook_at, feedback_at)


if __name__ == "__main__":
    unittest.main()
