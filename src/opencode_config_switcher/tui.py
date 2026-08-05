"""Pure TUI layout, state transitions, and structured formatting.

Also provides the curses renderer shell and typed result contracts.
"""

import curses
import json
import locale
import os
import signal
import unicodedata
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

from opencode_config_switcher.config import ConfigSummary, ModelSpec
from opencode_config_switcher.switching import ApplyResult, ApplyStatus


# ── display-width helpers ──────────────────────────────────────────

def display_width(text: str) -> int:
    """Compute the display width of *text*, handling CJK and combining marks."""
    w = 0
    for ch in text:
        if unicodedata.combining(ch) != 0:
            continue
        ea = unicodedata.east_asian_width(ch)
        w += 2 if ea in ("W", "F") else 1
    return w


def truncate_display(text: str, max_width: int,
                     indicator: str = "…") -> str:
    """Truncate *text* so its display width is at most *max_width*."""
    if max_width <= 0:
        return ""
    ind_w = display_width(indicator)
    if display_width(text) <= max_width:
        return text

    result: list[str] = []
    cur_w = 0
    limit = max_width - ind_w
    for ch in text:
        if unicodedata.combining(ch) != 0:
            result.append(ch)
            continue
        ch_w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cur_w + ch_w > limit:
            break
        result.append(ch)
        cur_w += ch_w
    return "".join(result) + indicator


# ── layout modes ───────────────────────────────────────────────────

class LayoutMode(Enum):
    TOO_SMALL = auto()
    NARROW = auto()
    WIDE = auto()


HEADER_ROWS = 3
FOOTER_ROWS = 2
AVAILABLE_ROWS = 12  # minimum rows for content


def compute_layout(cols: int, rows: int) -> LayoutMode:
    """Return the layout mode for the given terminal dimensions."""
    if cols < 40 or rows < 12:
        return LayoutMode.TOO_SMALL
    if cols >= 100 and rows >= 18:
        return LayoutMode.WIDE
    return LayoutMode.NARROW


def left_width(cols: int) -> int:
    """Compute the left pane width in WIDE mode."""
    return min(44, max(30, cols // 3))


class NarrowPane(Enum):
    MENU = auto()
    DETAILS = auto()


# ── app state ──────────────────────────────────────────────────────

@dataclass
class AppState:
    """Mutable TUI application state."""
    selected_idx: int = 0
    config_count: int = 0
    menu_offset: int = 0
    detail_offset: int = 0
    narrow_pane: NarrowPane = NarrowPane.MENU
    overlay_open: bool = False
    overlay_offset: int = 0
    status: str | None = None
    layout: LayoutMode = LayoutMode.NARROW

    def clamp(self) -> None:
        """Clamp all indices after config change or resize."""
        if self.config_count == 0:
            self.selected_idx = 0
            self.menu_offset = 0
            self.detail_offset = 0
            return
        self.selected_idx = max(0, min(
            self.selected_idx, self.config_count - 1))
        # Menu scroll: keep selection visible
        menu_height = AVAILABLE_ROWS - HEADER_ROWS - FOOTER_ROWS - 2
        # Actually the available content rows depend on mode...
        # Use a simple clamp for menu
        max_menu_off = max(0, self.config_count - 1)
        self.menu_offset = max(0, min(
            self.menu_offset, max_menu_off))


# ── key transitions (pure) ─────────────────────────────────────────

def handle_key(state: AppState, key: str) -> str | None:
    """Apply *key* to *state* and return an intent string or None.

    Valid intents: 'quit', 'apply'.
    """
    lm = state.layout

    # Overlay mode — check BEFORE universal quit keys (exclude TOO_SMALL)
    if state.overlay_open and lm != LayoutMode.TOO_SMALL:
        if key in ("q", "d"):
            state.overlay_open = False
            state.overlay_offset = 0
            return None
        if key == "up":
            state.overlay_offset = max(0, state.overlay_offset - 1)
        elif key == "down":
            state.overlay_offset += 1
        elif key == "pageup":
            state.overlay_offset = max(0, state.overlay_offset - 10)
        elif key == "pagedown":
            state.overlay_offset += 10
        return None

    # Universal quit keys
    if key in ("q", "ctrlc", "ctrld"):
        return "quit"

    # TOO_SMALL: only quit allowed (already handled above), everything else ignored
    if lm == LayoutMode.TOO_SMALL:
        return None

    # WIDE mode — root
    if lm == LayoutMode.WIDE:
        if key == "up":
            state.selected_idx = max(0, state.selected_idx - 1)
            state.detail_offset = 0  # reset on selection change
        elif key == "down":
            state.selected_idx = min(
                state.config_count - 1, state.selected_idx + 1)
            state.detail_offset = 0
        elif key == "pageup":
            state.detail_offset = max(0, state.detail_offset - 5)
        elif key == "pagedown":
            state.detail_offset += 5
        elif key == "d":
            state.overlay_open = True
            state.overlay_offset = 0
        elif key == "enter":
            return "apply"
        elif key == "tab":
            pass  # no-op in WIDE
        return None

    # NARROW mode — root
    if state.narrow_pane == NarrowPane.MENU:
        if key == "up":
            state.selected_idx = max(0, state.selected_idx - 1)
            state.detail_offset = 0
        elif key == "down":
            state.selected_idx = min(
                state.config_count - 1, state.selected_idx + 1)
            state.detail_offset = 0
        elif key == "tab":
            state.narrow_pane = NarrowPane.DETAILS
        elif key == "d":
            state.overlay_open = True
            state.overlay_offset = 0
        elif key == "enter":
            return "apply"
        elif key in ("pageup", "pagedown"):
            pass
    else:  # NARROW DETAILS
        if key == "up":
            state.detail_offset = max(0, state.detail_offset - 1)
        elif key == "down":
            state.detail_offset += 1
        elif key == "pageup":
            state.detail_offset = max(0, state.detail_offset - 5)
        elif key == "pagedown":
            state.detail_offset += 5
        elif key == "tab":
            state.narrow_pane = NarrowPane.MENU
        elif key == "d":
            state.overlay_open = True
            state.overlay_offset = 0
        elif key == "enter":
            return "apply"

    return None


# ── formatting ────────────────────────────────────────────────────

def _format_model(m: ModelSpec) -> str:
    parts = [m.model or "not configured"]
    if m.variant:
        parts.append(f"[{m.variant}]")
    elif m.reasoning:
        parts.append(f"(reasoning: {m.reasoning})")
    elif m.reasoning_effort:
        parts.append(f"(effort: {m.reasoning_effort})")
    return " ".join(parts)


def format_details(summary: ConfigSummary, width: int) -> list[str]:
    """Format a ConfigSummary into display lines for the details pane."""
    lines: list[str] = []
    w = max(width, 1)
    f = summary.file

    # File section
    lines.append(f"File: {truncate_display(str(f.path), w - 6)}")
    lines.append(f"Name: {f.name}")
    status_str = "CURRENT " if f.is_current else ""
    if summary.is_valid:
        status_str += "VALID"
    else:
        status_str += "INVALID"
    lines.append(f"Status: {status_str}")
    if f.size_bytes is not None:
        lines.append(f"Size: {f.size_bytes} bytes")
    if summary.error:
        lines.append(f"Error: {summary.error}")

    # Schema / fallback policy
    if summary.schema:
        lines.append(f"Schema: {truncate_display(summary.schema, w - 8)}")
    lines.append(f"Model Fallback: {summary.model_fallback}")

    # Runtime fallback
    rf = summary.runtime_fallback
    lines.append(f"Runtime Fallback: enabled={rf.enabled}")
    if rf.retry_on_errors:
        lines.append(f"  Retry on: {rf.retry_on_errors}")
    if rf.max_fallback_attempts is not None:
        lines.append(f"  Max attempts: {rf.max_fallback_attempts}")
    if rf.cooldown_seconds is not None:
        lines.append(f"  Cooldown: {rf.cooldown_seconds}s")
    for key, val in rf.additional:
        lines.append(f"  {key}: {val}")

    # Additional settings
    if summary.additional_settings:
        lines.append("Additional Settings:")
        for key, val in summary.additional_settings:
            if isinstance(val, (str, int, float, bool, type(None))):
                lines.append(f"  {key}: {val!r}")
            elif isinstance(val, list):
                lines.append(f"  {key}: [list, {len(val)} items]")
            elif isinstance(val, dict):
                lines.append(f"  {key}: {{object, {len(val)} keys}}")
            else:
                lines.append(f"  {key}: {type(val).__name__}")

    # Agents
    if not summary.agents and not summary.categories:
        lines.append("")
        lines.append("Agents: none configured")
        lines.append("Categories: none configured")
    else:
        if summary.agents:
            lines.append(f"Agents ({len(summary.agents)}):")
            for route in summary.agents:
                lines.append(f"  {route.name}: {_format_model(route.primary)}")
                for i, fb in enumerate(route.fallbacks, 1):
                    lines.append(f"    [{i}] {_format_model(fb)}")
                for warn in route.warnings:
                    lines.append(f"    ! {warn}")

        if summary.categories:
            lines.append(f"Categories ({len(summary.categories)}):")
            for route in summary.categories:
                lines.append(f"  {route.name}: {_format_model(route.primary)}")
                for i, fb in enumerate(route.fallbacks, 1):
                    lines.append(f"    [{i}] {_format_model(fb)}")
                for warn in route.warnings:
                    lines.append(f"    ! {warn}")

    # Global warnings
    for warn_msg in summary.warnings:
        lines.append(f"! {warn_msg}")

    # Truncate each line to width
    return [truncate_display(line, w) for line in lines]


def format_overlay(raw_text: str, width: int) -> list[str]:
    """Format raw JSON text for the overlay pane (vertical scroll only)."""
    lines = raw_text.splitlines()
    return [truncate_display(line, width) for line in lines]


# ── TUI result contracts ──────────────────────────────────────────

class TuiOutcome(str, Enum):
    QUIT = "QUIT"
    APPLIED = "APPLIED"
    NOOP = "NOOP"
    TERMINATED = "TERMINATED"
    FATAL = "FATAL"


@dataclass(frozen=True)
class TuiResult:
    outcome: TuiOutcome
    apply_result: ApplyResult | None = None
    error_type: str | None = None
    error_message: str | None = None
    signal_number: int | None = None


# ── curses renderer ───────────────────────────────────────────────

_ApplyFn = Callable[[object, object, object], ApplyResult]


def _safe_addstr(win, y: int, x: int, text: str, attr: int = 0) -> None:
    """Write *text* at (y, x) without triggering curses.error on overflow."""
    max_y, max_x = win.getmaxyx()
    if y < 0 or y >= max_y or x >= max_x:
        return
    avail = max_x - x
    display = truncate_display(text, avail)
    if y == max_y - 1 and x + display_width(display) >= max_x:
        display = truncate_display(text, avail - 1)
    try:
        win.addstr(y, x, display, attr)
    except curses.error:
        pass


def _init_colors() -> None:
    """Initialize color pairs; degrade gracefully when unavailable."""
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
    except curses.error:
        pass
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_YELLOW, -1)


def _color_attrs() -> dict[str, int]:
    """Return attribute dict, honoring NO_COLOR."""
    no_color = os.environ.get("NO_COLOR") is not None
    if no_color or not curses.has_colors():
        return {
            "cyan": curses.A_BOLD,
            "green": curses.A_BOLD,
            "red": curses.A_BOLD,
            "yellow": curses.A_BOLD,
            "bold": curses.A_BOLD,
            "reverse": curses.A_REVERSE,
            "normal": 0,
        }
    return {
        "cyan": curses.color_pair(1),
        "green": curses.color_pair(2),
        "red": curses.color_pair(3),
        "yellow": curses.color_pair(4),
        "bold": curses.A_BOLD,
        "reverse": curses.A_REVERSE,
        "normal": 0,
    }


# -- injection seam for unit-testing border drawing
_draw_border = None


def _draw_acs_border(win) -> None:
    """Draw border with ACS chars; fall back to ASCII on failure."""
    try:
        win.border(
            curses.ACS_VLINE, curses.ACS_VLINE,
            curses.ACS_HLINE, curses.ACS_HLINE,
            curses.ACS_ULCORNER, curses.ACS_URCORNER,
            curses.ACS_LLCORNER, curses.ACS_LRCORNER,
        )
    except (curses.error, AttributeError):
        _draw_ascii_border(win)


def _draw_ascii_border(win) -> None:
    max_y, max_x = win.getmaxyx()
    _safe_addstr(win, 0, 0, "+" + "-" * (max_x - 2) + "+")
    for y in range(1, max_y - 1):
        _safe_addstr(win, y, 0, "|")
        _safe_addstr(win, y, max_x - 1, "|")
    if max_y > 1:
        _safe_addstr(win, max_y - 1, 0, "+" + "-" * (max_x - 2) + "+")


def run_tui(configs: list[ConfigSummary],
            apply_fn: _ApplyFn) -> TuiResult:
    """Launch the full-screen curses selector and return a TuiResult.

    Must be called when stdin/stdout are TTYs.  Handles resize,
    colors, signals, and terminal cleanup automatically.
    """
    locale.setlocale(locale.LC_ALL, "")

    # Signal cleanup — temporary handlers
    old_handlers: dict[int, object] = {}
    sig_result: list[tuple[int, str] | None] = [None]

    def _make_handler(sig_num: int, name: str):
        def handler(_signum, _frame):
            sig_result[0] = (sig_num, name)
        return handler

    for sig_num, name in ((signal.SIGHUP, "SIGHUP"),
                          (signal.SIGTERM, "SIGTERM")):
        try:
            old_handlers[sig_num] = signal.signal(
                sig_num, _make_handler(sig_num, name))
        except Exception:
            pass

    def _inner(stdscr) -> TuiResult:
        curses.curs_set(0)
        _init_colors()
        attrs = _color_attrs()
        stdscr.keypad(True)

        state = AppState(config_count=len(configs))
        state.clamp()

        while True:
            # Check signal result
            if sig_result[0] is not None:
                sig_num, sig_name = sig_result[0]
                return TuiResult(
                    TuiOutcome.TERMINATED,
                    signal_number=sig_num)

            max_y, max_x = stdscr.getmaxyx()
            state.layout = compute_layout(max_x, max_y)
            stdscr.clear()

            if state.layout == LayoutMode.TOO_SMALL:
                _safe_addstr(stdscr, max_y // 2, max_x // 2 - 10,
                             f"Terminal too small ({max_x}x{max_y}). "
                             f"Need 40x12 minimum.",
                             attrs["red"])
                footer = "q/Ctrl-C quit"
                _safe_addstr(stdscr, max_y - 1, 0,
                             footer, attrs["bold"])
                stdscr.refresh()

                key = stdscr.getch()
                if key == ord("q"):
                    return TuiResult(TuiOutcome.QUIT)
                if key == curses.KEY_RESIZE:
                    curses.update_lines_cols()
                    continue
                continue

            # Build menu list
            menu = []
            for i, cfg in enumerate(configs):
                markers = ""
                if cfg.file.is_current:
                    markers += " [current]"
                if not cfg.is_valid:
                    markers += " [invalid]"
                menu.append((cfg.file.name, markers, i))

            # Layout calculations
            if state.layout == LayoutMode.WIDE:
                lw = left_width(max_x)
                detail_w = max_x - lw - 1  # divider column
            else:
                lw = max_x
                detail_w = max_x

            visible_menu = max(0, max_y - HEADER_ROWS - FOOTER_ROWS)
            visible_details = max(0, max_y - HEADER_ROWS - FOOTER_ROWS)

            # Header
            _safe_addstr(stdscr, 0, 0,
                         "OpenCode Configuration Switcher v2.0.0",
                         attrs["cyan"] | attrs["bold"])
            _safe_addstr(stdscr, 1, 0,
                         "─" * (max_x - 1), attrs["cyan"])
            if state.layout == LayoutMode.WIDE:
                _safe_addstr(stdscr, 2, 0,
                             " Configurations" + " " * (lw - 16)
                             + "│ Details",
                             attrs["bold"])
            else:
                mode_label = (" MENU" if state.narrow_pane == NarrowPane.MENU
                              else " DETAILS")
                _safe_addstr(stdscr, 2, 0,
                             f" [{mode_label}]  Tab to switch",
                             attrs["bold"])

            # Content
            if state.layout == LayoutMode.WIDE:
                # Menu (left)
                for r in range(visible_menu):
                    idx = state.menu_offset + r
                    if idx >= len(menu):
                        break
                    name, markers, cfg_i = menu[idx]
                    is_sel = cfg_i == state.selected_idx
                    text = (f" {cfg_i + 1}) {name}{markers}")
                    attr = attrs["reverse"] if is_sel else attrs["normal"]
                    if is_sel:
                        text = text.ljust(lw)
                    _safe_addstr(stdscr, HEADER_ROWS + r, 0, text, attr)

                # Divider
                for y in range(HEADER_ROWS, max_y - FOOTER_ROWS):
                    _safe_addstr(stdscr, y, lw, "│", attrs["cyan"])

                # Details (right)
                if state.selected_idx < len(configs):
                    selected = configs[state.selected_idx]
                    d_lines = format_details(selected, detail_w - 1)
                    for r in range(visible_details):
                        dl_idx = state.detail_offset + r
                        if dl_idx >= len(d_lines):
                            break
                        _safe_addstr(stdscr, HEADER_ROWS + r,
                                     lw + 1,
                                     d_lines[dl_idx], attrs["normal"])

            else:  # NARROW
                if state.narrow_pane == NarrowPane.MENU:
                    for r in range(visible_menu):
                        idx = state.menu_offset + r
                        if idx >= len(menu):
                            break
                        name, markers, cfg_i = menu[idx]
                        is_sel = cfg_i == state.selected_idx
                        text = (f" {cfg_i + 1}) {name}{markers}")
                        attr = attrs["reverse"] if is_sel else attrs["normal"]
                        _safe_addstr(stdscr, HEADER_ROWS + r, 0, text, attr)
                else:  # DETAILS
                    if state.selected_idx < len(configs):
                        selected = configs[state.selected_idx]
                        d_lines = format_details(selected, detail_w)
                        for r in range(visible_details):
                            dl_idx = state.detail_offset + r
                            if dl_idx >= len(d_lines):
                                break
                            _safe_addstr(stdscr, HEADER_ROWS + r, 0,
                                         d_lines[dl_idx], attrs["normal"])

            # Footer
            footer_y = max_y - 2
            if state.overlay_open:
                footer = "Overlay: Up/Down scroll  d/q close"
            elif state.layout == LayoutMode.WIDE:
                footer = (f"Up/Down: select  PgUp/PgDn: scroll  "
                          f"d: raw JSON  Enter: apply  q: quit")
            else:
                if state.narrow_pane == NarrowPane.MENU:
                    footer = (f"Up/Down: select  Tab: Details  "
                              f"d: raw JSON  Enter: apply  q: quit")
                else:
                    footer = (f"Up/Down/PgUp/PgDn: scroll  "
                              f"Tab: Menu  Enter: apply  q: quit")
            if state.status:
                footer = f"{state.status}  |  {footer}"
            _safe_addstr(stdscr, footer_y, 0, footer, attrs["bold"])
            _safe_addstr(stdscr, max_y - 1, 0,
                         "─" * (max_x - 1), attrs["cyan"])

            # Overlay
            if state.overlay_open and state.selected_idx < len(configs):
                raw = configs[state.selected_idx].file.raw_text
                if raw:
                    ov_lines = format_overlay(raw, max_x)
                    ov_visible = max_y - 4
                    for r in range(ov_visible):
                        oi = state.overlay_offset + r
                        if oi >= len(ov_lines):
                            break
                        _safe_addstr(stdscr, 2 + r, 0,
                                     ov_lines[oi], attrs["normal"])

            stdscr.refresh()

            # Input — route all keys through handle_key
            key = stdscr.getch()
            if key == curses.KEY_RESIZE:
                curses.update_lines_cols()
                state.clamp()
                continue

            key_map = {
                curses.KEY_UP: "up",
                curses.KEY_DOWN: "down",
                curses.KEY_PPAGE: "pageup",
                curses.KEY_NPAGE: "pagedown",
                ord("\t"): "tab",
                ord("d"): "d",
                ord("D"): "d",
                ord("q"): "q",
                ord("\x04"): "ctrld",
                3: "ctrlc",
                10: "enter",
                13: "enter",
                ord(" "): " ",
            }
            key_str = key_map.get(key)
            if key_str is None:
                continue

            intent = handle_key(state, key_str)
            if intent == "quit":
                return TuiResult(TuiOutcome.QUIT)
            if intent == "apply":
                if state.selected_idx < len(configs):
                    selected = configs[state.selected_idx]
                    result = apply_fn(
                        selected.file.path,
                        is_valid=selected.is_valid,
                        error_reason=selected.error)
                    if result.status == ApplyStatus.BLOCKED:
                        state.status = result.message
                    elif result.status == ApplyStatus.FAILED:
                        state.status = result.message
                    elif result.status == ApplyStatus.NOOP:
                        return TuiResult(TuiOutcome.NOOP,
                                         apply_result=result)
                    else:
                        return TuiResult(TuiOutcome.APPLIED,
                                         apply_result=result)
            state.clamp()

    try:
        result = curses.wrapper(_inner)
    except KeyboardInterrupt:
        result = TuiResult(TuiOutcome.QUIT)
    except Exception as exc:
        result = TuiResult(
            TuiOutcome.FATAL,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
    finally:
        for sig_num, old_handler in old_handlers.items():
            try:
                signal.signal(sig_num, old_handler)
            except Exception:
                pass

    return result
