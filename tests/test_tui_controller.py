"""Controller tests for the curses TUI driving full flows."""

import unittest
from pathlib import Path
from unittest import mock

from opencode_config_switcher.config import (
    ConfigSummary, FileSummary, ModelSpec, RouteSummary)
from opencode_config_switcher.tui import (
    ApplyResult, ApplyStatus, TuiOutcome, TuiResult, run_tui,
    )


def _cfg(name, **kw):
    defaults = dict(is_current=False, is_valid=True, agents=(),
                    categories=(), raw_text="{}")
    defaults.update(kw)
    return ConfigSummary(
        file=FileSummary(
            path=Path(f"/tmp/{name}"), name=name,
            size_bytes=100, modified_ns=1,
            is_current=defaults["is_current"],
            raw_text=defaults["raw_text"]),
        is_valid=defaults["is_valid"],
        error=defaults.get("error"),
        model_fallback=True,
        agents=defaults["agents"],
        categories=defaults["categories"],
    )


class ControllerIntegrationTests(unittest.TestCase):

    def setUp(self):
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
        p = mock.patch.object(
            tui_mod.curses, "wrapper",
            side_effect=lambda fn: fn(self.mock_stdscr))
        p.start()
        self._patches.append(p)
        self._patches.append(mock.patch("locale.setlocale").start())

    def tearDown(self):
        for p in getattr(self, "_patches", []):
            p.stop()

    def test_wide_mode_navigation(self):
        self.mock_stdscr.getmaxyx.return_value = (40, 120)
        cfgs = [_cfg("a.json"), _cfg("b.json"), _cfg("c.json")]
        import curses
        # Down, Down, Enter
        self.mock_stdscr.getch.side_effect = [
            curses.KEY_DOWN, curses.KEY_DOWN, 10]
        apply_fn = mock.MagicMock(return_value=ApplyResult(
            ApplyStatus.APPLIED, Path("c"), Path("a"), Path("b"), "ok"))
        result = run_tui(cfgs, apply_fn)
        self.assertEqual(result.outcome, TuiOutcome.APPLIED)
        # Should have applied the THIRD config (after 2 down presses)
        applied_source = apply_fn.call_args[0][0]
        self.assertEqual(applied_source.name, "c.json")

    def test_narrow_tab_switch(self):
        self.mock_stdscr.getmaxyx.return_value = (24, 80)
        cfgs = [_cfg("a.json")]
        import curses
        # Tab to Details, then quit
        self.mock_stdscr.getch.side_effect = [
            ord("\t"), ord("q")]
        result = run_tui(cfgs, mock.MagicMock())
        self.assertEqual(result.outcome, TuiOutcome.QUIT)

    def test_signal_termination(self):
        cfgs = [_cfg("a.json")]
        self.mock_stdscr.getch.side_effect = [ord("q")]
        result = run_tui(cfgs, mock.MagicMock())
        self.assertEqual(result.outcome, TuiOutcome.QUIT)


if __name__ == "__main__":
    unittest.main()
