"""Spec 059: supervised long-running secret session.

Fixtures use synthetic values only. No real secret source is read.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from sandbox.secrets.models import (
    DEFAULT_SESSION_SECONDS,
    END_REASONS,
    MAX_SESSION_SECONDS,
    RunResult,
    SecretBrokerError,
    SessionResult,
)
from tests.subprocess_support import synthetic_environment


SECRET = "TestOnly_" + "Session123456789AbCd"
REPOSITORY = Path(__file__).parent.parent


def _gone(pid: int, within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.05)
    return False


class Sink:
    """In-memory display sink recording when each write arrived."""

    def __init__(self):
        self.writes: list[tuple[float, bytes]] = []

    def __call__(self, data: bytes) -> None:
        self.writes.append((time.monotonic(), bytes(data)))

    def text(self) -> str:
        return b"".join(data for _at, data in self.writes).decode()


class SessionModelTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(DEFAULT_SESSION_SECONDS, 28_800)
        self.assertEqual(MAX_SESSION_SECONDS, 43_200)
        self.assertEqual(END_REASONS, ("lifetime_expired", "interrupted", "hangup", "child_exited"))

    def test_result_dict_is_metadata_only(self):
        result = SessionResult("child_exited", 0, 12.0, 0, 60)
        self.assertEqual(set(result.as_dict()), {
            "end_reason", "exit_code", "elapsed_class", "dropped_chunks", "lifetime_seconds",
            "group_ended",
        })
        self.assertTrue(result.as_dict()["group_ended"])
        self.assertFalse(SessionResult("hangup", None, 1.0, 0, 60, False).as_dict()["group_ended"])
        self.assertNotIn("elapsed_seconds", result.as_dict())
        self.assertNotIn("output", result.as_dict())

    def test_elapsed_class_boundaries(self):
        cases = {
            0.5: "under_1s", 5: "1_to_10s", 30: "10_to_60s", 300: "1_to_10m",
            1_800: "10_to_60m", 7_200: "1_to_4h", 21_600: "4_to_8h",
            36_000: "8_to_12h", 45_000: "12h_plus",
        }
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                result = SessionResult("lifetime_expired", None, seconds, 0, 43_200)
                self.assertEqual(result.as_dict()["elapsed_class"], expected)

    def test_run_result_classes_unchanged(self):
        result = RunResult(0, "exited", "", False, 1_799)
        self.assertEqual(result.as_dict()["elapsed_class"], "60s_plus")


class SessionSignalsTests(unittest.TestCase):
    def setUp(self):
        from sandbox.secrets.session import SessionSignals
        self.SessionSignals = SessionSignals

    def test_each_signal_records_its_reason(self):
        from sandbox.secrets.session import TERMINATION_SIGNALS
        # FR-010: every catchable termination request ends the session, not
        # just the four the terminal generates.
        expected = {
            signal.SIGINT: "interrupted", signal.SIGHUP: "hangup",
            signal.SIGTERM: "interrupted", signal.SIGQUIT: "interrupted",
            signal.SIGUSR1: "interrupted", signal.SIGUSR2: "interrupted",
            signal.SIGALRM: "interrupted", signal.SIGVTALRM: "interrupted",
            signal.SIGPROF: "interrupted", signal.SIGXCPU: "interrupted",
        }
        self.assertEqual(TERMINATION_SIGNALS, expected)
        for name in ("SIGKILL", "SIGSTOP", "SIGPIPE", "SIGXFSZ", "SIGSEGV", "SIGCHLD",
                     "SIGTSTP", "SIGWINCH"):
            self.assertNotIn(getattr(signal, name), TERMINATION_SIGNALS)
        for signum, reason in TERMINATION_SIGNALS.items():
            with self.subTest(signal=signum), self.SessionSignals() as signals:
                os.kill(os.getpid(), signum)
                time.sleep(0.01)
                self.assertEqual(signals.reason, reason)

    def test_only_first_reason_is_kept(self):
        with self.SessionSignals() as signals:
            os.kill(os.getpid(), signal.SIGHUP)
            time.sleep(0.01)
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.01)
            self.assertEqual(signals.reason, "hangup")
            signals.request_end("interrupted")
            self.assertEqual(signals.reason, "hangup")

    def test_sigtstp_is_ignored_inside(self):
        with self.SessionSignals() as signals:
            self.assertIs(signal.getsignal(signal.SIGTSTP), signal.SIG_IGN)
            os.kill(os.getpid(), signal.SIGTSTP)
            time.sleep(0.01)
            self.assertIsNone(signals.reason)

    def test_inherited_ignored_hangup_is_overridden(self):
        previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
        try:
            with self.SessionSignals() as signals:
                os.kill(os.getpid(), signal.SIGHUP)
                time.sleep(0.01)
                self.assertEqual(signals.reason, "hangup")
            self.assertIs(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
        finally:
            signal.signal(signal.SIGHUP, previous)

    def test_dispositions_restored_including_after_exception(self):
        from sandbox.secrets.session import TERMINATION_SIGNALS
        handled = (*TERMINATION_SIGNALS, signal.SIGTSTP)
        before = {signum: signal.getsignal(signum) for signum in handled}
        with self.SessionSignals():
            pass
        self.assertEqual({signum: signal.getsignal(signum) for signum in handled}, before)
        with self.assertRaises(RuntimeError):
            with self.SessionSignals():
                raise RuntimeError("fixture")
        self.assertEqual({signum: signal.getsignal(signum) for signum in handled}, before)

    def test_request_end_records_reason(self):
        signals = self.SessionSignals()
        signals.request_end("hangup")
        self.assertEqual(signals.reason, "hangup")
        with self.assertRaises(ValueError):
            self.SessionSignals().request_end("bogus")


class DisplayFilterTests(unittest.TestCase):
    def setUp(self):
        from sandbox.secrets.runner import SessionDisplayFilter
        self.Filter = SessionDisplayFilter

    def run_filter(self, *chunks):
        display = self.Filter()
        return "".join(display.feed(chunk) for chunk in chunks) + display.finish()

    def test_sgr_kept_other_sequences_removed_whole(self):
        self.assertEqual(self.run_filter("\x1b[31mred\x1b[0m\n"), "\x1b[31mred\x1b[0m\n")
        self.assertEqual(self.run_filter("\x1b[1;38;5;208mhi\x1b[m\n"), "\x1b[1;38;5;208mhi\x1b[m\n")
        self.assertEqual(self.run_filter("a\x1b[2Jb\n"), "ab\n")
        self.assertEqual(self.run_filter("a\x1b[?25lb\n"), "ab\n")
        self.assertEqual(self.run_filter("a\x1b]0;title\x07b\n"), "ab\n")
        self.assertEqual(self.run_filter("a\x1b]8;;http://x\x1b\\b\n"), "ab\n")
        self.assertEqual(self.run_filter("a\x1bPdata\x1b\\b\n"), "ab\n")
        self.assertEqual(self.run_filter("a\x1b(Bb\x1b7c\n"), "abc\n")
        self.assertEqual(self.run_filter("a\x1b[31\nb\n"), "a\nb\n")
        self.assertEqual(self.run_filter("a\x1b[31"), "a")
        self.assertEqual(self.run_filter("a\x9b2Jb\x9d0;t\x07c\x85d\n"), "abcd\n")
        self.assertEqual(self.run_filter("t\tx\r\n\x00\x07\x08\x7f!"), "t\tx\r\n!")

    def test_incomplete_sequence_held_across_chunks(self):
        display = self.Filter()
        self.assertEqual(display.feed("a\x1b"), "a")
        self.assertEqual(display.feed("[3"), "")
        self.assertEqual(display.feed("1mred"), "\x1b[31mred")
        self.assertEqual(display.feed("\x1b]0;ti"), "")
        self.assertEqual(display.feed("tle\x07ok"), "ok")
        self.assertEqual(display.finish(), "")

    def test_overlong_sequences_are_dropped(self):
        out = self.run_filter("a\x1b]" + "x" * 100_000 + "\x07b\n")
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)
        self.assertTrue(out.startswith("a") and out.endswith("b\n"))
        self.assertLess(len(out), 100_000)
        self.assertEqual(self.run_filter("a\x1b]0;t\x1b[31mb\n"), "a\x1b[31mb\n")
        out = self.run_filter("a\x1b[" + "1;" * 1_000 + "mb\n")
        self.assertNotIn("\x1b", out)
        self.assertTrue(out.startswith("a"))


class ColourRedactionTests(unittest.TestCase):
    def setUp(self):
        from sandbox.secrets.session import _ColourPreservingRedaction
        self.redaction = _ColourPreservingRedaction((SECRET.encode(),))

    def run_text(self, *chunks):
        return "".join(self.redaction.feed(chunk) for chunk in chunks) + self.redaction.finish()

    def test_colour_split_value_is_still_redacted(self):
        out = self.run_text("v=" + SECRET[:6] + "\x1b[31m" + SECRET[6:] + "\x1b[0m done\n")
        self.assertNotIn(SECRET[6:], out)
        self.assertIn("[REDACTED]", out)
        self.assertIn("done\n", out)

    def test_coloured_url_line_keeps_colour(self):
        line = "  Local:   \x1b[36mhttp://localhost:\x1b[1m5173\x1b[22m/\x1b[39m\n"
        self.assertEqual(self.run_text(line[:20], line[20:]), line)

    def test_value_split_across_chunks_with_colour(self):
        out = self.run_text("\x1b[32m" + SECRET[:4], SECRET[4:] + "\x1b[0m\n")
        self.assertNotIn(SECRET, out)
        self.assertNotIn(SECRET[4:], out)
        self.assertIn("[REDACTED]", out)


class RunSessionTests(unittest.TestCase):
    def setUp(self):
        from sandbox.secrets import session
        self.session = session

    def run_child(self, program, *, secrets=None, lifetime=10, signals=None, sink=None):
        sink = sink if sink is not None else Sink()
        result = self.session.run_session(
            [sys.executable, "-c", program], secrets=secrets or {"API_TOKEN": SECRET},
            lifetime_seconds=lifetime, signals=signals, display=sink,
        )
        return result, sink

    def test_bindings_present_minimal_environment_and_parent_unchanged(self):
        program = (
            "import os,json; print(json.dumps(sorted(os.environ))); "
            "print('match=' + str(os.environ['ACCESS_KEY'] == 'TestOnly_' + 'Session123456789AbCd'))"
        )
        self.assertIsNone(os.environ.get("ACCESS_KEY"))
        result, sink = self.run_child(program, secrets={"ACCESS_KEY": SECRET})
        self.assertIsNone(os.environ.get("ACCESS_KEY"))
        text = sink.text()
        self.assertIn("match=True", text)
        names = json.loads(text.splitlines()[0])
        allowed = {"PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "TERM", "ACCESS_KEY",
                   "__CF_USER_TEXT_ENCODING"}
        self.assertEqual([name for name in names if name not in allowed
                          and not name.startswith("LC_")], [])
        self.assertIn("ACCESS_KEY", names)
        self.assertEqual(result.end_reason, "child_exited")

    def test_popen_called_once_with_argv_and_closed_stdin(self):
        real = subprocess.Popen
        calls = []

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        argv = [sys.executable, "-c",
                "import sys; data = sys.stdin.read(); print('eof=' + str(data == '')); sys.exit(3)"]
        sink = Sink()
        with mock.patch.object(self.session.subprocess, "Popen", side_effect=spy):
            result = self.session.run_session(
                argv, secrets={"API_TOKEN": SECRET}, lifetime_seconds=10, display=sink,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0], argv)
        self.assertIs(calls[0][1]["stdin"], subprocess.DEVNULL)
        self.assertTrue(calls[0][1]["start_new_session"])
        self.assertIn("eof=True", sink.text())
        self.assertEqual((result.end_reason, result.exit_code), ("child_exited", 3))

    def test_exit_zero(self):
        result, _sink = self.run_child("print('done')")
        self.assertEqual((result.end_reason, result.exit_code), ("child_exited", 0))
        self.assertNotIn("output", result.as_dict())

    def test_line_reaches_display_before_child_exits(self):
        program = (
            "import sys,time; print('first line', flush=True); "
            "sys.stdout.flush(); time.sleep(2); print('last')"
        )
        started = time.monotonic()
        result, sink = self.run_child(program)
        finished = time.monotonic()
        first = next(at for at, data in sink.writes if b"first line" in data)
        self.assertLess(first - started, 1.8)
        self.assertLess(first, finished - 1.0)
        self.assertEqual(result.end_reason, "child_exited")

    def test_value_redacted_whole_and_split(self):
        program = (
            "import os,sys,time; s=os.environ['API_TOKEN']; print(s, flush=True); "
            "sys.stdout.write(s[:9]); sys.stdout.flush(); time.sleep(.1); "
            "sys.stdout.write(s[9:] + '\\n'); sys.stdout.flush()"
        )
        _result, sink = self.run_child(program)
        text = sink.text()
        self.assertNotIn(SECRET, text)
        self.assertNotIn(SECRET[:9], text)
        self.assertEqual(text.count("[REDACTED]"), 2)

    def test_colour_kept_and_other_sequences_removed(self):
        program = (
            "import sys; sys.stdout.write('\\x1b[31mred\\x1b[0m\\n'); "
            "sys.stdout.write('a\\x1b[2Jb\\n'); sys.stdout.write('c\\x1b]0;title\\x07d\\n'); "
            "sys.stdout.write('e\\x1b[31\\nf\\n')"
        )
        _result, sink = self.run_child(program)
        text = sink.text()
        self.assertIn("\x1b[31mred\x1b[0m\n", text)
        self.assertIn("ab\n", text)
        self.assertIn("cd\n", text)
        self.assertIn("e\nf\n", text)
        self.assertNotIn("[2J", text)
        self.assertNotIn("title", text)

    def test_trailing_partial_line_shown_at_end(self):
        _result, sink = self.run_child("import sys; sys.stdout.write('tail-without-newline')")
        self.assertTrue(sink.text().endswith("tail-without-newline"))

    def test_grandchild_gone_after_child_exits(self):
        program = (
            "import subprocess,sys; p = subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(30)']); print('grandchild=%d' % p.pid, flush=True)"
        )
        result, sink = self.run_child(program)
        grandchild = int(re.search(r"grandchild=(\d+)", sink.text()).group(1))
        self.assertEqual(result.end_reason, "child_exited")
        self.assertTrue(_gone(grandchild, 5))


class SessionBoundTests(unittest.TestCase):
    def setUp(self):
        from sandbox.secrets import session
        self.session = session

    def run_child(self, program, *, lifetime=10, signals=None, display=None):
        sink = display if display is not None else Sink()
        result = self.session.run_session(
            [sys.executable, "-c", program], secrets={"API_TOKEN": SECRET},
            lifetime_seconds=lifetime, signals=signals, display=sink,
        )
        return result, sink

    GRANDCHILD = (
        "import subprocess,sys,time; p = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)']); print('grandchild=%d' % p.pid, flush=True); "
        "time.sleep(60)"
    )

    def test_lifetime_expiry_ends_group(self):
        started = time.monotonic()
        result, sink = self.run_child(self.GRANDCHILD, lifetime=1)
        self.assertEqual(result.end_reason, "lifetime_expired")
        self.assertIsNone(result.exit_code)
        self.assertLess(time.monotonic() - started, 6)
        grandchild = int(re.search(r"grandchild=(\d+)", sink.text()).group(1))
        self.assertTrue(_gone(grandchild, 5))

    def test_request_end_from_timer_thread(self):
        for reason in ("interrupted", "hangup"):
            with self.subTest(reason=reason):
                signals = self.session.SessionSignals()
                timer = threading.Timer(0.5, signals.request_end, args=(reason,))
                timer.start()
                started = time.monotonic()
                result, sink = self.run_child(self.GRANDCHILD, lifetime=30, signals=signals)
                timer.join()
                self.assertEqual(result.end_reason, reason)
                self.assertLess(time.monotonic() - started, 5.5)
                match = re.search(r"grandchild=(\d+)", sink.text())
                if match:
                    self.assertTrue(_gone(int(match.group(1)), 5))

    def test_reason_before_launch_starts_no_child(self):
        signals = self.session.SessionSignals()
        signals.request_end("interrupted")
        with mock.patch.object(self.session.subprocess, "Popen") as popen:
            result, _sink = self.run_child("print(1)", signals=signals)
        popen.assert_not_called()
        self.assertEqual((result.end_reason, result.exit_code), ("interrupted", None))

    def test_display_failure_is_hangup(self):
        for error in (OSError(errno.EIO, "eio"), BrokenPipeError()):
            with self.subTest(error=type(error).__name__):
                calls = []

                def failing(data, error=error):
                    calls.append(data)
                    raise error

                started = time.monotonic()
                result, _sink = self.run_child(
                    "import time\nwhile True:\n print('beat', flush=True); time.sleep(.1)",
                    lifetime=30, display=failing,
                )
                self.assertEqual(result.end_reason, "hangup")
                self.assertEqual(len(calls), 1)
                self.assertLess(time.monotonic() - started, 5.5)

    def test_child_ignoring_sigterm_is_killed(self):
        program = (
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "import os; print('pid=%d' % os.getpid(), flush=True); time.sleep(60)"
        )
        result, sink = self.run_child(program, lifetime=1)
        ended = time.monotonic()
        self.assertEqual(result.end_reason, "lifetime_expired")
        pid = int(re.search(r"pid=(\d+)", sink.text()).group(1))
        self.assertTrue(_gone(pid, 5 - (time.monotonic() - ended)))

    def test_wall_clock_jump_expires_session(self):
        real_wall = self.session._wall_now
        offset = {"value": 0.0}
        mono_frozen = self.session._mono_now()

        def wall():
            return real_wall() + offset["value"]

        timer = threading.Timer(0.5, lambda: offset.__setitem__("value", 10_000.0))
        timer.start()
        started = time.monotonic()
        with mock.patch.object(self.session, "_wall_now", side_effect=wall), \
             mock.patch.object(self.session, "_mono_now", return_value=mono_frozen):
            result, _sink = self.run_child("import time; time.sleep(60)", lifetime=3_600)
        timer.join()
        self.assertEqual(result.end_reason, "lifetime_expired")
        self.assertLess(time.monotonic() - started, 6)

    def test_redaction_failure_drops_chunk_and_continues(self):
        from sandbox.services.redaction import RedactionError, StreamingRedactor
        state = {"failed": False}

        class Flaky(StreamingRedactor):
            def feed(self, chunk, *, final=False):
                if b"poison" in chunk and not state["failed"]:
                    state["failed"] = True
                    raise RedactionError("fixture")
                return super().feed(chunk, final=final)

        program = (
            "import os,sys,time; print('poison', flush=True); time.sleep(.3); "
            "print('after ' + os.environ['API_TOKEN'], flush=True)"
        )
        with mock.patch.object(self.session, "StreamingRedactor", Flaky):
            result, sink = self.run_child(program)
        text = sink.text()
        self.assertEqual(result.dropped_chunks, 1)
        self.assertNotIn("poison", text)
        self.assertIn("after [REDACTED]", text)
        self.assertNotIn(SECRET, text)

    def test_invalid_lifetime_refused(self):
        for lifetime in (0, 43_201, 1.5, True, "5"):
            with self.subTest(lifetime=lifetime), \
                 mock.patch.object(self.session.subprocess, "Popen") as popen:
                with self.assertRaises(SecretBrokerError) as raised:
                    self.run_child("print(1)", lifetime=lifetime)
                self.assertEqual(raised.exception.code, "lifetime_invalid")
                popen.assert_not_called()

    def test_escalating_command_refused_before_launch(self):
        real = subprocess.Popen
        calls = []
        with mock.patch.object(self.session.subprocess, "Popen",
                               side_effect=lambda *a, **k: calls.append(a) or real(*a, **k)):
            for argv in (["sudo", "true"], ["/usr/bin/doas", "true"], ["su", "-c", "true"],
                         ["pkexec", "true"], ["run0", "true"], ["sudoedit", "f"]):
                with self.subTest(argv=argv):
                    with self.assertRaises(SecretBrokerError) as raised:
                        self.session.run_session(
                            argv, secrets={"API_TOKEN": SECRET}, lifetime_seconds=5,
                            display=Sink())
                    self.assertEqual(raised.exception.code, "escalation_unsupported")
        self.assertEqual(calls, [])
        # Only the executable is inspected: "sudo" as an argument is fine.
        result, _sink = self.run_child("import sys; print(sys.argv)")
        self.assertEqual(result.end_reason, "child_exited")
        self.assertTrue(result.group_ended)

    def test_unreachable_group_member_is_reported_not_claimed_ended(self):
        # A member the broker may not signal (privilege escalation inside the
        # child) survives the bounded end; the result says so instead of
        # pretending the group is gone, and the bound still holds.
        program = "import os; print('pid=%d' % os.getpid(), flush=True); import time; time.sleep(60)"
        real_state = self.session._group_state
        with mock.patch.object(self.session, "_group_state",
                               side_effect=lambda pgid: "unreachable"
                               if real_state(pgid) == "gone" else real_state(pgid)):
            started = time.monotonic()
            result, sink = self.run_child(program, lifetime=1)
        self.assertEqual(result.end_reason, "lifetime_expired")
        self.assertFalse(result.group_ended)
        self.assertLess(time.monotonic() - started, 7)
        pid = int(re.search(r"pid=(\d+)", sink.text()).group(1))
        self.assertTrue(_gone(pid, 1))

    def test_invalid_command_refused(self):
        with self.assertRaises(SecretBrokerError) as raised:
            self.session.run_session([], secrets={"API_TOKEN": SECRET},
                                     lifetime_seconds=5, display=Sink())
        self.assertEqual(raised.exception.code, "command_invalid")


class SessionPtyEndToEndTests(unittest.TestCase):
    """Real ``./sb secrets run --session`` as the foreground job of a pty."""

    CHILD = (
        "import os,subprocess,sys,time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print('grandchild=%d' % p.pid, flush=True)\n"
        "print('canary ' + os.environ['API_TOKEN'], flush=True)\n"
        "while True:\n"
        "    print('heartbeat', flush=True)\n"
        "    time.sleep(0.2)\n"
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "compose.yaml").write_text("services: {}\n")
        (self.project / "sandbox.config.json").write_text(json.dumps({
            "kind": "compose",
            "compose": {"file": "compose.yaml", "service": "web",
                        "internal_port": 80, "health_path": "/"},
            "secrets": {"sources": {"fixture": {"path": ".env.fixture"}}},
        }))
        self.source = self.project / ".env.fixture"
        self.source.write_text(f"API_TOKEN={SECRET}\n")
        self.source.chmod(0o600)

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, *extra):
        master, slave = pty.openpty()

        def controlling_tty():
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        environment = synthetic_environment({
            "SANDBOX_HOME": str(self.home),
            "SANDBOX_PROJECT_ROOTS": str(self.root),
            "TERM": "xterm",
        })
        stderr_path = self.root / "stderr.txt"
        with open(stderr_path, "wb") as stderr:
            process = subprocess.Popen(
                [str(REPOSITORY / "sb"), "secrets", "run", "--session", *extra,
                 "--source", "fixture", "--key", "API_TOKEN",
                 "--project-dir", str(self.project), "--",
                 sys.executable, "-c", self.CHILD],
                cwd=REPOSITORY, env=environment, stdin=slave, stdout=slave,
                stderr=stderr, start_new_session=True, preexec_fn=controlling_tty,
                close_fds=True,
            )
        os.close(slave)
        return process, master, stderr_path

    @staticmethod
    def read_until(master, transcript, needle, timeout):
        deadline = time.monotonic() + timeout
        while needle not in transcript and time.monotonic() < deadline:
            ready, _w, _x = select.select([master], [], [], 0.1)
            if ready:
                try:
                    data = os.read(master, 65_536)
                except OSError:
                    break
                if not data:
                    break
                transcript.extend(data)
        return needle in transcript

    def finish(self, process, master, transcript, timeout=10):
        deadline = time.monotonic() + timeout
        while process.poll() is None and time.monotonic() < deadline:
            self.read_until(master, transcript, b"\x00never\x00", 0.1)
        self.read_until(master, transcript, b"\x00never\x00", 0.3)
        os.close(master)
        if process.poll() is None:
            process.kill()
            process.wait()
            self.fail("broker did not exit")
        return process.returncode

    def audit_outcome(self):
        files = list(self.home.rglob("audit.jsonl"))
        self.assertEqual(len(files), 1)
        events = [json.loads(line) for line in files[0].read_text().splitlines()]
        sessions = [event for event in events if event["operation"] == "use_session"]
        return sessions[-1]

    def assert_no_canary(self, transcript, stderr_path):
        self.assertNotIn(SECRET.encode(), bytes(transcript))
        self.assertNotIn(SECRET.encode(), stderr_path.read_bytes())
        for path in self.root.rglob("*"):
            if path.is_file() and path != self.source:
                self.assertNotIn(SECRET.encode(), path.read_bytes(), str(path))

    def run_case(self, extra, action, expected_exit, expected_reason):
        process, master, stderr_path = self.start(*extra)
        transcript = bytearray()
        try:
            self.assertTrue(self.read_until(master, transcript, b"heartbeat", 20),
                            bytes(transcript) + stderr_path.read_bytes())
            sent = time.monotonic()
            action(process, master)
            code = self.finish(process, master, transcript)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        self.assertLess(time.monotonic() - sent, 8)
        self.assertEqual(code, expected_exit, bytes(transcript) + stderr_path.read_bytes())
        self.assertIn(b"secrets session: started", bytes(transcript))
        self.assertIn(f"end_reason={expected_reason}".encode(), bytes(transcript))
        grandchild = int(re.search(rb"grandchild=(\d+)", bytes(transcript)).group(1))
        self.assertTrue(_gone(grandchild, 5))
        self.assertIn(b"[REDACTED]", bytes(transcript))
        outcome = self.audit_outcome()
        self.assertEqual((outcome["phase"], outcome["decision"], outcome["reason_code"]),
                         ("outcome", "succeeded", expected_reason))
        self.assert_no_canary(transcript, stderr_path)

    def test_interrupt_from_terminal(self):
        self.run_case((), lambda process, master: os.write(master, b"\x03"), 130, "interrupted")

    def test_hangup(self):
        self.run_case((), lambda process, master: os.killpg(process.pid, signal.SIGHUP),
                      129, "hangup")

    def test_terminal_closed_exits_129(self):
        process, master, stderr_path = self.start()
        transcript = bytearray()
        try:
            self.assertTrue(self.read_until(master, transcript, b"heartbeat", 20),
                            bytes(transcript) + stderr_path.read_bytes())
            os.close(master)
            process.wait(timeout=8)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        self.assertEqual(process.returncode, 129, stderr_path.read_bytes())
        grandchild = int(re.search(rb"grandchild=(\d+)", bytes(transcript)).group(1))
        self.assertTrue(_gone(grandchild, 5))
        outcome = self.audit_outcome()
        self.assertEqual((outcome["decision"], outcome["reason_code"]), ("succeeded", "hangup"))
        self.assert_no_canary(transcript, stderr_path)

    def test_lifetime_expiry(self):
        self.run_case(("--lifetime-seconds", "3"), lambda process, master: None,
                      0, "lifetime_expired")


if __name__ == "__main__":
    unittest.main()
