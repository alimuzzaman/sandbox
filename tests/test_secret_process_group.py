"""Process-group cleanup reports a child that exited on its own as ended."""

from __future__ import annotations

import subprocess
import sys
import time
import unittest
from unittest import mock

from sandbox.secrets.process_group import terminate_process_group


class _LatePoll(subprocess.Popen):
    """Skip the first reap, as when the child exits just after cleanup polls."""

    _skipped = False

    def poll(self):
        if not self._skipped:
            self._skipped = True
            return None
        return super().poll()


class TerminateProcessGroupTests(unittest.TestCase):
    def test_child_that_exits_between_poll_and_group_probe_counts_as_ended(self) -> None:
        # Darwin answers killpg(pgid, 0) with EPERM while the group holds only
        # the unreaped zombie leader; that must not read as an unreachable group.
        process = _LatePoll([sys.executable, "-I", "-c", "pass"],
                            start_new_session=True, close_fds=True)
        time.sleep(0.5)  # let it exit and stay an unreaped zombie

        self.assertTrue(terminate_process_group(process))
        self.assertIsNotNone(process.returncode)

    def test_leader_still_exiting_is_waited_for_not_reported_unreachable(self) -> None:
        # Darwin also answers EPERM while the leader is exiting but not yet
        # reapable; the bounded wait must reap it instead of giving up.
        class Exiting:
            pid = 424_242
            returncode = None
            polls = 0

            def poll(self):
                self.polls += 1
                if self.polls >= 3:
                    self.returncode = 0
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

        process = Exiting()

        def killpg(_pgid, _sig):
            if process.returncode is None:
                raise PermissionError()
            raise ProcessLookupError()

        with mock.patch("sandbox.secrets.process_group.os.killpg", side_effect=killpg):
            self.assertTrue(terminate_process_group(process))

    def test_group_that_stays_unsignalable_after_reap_is_not_ended(self) -> None:
        class Reaped:
            pid = 424_243
            returncode = 0

            def poll(self):
                return 0

        with mock.patch("sandbox.secrets.process_group.os.killpg", side_effect=PermissionError()):
            self.assertFalse(terminate_process_group(Reaped()))

    def test_running_child_is_terminated(self) -> None:
        process = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(30)"],
                                   start_new_session=True, close_fds=True)
        self.assertTrue(terminate_process_group(process))
        self.assertIsNotNone(process.returncode)


if __name__ == "__main__":
    unittest.main()
