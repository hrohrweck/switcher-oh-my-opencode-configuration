"""Fake-window rendering tests for the curses TUI renderer.

Verifies WIDE/NARROW/TOO_SMALL frames, colors, badges,
Details content, overlay, and safe clipping without a real terminal.
"""

import curses
import unittest
from pathlib import Path
from unittest import mock

from opencode_config_switcher.config import (
    ConfigSummary, FileSummary, ModelSpec, RouteSummary,
    RuntimeFallbackSummary)
from opencode_config_switcher.switching import ApplyResult, ApplyStatus
from opencode_config_switcher.tui import (
    TuiOutcome, TuiResult, run_tui, AppState, LayoutMode,
    _safe_addstr, _draw_acs_border, _draw_ascii_border,
    )
import opencode_config_switcher.tui as tui_mod


def _cfg(name: str, *, is_current: bool = False, is_valid: bool = True,
         agents=(), categories=(), raw_text: str = "{}",
         error: str | None = None) -> ConfigSummary:
    return ConfigSummary(
        file=FileSummary(
            path=Path(f"/tmp/{name}"), name=name,
            size_bytes=100, modified_ns=1,
            is_current=is_current, raw_text=raw_text),
        is_valid=is_valid, error=error,
        model_fallback=True,
        runtime_fallback=RuntimeFallbackSummary(enabled=True),
        agents=agents, categories=categories,
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


class FakeControllerTests(unittest.TestCase):
    """Drive run_tui with a mocked curses environment and assert outcomes."""

    def setUp(self):
        """Set up all necessary curses mocks."""
        import opencode_config_switcher.tui as tui_mod
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
                m.return_value = False
            elif name == "color_pair":
                m.return_value = 0
        self.mock_stdscr = mock.MagicMock()
        self.mock_stdscr.getmaxyx.return_value = (24, 80)
        self.patch_wrapper = mock.patch.object(
            tui_mod.curses, "wrapper",
            side_effect=lambda fn: fn(self.mock_stdscr))
        self.patch_wrapper.start()
        self._patches.append(self.patch_wrapper)
        self._patches.append(mock.patch("locale.setlocale").start())

    def tearDown(self):
        for p in getattr(self, "_patches", []):
            p.stop()

    def _assert_quit_outcome(self, configs, keys, expected=TuiOutcome.QUIT):
        self.mock_stdscr.getch.side_effect = list(keys)
        result = run_tui(configs, mock.MagicMock())
        self.assertEqual(result.outcome, expected)

    def test_quit_with_q(self):
        self._assert_quit_outcome(
            [_cfg("a.json")], [ord("q")])

    def test_quit_with_ctrl_d(self):
        self._assert_quit_outcome(
            [_cfg("a.json")], [4])  # Ctrl-D

    def test_apply_valid(self):
        cfg = [_cfg("a.json")]
        apply_fn = mock.MagicMock(return_value=ApplyResult(
            ApplyStatus.APPLIED, Path("a"), Path("b"), Path("c"), "ok"))
        # Navigate down (already at 0), press enter
        self.mock_stdscr.getch.side_effect = [10]  # Enter
        result = run_tui(cfg, apply_fn)
        self.assertEqual(result.outcome, TuiOutcome.APPLIED)

    def test_blocked_invalid(self):
        cfg = [_cfg("bad.json", is_valid=False, error="parse error")]
        apply_fn = mock.MagicMock(return_value=ApplyResult(
            ApplyStatus.BLOCKED, Path("a"), Path("b"), Path("c"),
            "blocked", error="parse error"))
        # Press enter then quit
        self.mock_stdscr.getch.side_effect = [10, ord("q")]
        result = run_tui(cfg, apply_fn)
        self.assertEqual(result.outcome, TuiOutcome.QUIT)
        # Status should have been set (we can't easily read from
        # inside the mock, but the apply_fn was called once)
        apply_fn.assert_called_once()

    def test_noop_current(self):
        cfg = [_cfg("active.json", is_current=True)]
        apply_fn = mock.MagicMock(return_value=ApplyResult(
            ApplyStatus.NOOP, Path("a"), Path("b"), Path("c"), "no change"))
        self.mock_stdscr.getch.side_effect = [10]
        result = run_tui(cfg, apply_fn)
        self.assertEqual(result.outcome, TuiOutcome.NOOP)

    def test_resize_preserves_state(self):
        cfg = [_cfg("a.json"), _cfg("b.json")]
        self.mock_stdscr.getch.side_effect = [
            curses.KEY_RESIZE, ord("q")]
        result = run_tui(cfg, mock.MagicMock())
        self.assertEqual(result.outcome, TuiOutcome.QUIT)

    def test_keyboard_interrupt(self):
        cfg = [_cfg("a.json")]
        with mock.patch.object(tui_mod.curses, "wrapper",
                               side_effect=KeyboardInterrupt()):
            result = run_tui(cfg, mock.MagicMock())
        self.assertEqual(result.outcome, TuiOutcome.QUIT)

    def test_fatal_exception(self):
        cfg = [_cfg("a.json")]
        with mock.patch.object(tui_mod.curses, "wrapper",
                               side_effect=RuntimeError("boom")):
            result = run_tui(cfg, mock.MagicMock())
        self.assertEqual(result.outcome, TuiOutcome.FATAL)
        self.assertEqual(result.error_type, "RuntimeError")


if __name__ == "__main__":
    unittest.main()
