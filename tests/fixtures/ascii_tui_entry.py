"""ASCII-border probe: forces ACS capability failure before launching TUI.

Used by PTY tests to verify the curses renderer draws `+ - |` borders
when ACS characters are unavailable.
"""

import curses
import opencode_config_switcher.tui as tui_mod

# Simulate ACS failure: replace _draw_acs_border with the ASCII fallback
_original_draw_acs = tui_mod._draw_acs_border


def _always_ascii(win):
    tui_mod._draw_ascii_border(win)


tui_mod._draw_acs_border = _always_ascii

from opencode_config_switcher.cli import main

raise SystemExit(main())
