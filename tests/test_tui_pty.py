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
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from opencode_config_switcher.engine import render_document
from opencode_config_switcher.omoconfig import (
    OMO_SCHEMA_URL,
    load_omo_document,
)
from opencode_config_switcher.paths import Paths, project_omo_paths
from opencode_config_switcher.profiles import (
    create_profile,
    read_profile,
)
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
            h.send(b"\r")      # Enter → scope prompt
            h.wait_for(b"Apply locally (l) or globally (g)? ",
                       timeout=10)
            h.send(b"g\r")     # global → use and exit
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
            h.send(b"\r")  # Enter → scope prompt
            h.wait_for(b"Apply locally (l) or globally (g)? ",
                       timeout=10)
            h.send(b"g\r")  # alpha is active+managed → NOOP exit
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
            # " Profiles" is the WIDE-only header; the selector's
            # 250ms poll resyncs even a dropped SIGWINCH, so the WIDE
            # frame lands within a second under load (timeout=10 for
            # headroom).
            h.wait_for(b" Profiles", timeout=10)
            h.send(b"\r")  # Enter → scope prompt
            h.wait_for(b"Apply locally (l) or globally (g)? ",
                       timeout=10)
            h.send(b"g\r")  # global → use and exit
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
            h.send(b"\r")  # Enter → scope prompt
            h.wait_for(b"Apply locally (l) or globally (g)? ",
                       timeout=10)
            h.send(b"g\r")  # use_fn raises → FATAL, terminal restored
            self.assertEqual(h.wait_exit(timeout=5), 1)
            self.assertIn(b"TUI-EXIT:FATAL", h.output)
        finally:
            h.close()


def _scoped_profile_doc() -> dict:
    """One valid profile document (same shape as the fixture seed)."""
    return {
        "$schema": OMO_SCHEMA_URL,
        "[opencode]": {
            "agents": {
                "build": {"model": "provider/alpha",
                          "fallback_models": ["provider/fallback"]},
            },
        },
    }


class ScopedApplyPtyTests(unittest.TestCase):
    """Scoped-apply pins over ``python -m opencode_config_switcher``.

    Unlike ``ProfileTuiPtyTests`` (selector-only through the fixture
    entry), these scenarios drive the REAL CLI: the bare selector on a
    PTY (TTY path with the curses scope prompt) and the plain selector
    over a real pipe (non-TTY path, always GLOBAL).  Each test owns a
    throwaway HOME holding one profile (``alpha``) AND a throwaway
    project cwd — PTY children inherit this process's cwd, so ``_chdir``
    moves into the project before spawning (the harness ``cwd=``
    parameter is accepted but never applied).
    """

    def setUp(self):
        home_tmp = tempfile.TemporaryDirectory(prefix="test-scope-home-")
        cwd_tmp = tempfile.TemporaryDirectory(prefix="test-scope-cwd-")
        self.addCleanup(cwd_tmp.cleanup)
        self.addCleanup(home_tmp.cleanup)
        self.home = Path(home_tmp.name)
        self.cwd = Path(cwd_tmp.name)
        self.paths = Paths.build(self.home)
        create_profile(self.paths, "alpha", _scoped_profile_doc())
        self.alpha = read_profile(self.paths, "alpha")
        # Fresh-render expectation: at setUp time ~/.omo/omo.jsonc is
        # absent, so this is render_document(profile, LoadError).
        self.expected_global = render_document(
            self.alpha.document, load_omo_document(self.paths.omo_path))

    # ── helpers ─────────────────────────────────────────────────

    def _chdir(self):
        previous = os.getcwd()
        os.chdir(self.cwd)
        self.addCleanup(os.chdir, previous)

    def _spawn_cli(self, *, rows: int = 24, cols: int = 80) -> PtyHarness:
        env = {
            "HOME": str(self.home),
            "PYTHONPATH": str(ROOT / "src"),
            "TERM": "xterm-256color",
        }
        return PtyHarness(
            [sys.executable, "-m", "opencode_config_switcher"],
            rows=rows, cols=cols, env=env)

    @staticmethod
    def _wait_for_from(h: PtyHarness, marker: bytes, start: int,
                       timeout: float = 10.0) -> None:
        """wait_for, but only occurrences AFTER byte offset ``start``.

        PTY output accumulates for the child's whole life and curses
        emits only frame DIFFS, so a marker re-appearing beyond a prior
        offset proves a genuinely new frame (a re-opened prompt, a new
        footer status), not a repaint of the old one.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            h.read_available(timeout=0.5)
            if h.output.find(marker, start) >= 0:
                return
        raise TimeoutError(
            f"Marker {marker!r} not found after offset {start} within "
            f"{timeout}s. Child exit status: {h.exit_status}. "
            f"Tail: {h.output[start:start + 3000]!r}")

    @staticmethod
    def _snapshot(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def _assert_unchanged(self, path: Path, before: bytes | None,
                          label: str) -> None:
        if before is None:
            self.assertFalse(path.exists(), f"{label} must stay absent")
        else:
            self.assertEqual(path.read_bytes(), before,
                             f"{label} must stay byte-unchanged")

    def _open_scope_prompt(self, h: PtyHarness) -> None:
        """Enter on the selected profile → scope prompt footer."""
        h.send(b"\r")
        h.wait_for(b"Apply locally (l) or globally (g)? ", timeout=10)

    # ── (a) TUI LOCAL flow ──────────────────────────────────────

    def test_tui_local_apply_writes_project_file_only(self):
        # Stale-state probe: a PRE-EXISTING local omo.jsonc whose extra
        # keys ('[codex]', '_migrations', seeded $schema) must survive
        # the LOCAL render (merge over the LOCAL file, not replace).
        local_omo, local_bak = project_omo_paths(self.cwd)
        local_omo.parent.mkdir(parents=True, exist_ok=True)
        seeded = ('{"[codex]": {"keep": true}, '
                  '"_migrations": [7], '
                  '"$schema": "https://example.com/seeded"}')
        local_omo.write_text(seeded, encoding="utf-8")
        expected_local = render_document(
            self.alpha.document, load_omo_document(local_omo))
        # Orphan marker: LOCAL applies must never rewrite .active.
        self.paths.active_marker.write_text("gamma\n", encoding="utf-8")
        active_before = self._snapshot(self.paths.active_marker)
        global_before = self._snapshot(self.paths.omo_path)

        self._chdir()
        h = self._spawn_cli(rows=40, cols=120)
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            self._open_scope_prompt(h)
            # Cancel/resume probe: Esc must cancel the prompt cleanly
            # and keep the selector alive, because Enter then RE-OPENS
            # the prompt (had Esc failed, Enter would submit the empty
            # buffer as GLOBAL and the child would exit instead).
            resume_at = len(h.output)
            h.send(b"\x1b")  # Esc cancels the scope prompt
            h.send(b"\r")    # Enter re-opens it
            self._wait_for_from(
                h, b"Apply locally (l) or globally (g)? ", resume_at)
            h.send(b"l\r")   # LOCAL apply → TUI exits
            self.assertEqual(h.wait_exit(timeout=10), 0)
        finally:
            h.close()

        out = h.output
        self.assertIn(b"Profile applied: alpha", out)
        self.assertIn(f"Applied to: {local_omo.resolve()}".encode(), out)
        # the seeded local file existed, so a local .BAK was written
        self.assertIn(f"Backup saved to: {local_bak.resolve()}".encode(),
                      out)

        self.assertEqual(load_omo_document(local_omo).raw, expected_local)
        self.assertEqual(local_bak.read_bytes(), seeded.encode())
        self._assert_unchanged(self.paths.omo_path, global_before,
                               "~/.omo/omo.jsonc")
        self._assert_unchanged(self.paths.active_marker, active_before,
                               "~/.omo/profiles/.active")

    # ── (b) TUI GLOBAL flow ─────────────────────────────────────

    def test_tui_global_apply_writes_home_file_and_marker(self):
        self._chdir()
        h = self._spawn_cli(rows=40, cols=120)
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            self._open_scope_prompt(h)
            h.send(b"g\r")   # GLOBAL apply → TUI exits
            self.assertEqual(h.wait_exit(timeout=10), 0)
        finally:
            h.close()

        out = h.output
        self.assertIn(b"Profile applied: alpha", out)
        self.assertIn(f"Applied to: {self.paths.omo_path}".encode(), out)
        # fresh global apply: no previous ~/.omo/omo.jsonc → no .BAK
        # → the backup line is truthfully absent
        self.assertNotIn(b"Backup saved to:", out)

        self.assertEqual(load_omo_document(self.paths.omo_path).raw,
                         self.expected_global)
        self.assertEqual(self.paths.active_marker.read_bytes(), b"alpha\n")
        self.assertFalse((self.cwd / ".omo").exists())

    # ── invalid scope + Esc cancel ──────────────────────────────

    def test_tui_invalid_scope_then_esc_writes_nothing(self):
        self._chdir()
        h = self._spawn_cli(rows=40, cols=120)
        try:
            h.wait_for(b"OpenCode Configuration Switcher", timeout=10)
            self._open_scope_prompt(h)
            hint_at = len(h.output)
            h.send(b"x\r")   # invalid scope → footer hint, prompt stays
            self._wait_for_from(
                h, b"Apply locally (l) or globally (g)", hint_at)
            h.send(b"\x1b")  # Esc cancels the still-open prompt
            h.send(b"q")     # selector alive → clean quit
            self.assertEqual(h.wait_exit(timeout=10), 0)
        finally:
            h.close()

        self.assertIn(b"Exiting without changes", h.output)
        self.assertNotIn(b"Profile applied", h.output)
        self.assertFalse((self.cwd / ".omo").exists())
        self.assertFalse(self.paths.omo_path.exists())
        self.assertFalse(self.paths.omo_backup.exists())
        self.assertFalse(self.paths.active_marker.exists())

    # ── (c) piped plain selector (non-TTY) ───────────────────────

    def test_piped_plain_selector_applies_globally(self):
        env = os.environ.copy()
        env.update({
            "HOME": str(self.home),
            "PYTHONPATH": str(ROOT / "src"),
        })
        proc = subprocess.run(
            [sys.executable, "-m", "opencode_config_switcher"],
            input=b"1\n", cwd=self.cwd, env=env,
            capture_output=True, timeout=60)

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"Available profiles:", proc.stdout)
        self.assertIn(b"1) alpha", proc.stdout)
        self.assertIn(b"Profile applied: alpha", proc.stdout)
        self.assertIn(f"Applied to: {self.paths.omo_path}".encode(),
                      proc.stdout)
        self.assertNotIn(b"\x1b", proc.stdout)  # no ANSI escapes
        self.assertNotIn(b"Backup saved to:", proc.stdout)
        # Misleading-success probe: run inside a project cwd, the
        # success lines still reference the GLOBAL target and only the
        # global files moved.
        self.assertEqual(load_omo_document(self.paths.omo_path).raw,
                         self.expected_global)
        self.assertEqual(self.paths.active_marker.read_bytes(), b"alpha\n")
        self.assertFalse((self.cwd / ".omo").exists())


if __name__ == "__main__":
    unittest.main()
