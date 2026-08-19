"""Fake-window rendering tests for the v3 curses profile selector.

Verifies WIDE/NARROW/TOO_SMALL frames, badge colors (ACTIVE green,
CUSTOM yellow, INVALID red+bold, selection A_REVERSE), details/overlay
content, safe clipping, and the EDITOR_AVAILABLE=False footer — all
against a mocked curses window (no real terminal).
"""

import curses
import unittest
from pathlib import Path
from unittest import mock

from opencode_config_switcher.omoconfig import OmoDocument
from opencode_config_switcher.profiles import ProfileRecord
from opencode_config_switcher.tui import (
    TuiOutcome, _draw_ascii_border, _safe_addstr, run_profile_tui,
    )
from opencode_config_switcher.tui_data import ProfileSummary
import opencode_config_switcher.tui as tui_mod

SCHEMA = "https://example.invalid/omo.schema.json"


def _summary(name, *, active=False, drift="unmanaged", invalid=False,
             agents=(), categories=(), raw_text='{"k": 1}',
             document="default"):
    """Hand-build a ProfileSummary; no disk, no I/O."""
    if document == "default" and not invalid:
        document = OmoDocument(raw={
            "$schema": SCHEMA, "[opencode]": {"agents": {}}})
    return ProfileSummary(
        record=ProfileRecord(
            name=name, path=Path(f"/tmp/{name}.jsonc"),
            document=document, is_valid=not invalid,
            error="Invalid JSONC at line 3: boom" if invalid else None,
            size_bytes=100, modified_ns=1, raw_text=raw_text),
        is_active=active, drift=drift,
        agents=agents, categories=categories, section_warnings=(),
    )


class SafeAddstrTests(unittest.TestCase):
    """Test clipping behaviour of _safe_addstr on a mock window."""

    def setUp(self):
        self.win = mock.MagicMock()
        self.win.getmaxyx.return_value = (24, 80)

    def test_normal_write(self):
        _safe_addstr(self.win, 5, 3, "hello")
        self.win.addstr.assert_called_with(5, 3, "hello", 0)

    def test_out_of_bounds_y(self):
        _safe_addstr(self.win, 30, 0, "hello")
        self.win.addstr.assert_not_called()

    def test_bottom_right_cell_avoided(self):
        self.win.getmaxyx.return_value = (24, 80)
        _safe_addstr(self.win, 23, 75, "hello world")
        # Should be truncated to avoid writing to (23, 79)
        called_text = self.win.addstr.call_args[0][2]
        self.assertLessEqual(len(called_text), 4)


class BorderTests(unittest.TestCase):
    def test_ascii_border_writes_plus_dash_pipe(self):
        win = mock.MagicMock()
        win.getmaxyx.return_value = (5, 10)
        _draw_ascii_border(win)
        # Should write +------+ on first row
        calls = [c[0] for c in win.addstr.call_args_list]
        self.assertIn("+", calls[0][2])


class FakeWindowTestCase(unittest.TestCase):
    """Mocked curses environment with colors ON (pair n -> attr n)."""

    def setUp(self):
        self._patches = []
        to_mock = [
            "curs_set", "has_colors", "start_color", "use_default_colors",
            "init_pair", "color_pair", "update_lines_cols",
        ]
        for name in to_mock:
            p = mock.patch.object(tui_mod.curses, name)
            m = p.start()
            self._patches.append(p)
            if name == "has_colors":
                m.return_value = True
            elif name == "color_pair":
                m.side_effect = lambda pair: pair  # identity: green=2 …
        self.stdscr = mock.MagicMock()
        self.stdscr.getmaxyx.return_value = (40, 120)
        p = mock.patch.object(
            tui_mod.curses, "wrapper",
            side_effect=lambda fn: fn(self.stdscr))
        p.start()
        self._patches.append(p)
        self._patches.append(mock.patch("locale.setlocale").start())
        self.services = tui_mod.SelectorServices(
            use_fn=mock.MagicMock(),
            create_fn=mock.MagicMock(),
            delete_fn=mock.MagicMock(),
            refresh_fn=mock.MagicMock(return_value=[]),
        )

    def tearDown(self):
        for p in getattr(self, "_patches", []):
            p.stop()

    # ── helpers ────────────────────────────────────────────────────

    def _run(self, summaries, keys):
        from opencode_config_switcher.paths import Paths
        self.stdscr.getch.side_effect = list(keys)
        return run_profile_tui(
            summaries, Paths.build(Path("/tmp/fake-home")), self.services)

    def _calls(self):
        """[(y, x, text, attr)] for every addstr the frame made."""
        return [c.args for c in self.stdscr.addstr.call_args_list]

    def _find(self, needle):
        """All calls whose text contains *needle*."""
        return [c for c in self._calls() if needle in c[2]]

    def _footer_calls(self):
        return self._find("q: quit") + self._find("q/Ctrl-C quit")


class WideFrameTests(FakeWindowTestCase):

    def test_startup_renders_header_menu_and_details(self):
        result = self._run(
            [_summary("alpha", active=True, drift="managed"),
             _summary("beta")],
            [ord("q")])
        self.assertEqual(result.outcome, TuiOutcome.QUIT)
        self.assertTrue(self._find("OpenCode Configuration Switcher"))
        self.assertTrue(self._find("alpha"), "menu shows alpha")
        self.assertTrue(self._find("beta"), "menu shows beta")
        self.assertTrue(self._find("[ACTIVE]"), "ACTIVE badge rendered")
        self.assertTrue(self._find("Profile: alpha"),
                        "details pane shows the selected profile")
        self.assertTrue(self._find("State: active"))

    def test_badge_colors(self):
        # First row is a plain profile: selection (reverse) stays off the
        # badge rows under test.
        self._run(
            [_summary("plain"),
             _summary("good", active=True, drift="managed"),
             _summary("drifted", active=True, drift="drifted"),
             _summary("bad", invalid=True)],
            [ord("q")])
        active = self._find("[ACTIVE]")
        drifted = self._find("[CUSTOM]")
        invalid = self._find("[INVALID]")
        self.assertTrue(active and drifted and invalid)
        self.assertEqual(active[0][3], 2)  # green pair 2
        self.assertEqual(drifted[0][3], 4)  # yellow pair 4
        self.assertEqual(invalid[0][3], 3 | curses.A_BOLD)  # red + bold

    def test_selected_row_is_reversed_plain_rows_normal(self):
        self._run([_summary("alpha"), _summary("beta")], [ord("q")])
        alpha = self._find("alpha")
        beta = self._find("beta")
        # Selected (alpha) renders reversed; beta (unselected) normal.
        self.assertEqual(alpha[0][3], curses.A_REVERSE)
        self.assertEqual(beta[0][3], 0)

    def test_footer_hides_editor_keys(self):
        import opencode_config_switcher.tui as tui_mod
        with mock.patch.object(tui_mod, "EDITOR_AVAILABLE", False):
            self._run([_summary("alpha")], [ord("q")])
            footers = self._footer_calls()
            self.assertTrue(footers, "a footer line was drawn")
            for _, _, text, _ in footers:
                self.assertNotIn("e: edit", text)
                self.assertNotIn("i: import", text)
                self.assertNotIn("r: replace", text)
                self.assertIn("q: quit", text)

    def test_overlay_draws_cached_raw_text(self):
        raw = '{"alpha-raw-marker": true}'
        result = self._run([_summary("alpha", raw_text=raw)],
                           [ord("d"), ord("q")])
        self.assertEqual(result.outcome, TuiOutcome.QUIT)
        self.assertTrue(self._find("alpha-raw-marker"),
                        "raw overlay content was drawn from raw_text")

    def test_resize_during_overlay_keeps_overlay(self):
        raw = '{"overlay-persist": 1}'
        self._run([_summary("alpha", raw_text=raw)],
                  [ord("d"), curses.KEY_RESIZE, ord("q")])
        # Two redraws after the toggle (overlay frame + post-resize frame).
        self.assertGreaterEqual(len(self._find("overlay-persist")), 2)

    def test_cjk_profile_name_renders(self):
        self._run([_summary("配置文件")], [ord("q")])
        self.assertTrue(self._find("配置文件"), "CJK name rendered")
        # Everything written stayed inside the window bounds.
        for _, x, text, _ in self._calls():
            self.assertLess(x + tui_mod.display_width(text), 120)

    def test_empty_list_renders_sane_frame(self):
        result = self._run([], [ord("q")])
        self.assertEqual(result.outcome, TuiOutcome.QUIT)
        self.assertTrue(self._find("OpenCode Configuration Switcher"))
        self.assertFalse(self._find("[ACTIVE]"))


class NarrowFrameTests(FakeWindowTestCase):

    def test_narrow_menu_frame(self):
        self.stdscr.getmaxyx.return_value = (24, 80)
        self._run([_summary("alpha")],
                  [ord("\t"), ord("q")])  # Tab to details, quit
        self.assertTrue(self._find("alpha"))
        self.assertTrue(self._find("Details"))

    def test_too_small_frame(self):
        self.stdscr.getmaxyx.return_value = (10, 39)
        result = self._run([_summary("alpha")], [ord("q")])
        self.assertEqual(result.outcome, TuiOutcome.QUIT)
        self.assertTrue(self._find("too small"))
        self.assertTrue(self._find("q/Ctrl-C quit"))


if __name__ == "__main__":
    unittest.main()
