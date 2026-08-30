"""Invariants for the edge-preserving bounded output capture.

``_BoundedEdgeCapture`` backs every process stream the CLI records, so its
overflow contract is load-bearing: the retained head plus marker plus tail
must never exceed the limit, and — the subtle first-overflow case where the
overflowing chunk itself is smaller than the tail window — the tail must be
the TRUE last bytes of the full stream, never a duplicated prefix fragment.
"""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.resources.models import ResourceCancellationSignal
from sandbox.services.process import (
    BoundedProcessRunner, _EDGE_TRUNCATION_MARKER, _BoundedEdgeCapture,
)

MARKER = _EDGE_TRUNCATION_MARKER


def stream_of(seed: int, total: int) -> bytes:
    """Deterministic pseudo-random byte stream (fixed seed, no randomness)."""
    out = bytearray()
    state = seed
    while len(out) < total:
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(65 + (state >> 16) % 26)
    return bytes(out[:total])


class TestBoundedEdgeCapture(unittest.TestCase):
    def test_invalid_cancellation_is_rejected_before_spawn(self):
        with patch("sandbox.services.process.subprocess.Popen") as popen:
            with self.assertRaisesRegex(ValueError, "terminal_status"):
                BoundedProcessRunner().run(
                    (sys.executable, "-c", "pass"), cancellation=object(),
                )
        popen.assert_not_called()

    def test_pre_cancelled_signal_returns_without_spawn(self):
        cancellation = ResourceCancellationSignal()
        cancellation.cancel()
        with patch("sandbox.services.process.subprocess.Popen") as popen:
            result = BoundedProcessRunner().run(
                (sys.executable, "-c", "pass"), cancellation=cancellation,
            )
        self.assertEqual(result.returncode, 130)
        popen.assert_not_called()

    def test_later_cancellation_probe_failure_terminates_owned_child(self):
        with tempfile.TemporaryDirectory() as raw:
            ready = Path(raw) / "ready"

            class FailingProbe:
                def terminal_status(self):
                    if ready.exists():
                        raise RuntimeError("untrusted detail")
                    return None

            script = (
                "import pathlib,time; "
                "print('retained', flush=True); "
                f"pathlib.Path({str(ready)!r}).write_text('ready'); "
                "time.sleep(30)"
            )
            started = time.monotonic()
            result = BoundedProcessRunner(max_output=1024).run((
                sys.executable, "-c", script,
            ), timeout=10, cancellation=FailingProbe())
        self.assertEqual(result.returncode, 130)
        self.assertIn("retained", result.stdout)
        self.assertIn("cancellation probe failed", result.stderr)
        self.assertNotIn("untrusted detail", result.stderr)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_cancellation_terminates_and_reaps_owned_child_with_partial_output(self):
        with tempfile.TemporaryDirectory() as raw:
            ready = Path(raw) / "ready"
            signal = ResourceCancellationSignal()

            def cancel_when_ready():
                deadline = time.monotonic() + 2
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.005)
                signal.cancel()

            watcher = threading.Thread(target=cancel_when_ready)
            watcher.start()
            script = (
                "import pathlib,time; "
                "print('completed', flush=True); "
                f"pathlib.Path({str(ready)!r}).write_text('ready'); "
                "time.sleep(30)"
            )
            started = time.monotonic()
            try:
                result = BoundedProcessRunner(max_output=1024).run((
                    sys.executable, "-c", script,
                ), timeout=10, cancellation=signal)
            finally:
                watcher.join(timeout=2)
        self.assertEqual(result.returncode, 130)
        self.assertIn("completed", result.stdout)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_post_spawn_cancellation_does_not_allow_future_output(self):
        with tempfile.TemporaryDirectory() as raw:
            child_started = Path(raw) / "child-started"
            mutation = Path(raw) / "ran-after-cancel"
            signal = ResourceCancellationSignal()
            readiness_observed = threading.Event()

            def cancel_after_child_start():
                deadline = time.monotonic() + 2
                while not child_started.exists() and time.monotonic() < deadline:
                    time.sleep(0.005)
                if child_started.exists():
                    readiness_observed.set()
                    signal.cancel()

            watcher = threading.Thread(target=cancel_after_child_start)
            watcher.start()
            script = (
                "import pathlib,time; "
                f"pathlib.Path({str(child_started)!r}).write_text('started'); "
                "time.sleep(0.3); "
                f"pathlib.Path({str(mutation)!r}).write_text('mutated'); "
                "print('ran-after-cancel', flush=True); time.sleep(30)"
            )
            started = time.monotonic()
            try:
                result = BoundedProcessRunner(max_output=1024).run((
                    sys.executable, "-c", script,
                ), timeout=1, cancellation=signal)
            finally:
                watcher.join(timeout=2)
            self.assertTrue(child_started.exists())
            self.assertTrue(readiness_observed.is_set())
            self.assertFalse(mutation.exists())
        self.assertEqual(result.returncode, 130)
        self.assertEqual(result.termination_reason, "cancelled")
        self.assertNotIn("ran-after-cancel", result.stdout)
        self.assertLess(time.monotonic() - started, 2.0)

    def _feed(self, cap: _BoundedEdgeCapture, data: bytes, chunk: int) -> None:
        for i in range(0, len(data), max(chunk, 1)):
            cap.append(data[i:i + chunk])

    def test_zero_limit_is_always_empty(self):
        for chunks in ([b"x"], [b"a" * 999], [b"", b"y", b"z" * 50]):
            cap = _BoundedEdgeCapture(0)
            for c in chunks:
                cap.append(c)
            self.assertEqual(cap.render(), b"")

    def test_below_limit_is_exact_concatenation(self):
        for limit, parts in [
            (5, [b"ab"]),
            (100, [b"hello ", b"world"]),
            (333, [b"", b"A" * 40, b"B", b"C" * 291]),
        ]:
            cap = _BoundedEdgeCapture(limit)
            for p in parts:
                cap.append(p)
            self.assertEqual(cap.render(), b"".join(parts))

    def test_exactly_at_limit_never_truncates(self):
        data = stream_of(7, 100)
        cap = _BoundedEdgeCapture(100)
        self._feed(cap, data, 13)
        self.assertEqual(cap.render(), data)

    def test_overflow_respects_limit_and_true_tail(self):
        # Limits around the marker size and well past it.
        limits = [len(MARKER) - 1, len(MARKER), len(MARKER) + 1, 50, 100, 333]
        for limit in limits:
            available = max(0, limit - len(MARKER))
            head_len = available // 2
            tail_len = available - head_len
            if tail_len == 0 or head_len == 0:
                continue
            for seed, total in [(1, limit + 1), (2, limit * 3), (3, limit * 10)]:
                data = stream_of(seed, total)
                for chunk in (1, 7, limit):
                    cap = _BoundedEdgeCapture(limit)
                    self._feed(cap, data, chunk)
                    got = cap.render()
                    self.assertLessEqual(len(got), limit,
                                         f"limit={limit} chunk={chunk}")
                    expected = (
                        data[:head_len] + MARKER
                        + (data[len(data) - tail_len:] if tail_len else b"")
                    )
                    self.assertEqual(
                        got, expected,
                        f"limit={limit} chunk={chunk} total={total}",
                    )

    def test_small_overflowing_chunk_does_not_duplicate_prefix(self):
        # The historical suspect: overflow with a chunk smaller than the tail
        # window. The rendered tail must still be the stream's true suffix.
        limit = 100
        cap = _BoundedEdgeCapture(limit)
        cap.append(b"A" * 60)
        cap.append(b"B" * 30)   # overflows; smaller than the tail window
        cap.append(b"C" * 20)
        got = cap.render()
        self.assertEqual(len(got), limit)
        self.assertIn(MARKER, got)
        self.assertTrue(got.endswith(b"C" * 20))
        # True suffix: the last tail-window bytes of the real stream.
        available = limit - len(MARKER)
        tail_len = available - available // 2
        self.assertEqual(got[got.index(MARKER) + len(MARKER):],
                         (b"A" * 60 + b"B" * 30 + b"C" * 20)[-tail_len:])

    def test_marker_omitted_when_limit_too_small(self):
        small = len(MARKER) - 1
        data = stream_of(11, small * 4)
        cap = _BoundedEdgeCapture(small)
        self._feed(cap, data, 3)
        got = cap.render()
        self.assertNotIn(MARKER, got)
        self.assertEqual(len(got), small)
        head_len = small // 2
        tail_len = small - head_len
        self.assertEqual(got[:head_len], data[:head_len])
        self.assertEqual(got[head_len:], data[len(data) - tail_len:])

    def test_empty_chunks_are_inert(self):
        cap = _BoundedEdgeCapture(10)
        cap.append(b"")
        cap.append(b"abc")
        cap.append(b"")
        self.assertEqual(cap.render(), b"abc")

    def test_single_byte_drip_overflow(self):
        limit = 60  # larger than the marker so the marker contract applies
        data = stream_of(5, limit * 4)
        cap = _BoundedEdgeCapture(limit)
        for i in range(len(data)):
            cap.append(data[i:i + 1])
        available = limit - len(MARKER)
        head_len = available // 2
        tail_len = available - head_len
        expected = data[:head_len] + MARKER + data[len(data) - tail_len:]
        self.assertEqual(cap.render(), expected)


if __name__ == "__main__":
    unittest.main()
