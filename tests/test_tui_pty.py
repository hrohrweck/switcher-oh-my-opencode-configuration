"""Real PTY integration tests for the v3 curses profile selector.

Drives the REAL curses selector through ``tui.run_profile_tui`` via the
tiny in-test entry ``tests/fixtures/profile_tui_entry.py`` (never the
CLI — cli wiring is Task 16), under TERM=xterm-256color through
``tests/pty_harness.py``.  Profile stores are built in a temp HOME
through the real store/engine, and post-exit outcomes plus the rendered
``omo.jsonc``/``.active`` state are verified from THIS process.

Replaces the v2 PTY suite (whose 4 env failures shared one root cause:
HOMEs seeded with only the canonical active file produced an empty v2
menu, so the child CLI printed 'No configuration files found' and never
launched curses — see .omo/notepads/v3-omo-profiles/issues.md).
"""

import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.pty_harness import PtyHarness

ROOT = Path(__file__).resolve().parent.parent
ENTRY = Path(__file__).resolve().parent / "fixtures" / "profile_tui_entry.py"


class ProfileTuiPtyTests(unittest.TestCase):
    """Each test owns a throwaway HOME and one PTY child."""

    def _spawn(self, home: Path, *, rows: int = 24, cols: int = 80,
               mode: str = "") -> PtyHarness:
        args = [sys.executable, str(ENTRY), mode, str(home)]
        env = {
            "HOME": str(home),
            "PYTHONPATH": str(ROOT / "src"),
            "TERM": "xterm-256color",
        }
        return PtyHarness(args, rows=rows, cols=cols, env=env)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="test-tui-pty-")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    # ── WIDE startup ─────────────────────────────────────────────

    def test_wide_startup_renders_menu_and_details(self):
        h = self._spawn(self.home, rows=40, cols=120)
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            h.wait_for(b"alpha", timeout=5)
            h.wait_for(b"beta", timeout=5)
            h.wait_for(b"gamma", timeout=5)
            h.wait_for(b"Agents (1):", timeout=5)
            h.send(b"q")
            self.assertEqual(h.wait_exit(timeout=5), 0)
        finally:
            h.close()

    # ── apply and exit ───────────────────────────────────────────

    def test_enter_applies_second_profile(self):
        h = self._spawn(self.home, rows=40, cols=120)
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            h.send(b"\x1bOB")  # Down (application-mode SS3 form) → beta
            h.send(b"\r")      # Enter → use and exit
            self.assertEqual(h.wait_exit(timeout=10), 0)
            self.assertIn(b"TUI-EXIT:APPLIED", h.output)
            self.assertIn(b"TUI-USE:APPLIED:Profile applied: beta",
                          h.output)
            marker = self.home / ".omo" / "profiles" / ".active"
            self.assertEqual(marker.read_text().strip(), "beta")
            omo = self.home / ".omo" / "omo.jsonc"
            self.assertIn("provider/beta", omo.read_text())
        finally:
            h.close()

    def test_noop_enter_exits_cleanly(self):
        h = self._spawn(self.home, rows=40, cols=120, mode="noop-seed")
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            h.send(b"\r")  # alpha is active+managed → NOOP exit
            self.assertEqual(h.wait_exit(timeout=10), 0)
            self.assertIn(b"TUI-EXIT:NOOP", h.output)
            self.assertIn(
                b"TUI-USE:NOOP:No change: profile 'alpha' is already "
                b"active", h.output)
        finally:
            h.close()

    # ── delete with confirm ──────────────────────────────────────

    def test_delete_confirm_removes_profile(self):
        h = self._spawn(self.home, rows=40, cols=120)
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            h.send(b"D")
            h.wait_for(b"Delete profile 'alpha'? [y/N]: ", timeout=5)
            h.send(b"y\r")
            h.wait_for(b"Deleted profile: alpha", timeout=5)
            h.send(b"q")
            self.assertEqual(h.wait_exit(timeout=5), 0)
            profiles = self.home / ".omo" / "profiles"
            self.assertFalse((profiles / "alpha.jsonc").exists())
            self.assertTrue((profiles / "alpha.jsonc.BAK").exists())
            self.assertTrue((profiles / "beta.jsonc").exists())
        finally:
            h.close()

    def test_delete_declined_keeps_profile(self):
        h = self._spawn(self.home, rows=40, cols=120)
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            h.send(b"D")
            h.wait_for(b"Delete profile 'alpha'? [y/N]: ", timeout=5)
            h.send(b"n\r")
            h.wait_for(b"Delete cancelled", timeout=5)
            h.send(b"q")
            self.assertEqual(h.wait_exit(timeout=5), 0)
            self.assertTrue(
                (self.home / ".omo" / "profiles" / "alpha.jsonc").exists())
        finally:
            h.close()

    # ── create prompt ────────────────────────────────────────────

    def test_create_prompt_adds_profile(self):
        h = self._spawn(self.home, rows=40, cols=120)
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            h.send(b"n")
            h.wait_for(b"New profile name: ", timeout=5)
            h.send(b"delta\r")
            h.wait_for(b"Profile created: delta", timeout=5)
            h.send(b"q")
            self.assertEqual(h.wait_exit(timeout=5), 0)
            delta = self.home / ".omo" / "profiles" / "delta.jsonc"
            self.assertTrue(delta.exists())
            self.assertIn("[opencode]", delta.read_text())
        finally:
            h.close()

    # ── NARROW + resize ──────────────────────────────────────────

    def test_narrow_tab_switches_panes(self):
        h = self._spawn(self.home, rows=24, cols=80)
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            h.send(b"\t")  # Menu → Details
            h.wait_for(b"Profile: alpha", timeout=5)
            h.send(b"\t")  # back to Menu
            h.wait_for(b"alpha", timeout=5)
            h.send(b"q")
            self.assertEqual(h.wait_exit(timeout=5), 0)
        finally:
            h.close()

    def test_resize_preserves_selection(self):
        h = self._spawn(self.home, rows=24, cols=80)
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            h.send(b"\x1bOB")  # Down → beta (selection to preserve)
            h.resize(rows=40, cols=120)
            h.wait_for(b" Profiles", timeout=5)  # WIDE redraw happened
            h.send(b"\r")
            self.assertEqual(h.wait_exit(timeout=10), 0)
            self.assertIn(b"TUI-USE:APPLIED:Profile applied: beta",
                          h.output)
            marker = self.home / ".omo" / "profiles" / ".active"
            self.assertEqual(marker.read_text().strip(), "beta")
        finally:
            h.close()

    # ── terminal lifecycle (v3 equivalents of the 4 v2 env failures) ──

    def test_quit_restores_terminal(self):
        h = self._spawn(self.home, rows=24, cols=80)
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            h.send(b"q")
            self.assertEqual(h.wait_exit(timeout=5), 0)
            # Post-curses stdout proves the alternate screen was left:
            self.assertIn(b"TUI-EXIT:QUIT", h.output)
        finally:
            h.close()

    def test_too_small_notice(self):
        h = self._spawn(self.home, rows=10, cols=39)
        try:
            h.wait_for(b"too small", timeout=10)
            h.send(b"q")
            self.assertEqual(h.wait_exit(timeout=5), 0)
        finally:
            h.close()

    def test_ctrl_c_cleanup(self):
        h = self._spawn(self.home, rows=24, cols=80)
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            os.kill(h._child_pid, signal.SIGINT)
            self.assertEqual(h.wait_exit(timeout=5), 0)
            self.assertIn(b"TUI-EXIT:QUIT", h.output)
        finally:
            h.close()

    def test_ascii_border_entry(self):
        h = self._spawn(self.home, rows=24, cols=80, mode="ascii-border")
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            h.send(b"q")
            self.assertEqual(h.wait_exit(timeout=5), 0)
        finally:
            h.close()

    def test_injected_failure_restores_terminal(self):
        h = self._spawn(self.home, rows=24, cols=80, mode="fail-apply")
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            h.send(b"\r")  # use_fn raises → FATAL, terminal restored
            self.assertEqual(h.wait_exit(timeout=5), 1)
            self.assertIn(b"TUI-EXIT:FATAL", h.output)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
