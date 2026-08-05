"""Self-tests for the PTY harness proving input, resize, signal, and cleanup."""

import os
import signal
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.pty_harness import PtyHarness

PROBE = [sys.executable, os.path.join(
    os.path.dirname(__file__), "fixtures", "pty_probe.py")]


class PtyHarnessSelfTests(unittest.TestCase):

    # ── basic spawn/input/output ────────────────────────────────────

    def test_echo_mode(self):
        h = PtyHarness(PROBE + ["echo"], rows=24, cols=80)
        h.wait_for(b"READY")
        h.send(b"x")
        h.wait_for(b"KEY 'x'")
        status = h.wait_exit()
        self.assertEqual(status, 0)
        h.close()

    def test_initial_dimensions(self):
        h = PtyHarness(PROBE + ["echo"], rows=30, cols=100)
        h.wait_for(b"READY 30x100")
        h.send(b"q")
        h.wait_for(b"KEY 'q'")
        status = h.wait_exit()
        self.assertEqual(status, 0)
        h.close()

    # ── live resize (via dimensions mode with signal) ───────────────

    def test_live_resize(self):
        h = PtyHarness(PROBE + ["resize"], rows=24, cols=80)
        h.wait_for(b"INIT 24x80")
        h.resize(40, 120)
        h.wait_for(b"RESIZED 40x120")
        status = h.wait_exit()
        self.assertEqual(status, 0)
        h.close()

    # ── signal exit ─────────────────────────────────────────────────

    def test_signal_exit(self):
        h = PtyHarness(PROBE + ["signal_die"], rows=24, cols=80)
        h.wait_for(b"READY")
        os.kill(h._child_pid, signal.SIGTERM)
        status = h.wait_exit()
        # Negative status means killed by signal
        self.assertEqual(status, -signal.SIGTERM)
        h.close()

    # ── timeout cleanup ─────────────────────────────────────────────

    def test_timeout_cleanup(self):
        h = PtyHarness(PROBE + ["sleep"], rows=24, cols=80)
        h.wait_for(b"SLEEPING")
        with self.assertRaises(TimeoutError):
            h.wait_for(b"WILL_NEVER_APPEAR", timeout=2.0)
        # Harness should still be cleanable
        h.terminate()
        h.close()
        # Verify child is reaped — no zombie
        self.assertIsNone(h._child_pid)

    # ── no leaked children ──────────────────────────────────────────

    def test_no_leaked_child_after_close(self):
        h = PtyHarness(PROBE + ["echo"], rows=24, cols=80)
        h.wait_for(b"READY")
        h.send(b"z")
        h.wait_for(b"KEY 'z'")
        status = h.wait_exit()
        self.assertEqual(status, 0)
        h.close()
        self.assertIsNone(h._child_pid)


if __name__ == "__main__":
    unittest.main()
