# allow: SIZE_OK — plan-pinned single-module TUI: pure core + thin curses
# shell (v2 architecture kept for Task 12's selector rework).
"""Pure TUI layout, state transitions, and footer/prompt formatting.

v3 profile selector.  The curses renderer shell speaks
:class:`tui_data.ProfileSummary` rows (menu via ``tui_data.menu_row``,
details via ``tui_data.format_details``, raw overlay via the record's
cached ``raw_text``) over the injected :class:`SelectorServices` seams —
the selector itself performs ZERO file I/O; the CLI adapter (Task 16)
wires the real engine/store functions.

Contracts (binding for Task 16):

- ``run_profile_tui(summaries, paths, services) -> TuiResult`` —
  ``paths`` is carried for context only; every mutating action goes
  through ``services``.  Enter calls ``services.use_fn(name)``:
  APPLIED/NOOP exit the TUI returning a :class:`TuiResult` whose
  ``apply_result`` carries the engine :class:`UseResult` (the CLI owns
  all post-curses printing); BLOCKED/FAILED keep the TUI interactive
  with ``result.message`` in the footer.
- ``SelectorServices(use_fn, create_fn, delete_fn, refresh_fn)`` —
  ``create_fn``/``delete_fn`` raise store exceptions
  (``InvalidProfileName`` / ``ProfileExistsError`` /
  ``ProfileNotFoundError``) which the selector turns into footer
  status lines; ``refresh_fn()`` returns a fresh ``list[ProfileSummary]``
  which REPLACES the menu (selection clamped).
- ``EDITOR_AVAILABLE = False`` gates the ``e``/``i``/``r`` keys AND
  their footer advertisement; Task 16 flips it and wires the intents
  (``edit`` / ``import`` / ``replace``) which are already routed
  through :func:`handle_key`.
- Prompt footers (pinned): create label ``"New profile name: "``;
  delete label ``"Delete profile '{name}'? [y/N]: "``; success
  ``"Profile created: {name}"`` / ``"Deleted profile: {name}"``;
  failures ``"Profile name must not be empty"``,
  ``str(InvalidProfileName)``, ``"Profile already exists: {name}"``,
  ``"Profile '{name}' not found"``; decline ``"Delete cancelled"``;
  empty-store guard ``"No profiles"``.
- ``display_width`` / ``truncate_display`` are byte-identical v2
  helpers (tui_data imports them).
"""

import curses
import locale
import os
import signal
import unicodedata
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, NamedTuple

from opencode_config_switcher import __version__
from opencode_config_switcher.engine import UseResult, UseStatus
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.profiles import (
    InvalidProfileName,
    ProfileExistsError,
    ProfileNotFoundError,
)

__all__ = [
    "display_width", "truncate_display",
    "LayoutMode", "compute_layout", "left_width", "NarrowPane",
    "AppState", "handle_key",
    "CREATE_PROMPT_LABEL", "delete_prompt_label", "compose_footer",
    "TuiOutcome", "TuiResult",
    "SelectorServices", "run_profile_tui", "EDITOR_AVAILABLE",
    "_safe_addstr", "_draw_acs_border", "_draw_ascii_border",
]

# Task 16 flips this to True when the editor/import/replace screens
# land; while False the keys are inert AND hidden from the footer.
EDITOR_AVAILABLE = False


# ── display-width helpers (byte-identical v2; tui_data imports) ────

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
    """Mutable TUI application state.

    ``prompt`` is ``None`` (no prompt) or one of ``"create"`` /
    ``"delete"``; while set, every printable key is captured into
    ``prompt_buffer`` (rendered after ``prompt_label``), Enter submits,
    Esc/Ctrl-C/Ctrl-D cancel.  ``status`` is the transient footer
    message (engine/create/delete results).
    """

    selected_idx: int = 0
    config_count: int = 0
    menu_offset: int = 0
    detail_offset: int = 0
    narrow_pane: NarrowPane = NarrowPane.MENU
    detail_raw: bool = False
    status: str | None = None
    layout: LayoutMode = LayoutMode.NARROW
    prompt: str | None = None
    prompt_label: str = ""
    prompt_buffer: str = ""

    def clamp(self) -> None:
        """Clamp all indices after config change or resize."""
        if self.config_count == 0:
            self.selected_idx = 0
            self.menu_offset = 0
            self.detail_offset = 0
            return
        self.selected_idx = max(0, min(
            self.selected_idx, self.config_count - 1))
        self.menu_offset = max(0, min(
            self.menu_offset, self.config_count - 1))
        self.detail_offset = max(0, self.detail_offset)


# ── key transitions (pure) ─────────────────────────────────────────

def handle_key(state: AppState, key: str) -> str | None:
    """Apply *key* to *state* and return an intent string or None.

    Valid intents: 'quit', 'apply', 'create', 'delete', 'edit',
    'import', 'replace', 'prompt_submit'.  While ``state.prompt`` is
    set, keys are captured into the prompt buffer instead of driving
    navigation (q/Ctrl-D are CHARACTERS there; Esc/Ctrl-C cancel).
    """
    # Prompt mode captures everything except submit/cancel.
    if state.prompt is not None:
        if key == "enter":
            return "prompt_submit"
        if key in ("esc", "ctrlc", "ctrld"):
            state.prompt = None
            state.prompt_label = ""
            state.prompt_buffer = ""
            return None
        if key == "backspace":
            state.prompt_buffer = state.prompt_buffer[:-1]
            return None
        if len(key) == 1:
            state.prompt_buffer += key
            return None
        return None

    lm = state.layout

    # Universal quit keys
    if key in ("q", "ctrlc", "ctrld"):
        return "quit"

    # TOO_SMALL: only quit allowed (already handled above)
    if lm == LayoutMode.TOO_SMALL:
        return None

    # v3 selector actions (available in every non-TOO_SMALL mode)
    if key == "n":
        return "create"
    if key == "D":
        return "delete"
    if key in ("e", "i", "r") and EDITOR_AVAILABLE:
        return {"e": "edit", "i": "import", "r": "replace"}[key]

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
            state.detail_raw = not state.detail_raw
            state.detail_offset = 0
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
            state.detail_raw = not state.detail_raw
            state.detail_offset = 0
            state.narrow_pane = NarrowPane.DETAILS
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
            state.detail_raw = not state.detail_raw
            state.detail_offset = 0
        elif key == "enter":
            return "apply"

    return None


# ── footer / prompt formatting (pure) ─────────────────────────────

CREATE_PROMPT_LABEL = "New profile name: "


def delete_prompt_label(name: str) -> str:
    """The inline delete-confirmation footer label."""
    return f"Delete profile '{name}'? [y/N]: "


def _mode_footer(state: AppState) -> str:
    if state.layout == LayoutMode.TOO_SMALL:
        return "q/Ctrl-C quit"
    if state.layout == LayoutMode.WIDE:
        footer = ("Up/Down: select  d: raw  Enter: use  "
                  "n: new  D: delete  q: quit")
    elif state.narrow_pane == NarrowPane.MENU:
        footer = ("Up/Down: select  Tab: Details  Enter: use  "
                  "n: new  D: delete  q: quit")
    else:
        footer = ("Up/Down/PgUp/PgDn: scroll  Tab: Menu  "
                  "d: raw  Enter: use  q: quit")
    if EDITOR_AVAILABLE:
        footer += "  e: edit  i: import  r: replace"
    return footer


def compose_footer(state: AppState) -> str:
    """Footer line: the active prompt, else status, else mode hints."""
    if state.prompt is not None:
        return state.prompt_label + state.prompt_buffer
    if state.layout == LayoutMode.TOO_SMALL:
        return _mode_footer(state)
    if state.status:
        return f"{state.status}  |  {_mode_footer(state)}"
    return _mode_footer(state)


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
    apply_result: UseResult | None = None
    error_type: str | None = None
    error_message: str | None = None
    signal_number: int | None = None


# ── injected service seams ────────────────────────────────────────

class SelectorServices(NamedTuple):
    """Every mutating action the selector can perform.

    ``use_fn(name) -> UseResult``; ``create_fn(name)`` /
    ``delete_fn(name)`` raise store exceptions which become footer
    status lines; ``refresh_fn() -> list[ProfileSummary]`` rebuilds the
    menu after a mutation (selection is clamped to the new list).
    """

    use_fn: Callable[[str], UseResult]
    create_fn: Callable[[str], object]
    delete_fn: Callable[[str], object]
    refresh_fn: Callable[[], list]


# ── curses renderer shell ─────────────────────────────────────────

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


def _badge_attr(badge: str, attrs: dict[str, int]) -> int:
    """Palette role per menu badge: INVALID red+bold, ACTIVE green,
    CUSTOM yellow."""
    if badge == "INVALID":
        return attrs["red"] | attrs["bold"]
    if badge == "ACTIVE":
        return attrs["green"]
    if badge == "CUSTOM":
        return attrs["yellow"]
    return attrs["normal"]


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


def _decode_prompt_buffer(text: str) -> str:
    """Rejoin UTF-8 multibyte input delivered as per-byte getch keys."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _refresh_menu(state: AppState, box: dict,
                  services: SelectorServices) -> None:
    """Replace the summary list via ``refresh_fn`` and clamp selection."""
    box["summaries"] = list(services.refresh_fn())
    state.config_count = len(box["summaries"])
    state.clamp()


def _submit_prompt(state: AppState, box: dict,
                   services: SelectorServices) -> None:
    """Act on the submitted prompt buffer; see module docstring."""
    raw = _decode_prompt_buffer(state.prompt_buffer)
    if state.prompt == "create":
        name = raw.strip()
        if not name:
            state.status = "Profile name must not be empty"
        else:
            try:
                services.create_fn(name)
            except InvalidProfileName as exc:
                state.status = str(exc)
            except ProfileExistsError as exc:
                state.status = str(exc)
            else:
                _refresh_menu(state, box, services)
                state.status = f"Profile created: {name}"
    elif state.prompt == "delete":
        if raw.strip().lower() == "y":
            summaries = box["summaries"]
            if state.selected_idx >= len(summaries):
                state.status = "No profiles"
            else:
                name = summaries[state.selected_idx].record.name
                try:
                    services.delete_fn(name)
                except ProfileNotFoundError:
                    state.status = f"Profile '{name}' not found"
                except InvalidProfileName as exc:
                    state.status = str(exc)
                else:
                    _refresh_menu(state, box, services)
                    state.status = f"Deleted profile: {name}"
        else:
            state.status = "Delete cancelled"
    state.prompt = None
    state.prompt_label = ""
    state.prompt_buffer = ""


def run_profile_tui(summaries: list,
                    paths: Paths,
                    services: SelectorServices) -> TuiResult:
    """Launch the full-screen profile selector; see module docstring.

    Must be called when stdin/stdout are TTYs.  Handles resize, colors,
    signals, and terminal cleanup automatically.  ``paths`` is carried
    for context only — all mutations flow through ``services``.
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
        # tui_data imports tui (display helpers) — import lazily here.
        from opencode_config_switcher.tui_data import (
            format_details, format_raw, menu_row, state_badge)

        try:
            curses.curs_set(0)
        except curses.error:
            pass
        _init_colors()
        attrs = _color_attrs()
        stdscr.keypad(True)

        box: dict = {"summaries": list(summaries)}
        state = AppState(config_count=len(box["summaries"]))
        state.clamp()

        while True:
            # Check signal result
            if sig_result[0] is not None:
                sig_num, _sig_name = sig_result[0]
                return TuiResult(
                    TuiOutcome.TERMINATED,
                    signal_number=sig_num)

            max_y, max_x = stdscr.getmaxyx()
            state.layout = compute_layout(max_x, max_y)
            stdscr.clear()
            try:
                curses.curs_set(1 if state.prompt else 0)
            except curses.error:
                pass

            if state.layout == LayoutMode.TOO_SMALL:
                _safe_addstr(stdscr, max_y // 2, max_x // 2 - 10,
                             f"Terminal too small ({max_x}x{max_y}). "
                             f"Need 40x12 minimum.",
                             attrs["red"])
                _safe_addstr(stdscr, max_y - 1, 0,
                             compose_footer(state), attrs["bold"])
                stdscr.refresh()

                key = stdscr.getch()
                if key == ord("q"):
                    return TuiResult(TuiOutcome.QUIT)
                if key == curses.KEY_RESIZE:
                    curses.update_lines_cols()
                    continue
                continue

            current = box["summaries"]

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
                         f"OpenCode Configuration Switcher v{__version__}",
                         attrs["cyan"] | attrs["bold"])
            _safe_addstr(stdscr, 1, 0,
                         "─" * (max_x - 1), attrs["cyan"])
            if state.layout == LayoutMode.WIDE:
                detail_label = (" Details (raw)" if state.detail_raw
                                else " Details")
                _safe_addstr(stdscr, 2, 0,
                             " Profiles" + " " * (lw - 10)
                             + "│" + detail_label,
                             attrs["bold"])
            else:
                if state.narrow_pane == NarrowPane.MENU:
                    mode_label = " MENU"
                elif state.detail_raw:
                    mode_label = " DETAILS (raw)"
                else:
                    mode_label = " DETAILS"
                _safe_addstr(stdscr, 2, 0,
                             f" [{mode_label}]  Tab to switch",
                             attrs["bold"])

            def _menu_row_text(summary, width: int) -> tuple[str, int]:
                """(row text, attr) — badge color unless selected."""
                display = menu_row(summary, width)
                return f" {display}", state_badge(summary)

            # Content
            if state.layout == LayoutMode.WIDE:
                # Menu (left)
                for r in range(visible_menu):
                    idx = state.menu_offset + r
                    if idx >= len(current):
                        break
                    text, badge = _menu_row_text(current[idx], lw - 2)
                    is_sel = idx == state.selected_idx
                    attr = (attrs["reverse"] if is_sel
                            else _badge_attr(badge, attrs))
                    if is_sel:
                        text = text.ljust(lw)
                    _safe_addstr(stdscr, HEADER_ROWS + r, 0, text, attr)

                # Divider
                for y in range(HEADER_ROWS, max_y - FOOTER_ROWS):
                    _safe_addstr(stdscr, y, lw, "│", attrs["cyan"])

                # Details (right)
                if state.selected_idx < len(current):
                    selected = current[state.selected_idx]
                    raw_text = selected.record.raw_text
                    if state.detail_raw and raw_text:
                        d_lines = format_raw(raw_text, detail_w - 1)
                    else:
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
                        if idx >= len(current):
                            break
                        text, badge = _menu_row_text(current[idx],
                                                     max_x - 2)
                        is_sel = idx == state.selected_idx
                        attr = (attrs["reverse"] if is_sel
                                else _badge_attr(badge, attrs))
                        if is_sel:
                            text = text.ljust(max_x)
                        _safe_addstr(stdscr, HEADER_ROWS + r, 0,
                                     text, attr)
                else:  # DETAILS
                    if state.selected_idx < len(current):
                        selected = current[state.selected_idx]
                        raw_text = selected.record.raw_text
                        if state.detail_raw and raw_text:
                            d_lines = format_raw(raw_text, detail_w)
                        else:
                            d_lines = format_details(selected, detail_w)
                        for r in range(visible_details):
                            dl_idx = state.detail_offset + r
                            if dl_idx >= len(d_lines):
                                break
                            _safe_addstr(stdscr, HEADER_ROWS + r, 0,
                                         d_lines[dl_idx], attrs["normal"])

            # Footer
            _safe_addstr(stdscr, max_y - 2, 0,
                         compose_footer(state), attrs["bold"])
            _safe_addstr(stdscr, max_y - 1, 0,
                         "─" * (max_x - 1), attrs["cyan"])

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
                ord("D"): "D",
                ord("n"): "n",
                ord("e"): "e",
                ord("i"): "i",
                ord("r"): "r",
                ord("q"): "q",
                27: "esc",
                127: "backspace",
                curses.KEY_BACKSPACE: "backspace",
                ord("\x04"): "ctrld",
                3: "ctrlc",
                10: "enter",
                13: "enter",
            }
            key_str = key_map.get(key)
            if key_str is None:
                # Printable ASCII (prompt capture) and UTF-8 bytes.
                if 32 <= key < 256:
                    key_str = chr(key)
                else:
                    continue

            intent = handle_key(state, key_str)
            if intent == "quit":
                return TuiResult(TuiOutcome.QUIT)
            if intent == "apply":
                if not current:
                    state.status = "No profiles"
                else:
                    selected = current[state.selected_idx]
                    result = services.use_fn(selected.record.name)
                    if result.status in (UseStatus.BLOCKED,
                                         UseStatus.FAILED):
                        state.status = result.message
                    elif result.status == UseStatus.NOOP:
                        return TuiResult(TuiOutcome.NOOP,
                                         apply_result=result)
                    else:
                        return TuiResult(TuiOutcome.APPLIED,
                                         apply_result=result)
            elif intent == "create":
                state.prompt = "create"
                state.prompt_label = CREATE_PROMPT_LABEL
                state.prompt_buffer = ""
            elif intent == "delete":
                if not current:
                    state.status = "No profiles"
                else:
                    state.prompt = "delete"
                    state.prompt_label = delete_prompt_label(
                        current[state.selected_idx].record.name)
                    state.prompt_buffer = ""
            elif intent == "prompt_submit":
                _submit_prompt(state, box, services)
            # 'edit' / 'import' / 'replace': registered; Task 16 wires.
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
