"""Fault-injection probe: replaces the renderer to raise an exception.

Used by PTY tests to verify the TUI catches exceptions and restores
the terminal instead of crashing.
"""

import opencode_config_switcher.tui as tui_mod

_original_run_tui = tui_mod.run_tui


def _failing_run_tui(*args, **kwargs):
    raise RuntimeError("injected renderer failure")


tui_mod.run_tui = _failing_run_tui

from opencode_config_switcher.cli import main

raise SystemExit(main())
