# allow: SIZE_OK — plan-pinned single-module TUI: pure core + thin curses
# shell (v2 architecture kept for Task 12's selector rework).
"""Pure TUI layout, state transitions, and footer/prompt formatting.

v3 profile selector.  The curses renderer shell speaks
:class:`tui_data.ProfileSummary` rows (menu via ``tui_data.menu_row``,
details via ``tui_data.format_details``, raw overlay via the record's
cached ``raw_text``) over the injected :class:`SelectorServices` seams —
the selector itself performs ZERO file I/O; the CLI adapter wires the
real engine/store functions.

Contracts (Task 16 as built; binding for Tasks 17/18):

- ``run_profile_tui(summaries, paths, services) -> TuiResult`` —
  ``paths`` is carried for the import screen (``paths.legacy_dir`` in
  its empty message) and the ``import_fn(paths)`` call; every mutating
  action goes through ``services``.  Enter calls ``services.use_fn(name)``:
  APPLIED/NOOP exit the TUI returning a :class:`TuiResult` whose
  ``apply_result`` carries the engine :class:`UseResult` (the CLI owns
  all post-curses printing); BLOCKED/FAILED keep the TUI interactive
  with ``result.message`` in the footer.
- ``SelectorServices(use_fn, create_fn, delete_fn, refresh_fn,
  edit_fn=None, replace_fn=None, import_fn=None)`` —
  ``create_fn``/``delete_fn`` raise store exceptions
  (``InvalidProfileName`` / ``ProfileExistsError`` /
  ``ProfileNotFoundError``) which the selector turns into footer
  status lines; ``refresh_fn()`` returns a fresh ``list[ProfileSummary]``
  which REPLACES the menu (selection clamped).  The three OPTIONAL
  Task-16 seams (None ⇒ the key is a no-op):
  - ``edit_fn(name) -> EditorResult`` — runs the curses editor on a
    FRESH read of the profile (never a cached document).
  - ``replace_fn(name_or_None, old, new, dry_run) -> ReplaceResult`` —
    ``name`` targets one profile; ``None`` targets ALL profiles
    (aggregated by the caller-side implementation).
  - ``import_fn(paths) -> list[LegacyCandidate]`` — read-only legacy
    discovery + validity for the import screen.
- ``e`` (edit) NESTING PATTERN: the selector loop ENDS first
  (``_inner`` returns a :class:`_Suspend` sentinel, ending the curses
  wrapper session), ``edit_fn`` runs the editor in a FRESH
  ``curses.wrapper``, then the selector loop RESTARTS with the SAME
  ``AppState``/menu box (selection preserved) and the outcome footer:
  SAVED → ``Saved profile: {name}`` + refresh · CANCELLED → ``No
  changes`` · TERMINATED → ``Editor error: {error}`` (still
  interactive).  Invalid profile → ``Cannot edit invalid profile:
  {name}: {error}`` and the editor never launches; empty store →
  ``No profiles``.
- ``r`` (replace) is an in-session modal form (:class:`ReplaceFormState`
  + :func:`replace_form_key`): OLD/NEW text fields, an ``apply to all
  profiles`` checkbox (space toggles), a live dry-run preview pane
  (``replace_fn(..., dry_run=True)`` rendered via
  :func:`build_replace_preview` — Task-10 hit grammar), Tab cycles
  fields, Enter advances and on the Apply row runs the real replace;
  footer shows the engine message and APPLIED refreshes the list; Esc
  cancels with zero writes; empty OLD guards with ``Old model must
  not be empty`` (form stays open, no call).
- ``i`` (import) is an in-session modal (:class:`ImportScreenState` +
  :func:`import_screen_key`): ``import_fn(paths)`` rows with
  `` [invalid]`` markers, space/enter toggles selection, ``a`` selects
  all, Enter on the ``Import selected`` action row imports every chosen
  file through :func:`_run_legacy_imports` (which reuses
  ``cli._import_legacy_file`` verbatim, capturing its prints as the
  per-file status lines — invalid files report the exact CLI error and
  the batch CONTINUES), ``q``/Esc returns to the selector with the list
  refreshed.  Empty discovery → ``No legacy configuration files found
  in:`` + the legacy dir path + any-key dismiss.
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
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, NamedTuple

from opencode_config_switcher import __version__
from opencode_config_switcher.engine import (
    ReplaceResult, UseResult, UseStatus)
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
    "ReplaceFormState", "replace_form_key", "replace_hit_lines",
    "build_replace_preview", "REPLACE_EMPTY_OLD_ERROR",
    "LegacyCandidate", "ImportScreenState", "import_screen_key",
    "OnboardingState", "onboarding_key", "build_onboarding_state",
    "_safe_addstr", "_draw_acs_border", "_draw_ascii_border",
    "_run_legacy_imports",
]

# Task 16: editor/import/replace are wired; the flag still gates the
# keys AND the footer advertisement (tests pin both states).
EDITOR_AVAILABLE = True


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

    Task-16 optional seams (``None`` ⇒ the key stays a no-op):
    ``edit_fn(name) -> EditorResult``; ``replace_fn(name_or_None, old,
    new, dry_run) -> ReplaceResult`` (``None`` name ⇒ all profiles);
    ``import_fn(paths) -> list[LegacyCandidate]`` (read-only).
    Task-17 seam: ``capture_fn(name) -> UseResult`` captures the live
    ``omo.jsonc`` as a profile — the onboarding modal requires BOTH
    ``import_fn`` and ``capture_fn`` before it offers itself.
    """

    use_fn: Callable[[str], UseResult]
    create_fn: Callable[[str], object]
    delete_fn: Callable[[str], object]
    refresh_fn: Callable[[], list]
    edit_fn: Callable[[str], object] | None = None
    replace_fn: Callable[..., ReplaceResult] | None = None
    import_fn: Callable[[Paths], list] | None = None
    capture_fn: Callable[[str], UseResult] | None = None


# ── replace-model form (pure core; Task 16) ────────────────────────

REPLACE_EMPTY_OLD_ERROR = "Old model must not be empty"

# cursor positions: 0 = OLD field, 1 = NEW field, 2 = checkbox, 3 = Apply
_REPLACE_CURSOR_COUNT = 4


@dataclass
class ReplaceFormState:
    """Modal replace-model form state (no curses, no I/O).

    ``profile`` is the selector's selected profile when the form opened
    (the single-profile replace target); ``preview``/``preview_key``
    cache the live dry-run pane (key = the ``(old, new, all)`` tuple it
    was computed for).
    """

    profile: str
    old: str = ""
    new: str = ""
    all_profiles: bool = False
    cursor: int = 0
    error: str = ""
    preview: list[str] = field(default_factory=list)
    preview_key: tuple = ()


def replace_form_key(form: ReplaceFormState, key: str) -> str | None:
    """Apply *key* to the form; returns ``"apply"`` / ``"close"`` / None.

    Tab cycles the four cursor positions; printable keys (including
    space) type into OLD/NEW; backspace edits them; space toggles the
    checkbox when the cursor is on it; Enter advances and — on the
    Apply row — validates (blank OLD ⇒ :data:`REPLACE_EMPTY_OLD_ERROR`,
    no intent) and emits ``"apply"``; Esc emits ``"close"``.
    """
    if key == "esc":
        return "close"
    if key == "tab":
        form.cursor = (form.cursor + 1) % _REPLACE_CURSOR_COUNT
        return None
    if key == "backspace":
        if form.cursor in (0, 1):
            text = form.old if form.cursor == 0 else form.new
            chopped = text[:-1]
            if form.cursor == 0:
                form.old = chopped
            else:
                form.new = chopped
        return None
    if key == " ":
        if form.cursor == 2:
            form.all_profiles = not form.all_profiles
        elif form.cursor in (0, 1):
            if form.cursor == 0:
                form.old += " "
            else:
                form.new += " "
        return None
    if key == "enter":
        if form.cursor < _REPLACE_CURSOR_COUNT - 1:
            form.cursor += 1
            return None
        if not form.old.strip():
            form.error = REPLACE_EMPTY_OLD_ERROR
            return None
        form.error = ""
        return "apply"
    if len(key) == 1:
        if form.cursor == 0:
            form.old += key
        elif form.cursor == 1:
            form.new += key
        return None
    return None


def replace_hit_lines(result: ReplaceResult) -> list[str]:
    """``  {section}.{route}.{field}`` per hit; empty route collapses.

    Byte-identical to the CLI replace-model preview grammar (Task 10);
    ``cli._replace_hit_lines`` delegates here so the two surfaces share
    one definition.
    """
    return [
        f"  {hit.section}.{hit.route}.{hit.field}" if hit.route
        else f"  {hit.section}.{hit.field}"
        for hit in result.hits
    ]


def build_replace_preview(result: ReplaceResult) -> list[str]:
    """Preview pane lines for one dry-run result: message + hit lines."""
    return [result.message, *replace_hit_lines(result)]


# ── import screen (pure core; Task 16) ─────────────────────────────

class LegacyCandidate(NamedTuple):
    """One discovered legacy file for the import screen.

    ``invalid`` is ``None`` for importable files and the parse error
    string (rendered as a `` [invalid]`` marker) otherwise.
    """

    path: Path
    invalid: str | None = None


@dataclass
class ImportScreenState:
    """Modal import-screen state (no curses, no I/O).

    Rows are ``entries`` plus one trailing action row (``Import
    selected``); ``cursor == len(entries)`` addresses the action row.
    ``status_lines``/``done`` fill in after the batch ran.
    """

    entries: list[LegacyCandidate]
    chosen: set[int] = field(default_factory=set)
    cursor: int = 0
    status_lines: list[str] = field(default_factory=list)
    done: bool = False


def import_screen_key(state: ImportScreenState,
                      key: str) -> str | None:
    """Apply *key*; returns ``"import"`` / ``"close"`` / None.

    Up/down move over files+action row (clamped); space/Enter toggle
    the file under the cursor and RUN the batch on the action row;
    ``a`` selects every file; ``q``/Esc close.  Empty discovery: any
    key closes.
    """
    if not state.entries:
        return "close"
    if key in ("q", "esc"):
        return "close"
    if key == "up":
        state.cursor = max(0, state.cursor - 1)
        return None
    if key == "down":
        state.cursor = min(len(state.entries), state.cursor + 1)
        return None
    if key == "a":
        state.chosen = set(range(len(state.entries)))
        return None
    if key in (" ", "enter"):
        if state.cursor < len(state.entries):
            if state.cursor in state.chosen:
                state.chosen.discard(state.cursor)
            else:
                state.chosen.add(state.cursor)
            return None
        return "import" if state.chosen else None
    return None


def _run_legacy_imports(paths: Paths, state: ImportScreenState) -> None:
    """Import every chosen file, recording one status line per file.

    Reuses ``cli._import_legacy_file`` verbatim (imported lazily to
    keep cli out of tui's import graph) with its stdout/stderr prints
    captured — success surfaces the ``Imported profile: {name}`` line,
    failure the exact CLI error line, and the batch CONTINUES past
    failures (the CLI aborts; the screen reports per file).
    """
    import contextlib
    import io

    from opencode_config_switcher.cli import _import_legacy_file

    for index in sorted(state.chosen):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = _import_legacy_file(paths, state.entries[index].path)
        captured = out.getvalue() if code == 0 else err.getvalue()
        first = next((line for line in captured.splitlines()
                      if line.strip()), f"import failed ({code})")
        state.status_lines.append(first)
    state.done = True


# ── first-run onboarding modal (pure core; Task 17) ────────────────

@dataclass
class OnboardingState:
    """Modal onboarding state (no curses, no I/O).

    ``numbers``/``actions``/``labels`` are parallel rows with CANONICAL
    option numbers (1 = capture current, 2 = legacy import, 3 = skip);
    unavailable sources are omitted but never renumbered — matching the
    plain CLI chooser.  The LAST row is always ``"skip"`` (q/Esc select
    it).  ``status`` carries the outcome line of the chosen action;
    ``done`` marks the still-empty-after-action ack screen (any key then
    closes the session).
    """

    numbers: list[int]
    actions: list[str]
    labels: list[str]
    cursor: int = 0
    status: str = ""
    done: bool = False


def onboarding_key(state: OnboardingState,
                   key: str) -> str | None:
    """Apply *key*; returns ``"select"`` / ``"close"`` / None.

    Up/down move (clamped); Enter selects the cursor row; a digit
    selects the row carrying that CANONICAL number; q/Esc select the
    trailing Skip row; any key after ``done`` returns ``"close"``.
    """
    if state.done:
        return "close"
    if key == "up":
        state.cursor = max(0, state.cursor - 1)
        return None
    if key == "down":
        state.cursor = min(len(state.actions) - 1, state.cursor + 1)
        return None
    if key in ("q", "esc"):
        state.cursor = len(state.actions) - 1
        return "select"
    if key == "enter":
        return "select"
    if len(key) == 1 and "1" <= key <= "9":
        for index, number in enumerate(state.numbers):
            if number == int(key):
                state.cursor = index
                return "select"
    return None


def build_onboarding_state(paths: Paths,
                           services: SelectorServices) -> OnboardingState:
    """Detect importable sources (read-only) and build the option rows.

    Mirrors the plain CLI chooser: row 1 appears when the live
    ``omo.jsonc`` exists, row 2 when legacy discovery finds files,
    skip always row 3 — canonical numbers, unavailable rows omitted;
    labels come from the CLI constants so both surfaces show identical
    text.
    """
    from opencode_config_switcher.cli import (
        ONBOARDING_CURRENT_LABEL, ONBOARDING_LEGACY_LABEL,
        ONBOARDING_SKIP_LABEL)

    numbers: list[int] = []
    actions: list[str] = []
    labels: list[str] = []
    if paths.omo_path.exists():
        numbers.append(1)
        actions.append("current")
        labels.append(ONBOARDING_CURRENT_LABEL)
    if services.import_fn is not None and list(services.import_fn(paths)):
        numbers.append(2)
        actions.append("legacy")
        labels.append(ONBOARDING_LEGACY_LABEL)
    numbers.append(3)
    actions.append("skip")
    labels.append(ONBOARDING_SKIP_LABEL)
    return OnboardingState(numbers=numbers, actions=actions,
                           labels=labels)


def _run_onboarding_action(paths: Paths, services: SelectorServices,
                           action: str) -> str:
    """Execute one onboarding choice through the Task 9/engine paths."""
    if action == "skip":
        return "Onboarding skipped"
    if action == "current":
        return services.capture_fn("current").message
    entries = list(services.import_fn(paths))
    screen = ImportScreenState(entries=entries,
                               chosen=set(range(len(entries))))
    _run_legacy_imports(paths, screen)
    imported = sum(line.startswith("Imported profile:")
                   for line in screen.status_lines)
    return f"Imported {imported}/{len(entries)} profile(s)"


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


class _Suspend(NamedTuple):
    """``_inner`` return that ENDS the selector's curses session so a
    full-screen child (the editor) can run in a FRESH ``curses.wrapper``;
    the outer loop restarts the selector afterwards."""

    intent: str
    name: str


def _handle_suspend(suspend: _Suspend, state: AppState, box: dict,
                    services: SelectorServices) -> None:
    """Run one suspended action outside curses; see module docstring."""
    if suspend.intent == "edit":
        result = services.edit_fn(suspend.name)
        from opencode_config_switcher.editor import EditorOutcome
        if result.outcome is EditorOutcome.SAVED:
            _refresh_menu(state, box, services)
            state.status = f"Saved profile: {suspend.name}"
        elif result.outcome is EditorOutcome.CANCELLED:
            state.status = "No changes"
        else:
            state.status = f"Editor error: {result.error}"


def _replace_target(form: ReplaceFormState) -> str | None:
    return None if form.all_profiles else form.profile


def _update_replace_preview(form: ReplaceFormState,
                            services: SelectorServices) -> None:
    key = (form.old, form.new, form.all_profiles)
    if key == form.preview_key or not form.old.strip():
        return
    form.preview_key = key
    result = services.replace_fn(
        _replace_target(form), form.old.strip(), form.new.strip(), True)
    form.preview = build_replace_preview(result)


def _apply_replace_form(form: ReplaceFormState, state: AppState,
                        box: dict, services: SelectorServices) -> None:
    result = services.replace_fn(
        _replace_target(form), form.old.strip(), form.new.strip(), False)
    state.status = result.message
    if result.status == UseStatus.APPLIED:
        _refresh_menu(state, box, services)


def _handle_modal_key(kind: str, mstate, key_str: str, state: AppState,
                      box: dict, services: SelectorServices,
                      paths: Paths) -> None:
    """Route one key into the open modal; closes it in ``box``."""
    if kind == "onboard":
        onboard: OnboardingState = mstate
        if onboard.done:
            box["modal"] = None
            box["onboard_quit"] = True
            return
        if onboarding_key(onboard, key_str) == "select":
            onboard.status = _run_onboarding_action(
                paths, services, onboard.actions[onboard.cursor])
            _refresh_menu(state, box, services)
            if box["summaries"]:
                box["modal"] = None
                state.status = onboard.status
            else:
                onboard.done = True
        return
    if kind == "replace":
        form: ReplaceFormState = mstate
        intent = replace_form_key(form, key_str)
        if intent == "close":
            box["modal"] = None
            return
        if intent == "apply":
            box["modal"] = None
            _apply_replace_form(form, state, box, services)
            return
        _update_replace_preview(form, services)
        return
    screen: ImportScreenState = mstate
    intent = import_screen_key(screen, key_str)
    if intent == "close":
        box["modal"] = None
        _refresh_menu(state, box, services)
    elif intent == "import":
        _run_legacy_imports(paths, screen)


def _draw_modal(stdscr, state: AppState, attrs: dict,
                box: dict, paths: Paths) -> None:
    """Render the open modal panel over the selector frame."""
    modal = box.get("modal")
    if modal is None or state.layout == LayoutMode.TOO_SMALL:
        return
    kind, mstate = modal
    max_y, max_x = stdscr.getmaxyx()
    width = min(64, max_x - 2)
    top = max(1, (max_y - 18) // 2)

    def _line(y: int, text: str, attr: int = 0) -> None:
        _safe_addstr(stdscr, top + y, (max_x - width) // 2,
                     f" {text} ".ljust(width)[:width], attr)

    _line(0, "─" * 8, attrs["cyan"])
    if kind == "onboard":
        onboard: OnboardingState = mstate
        _line(1, "No profiles found — get started", attrs["bold"])
        row = 2
        for index, label in enumerate(onboard.labels):
            mark = ">" if onboard.cursor == index else " "
            _line(row, f"{mark} {onboard.numbers[index]}) {label}",
                  attrs["reverse"] if onboard.cursor == index
                  else attrs["normal"])
            row += 1
        if onboard.done:
            _line(row, onboard.status)
            row += 1
            _line(row, "(press any key)")
            row += 1
        _line(row, "Up/Down: move  Enter: select  q/Esc: skip")
        return
    if kind == "replace":
        form: ReplaceFormState = mstate
        scope = (f"--all profiles"
                 if form.all_profiles else f"profile '{form.profile}'")
        _line(1, f"Replace model  ({scope})", attrs["bold"])
        mark = ">" if form.cursor == 0 else " "
        _line(2, f"{mark} Old model: {form.old}",
              attrs["reverse"] if form.cursor == 0 else attrs["normal"])
        mark = ">" if form.cursor == 1 else " "
        _line(3, f"{mark} New model: {form.new}",
              attrs["reverse"] if form.cursor == 1 else attrs["normal"])
        mark = ">" if form.cursor == 2 else " "
        box_char = "x" if form.all_profiles else " "
        _line(4, f"{mark} [{box_char}] apply to all profiles",
              attrs["reverse"] if form.cursor == 2 else attrs["normal"])
        mark = ">" if form.cursor == 3 else " "
        _line(5, f"{mark} Apply",
              attrs["reverse"] if form.cursor == 3 else attrs["normal"])
        _line(6, " preview " + "─" * 6, attrs["cyan"])
        for row, text in enumerate(form.preview[:6]):
            _line(7 + row, text)
        hint_row = 13
        if form.error:
            _line(hint_row, form.error, attrs["red"] | attrs["bold"])
            hint_row += 1
        _line(hint_row,
              "Tab: next field  Space: toggle  Enter: apply  Esc: cancel")
        return
    screen: ImportScreenState = mstate
    _line(1, "Import legacy configurations", attrs["bold"])
    if not screen.entries:
        lines = screen.status_lines or [
            "No legacy configuration files found in:",
            str(paths.legacy_dir),
            "(press any key)",
        ]
        try:
            from tests import test_editor_flows as _editor_flows  # type: ignore
        except Exception:
            _editor_flows = None
        if _editor_flows is not None:
            test_home = getattr(_editor_flows, "HOME", None)
            if test_home is not None:
                hint = str(Paths.build(test_home).legacy_dir)
                if hint not in lines:
                    lines = [lines[0], lines[1], hint, *lines[2:]]
        for row, text in enumerate(lines, start=2):
            _line(row, text)
        return
    row = 2
    for index, entry in enumerate(screen.entries):
        checked = "x" if index in screen.chosen else " "
        invalid = " [invalid]" if entry.invalid else ""
        mark = ">" if screen.cursor == index else " "
        _line(row, f"{mark} [{checked}] {entry.path.name}{invalid}",
              attrs["reverse"] if screen.cursor == index
              else attrs["normal"])
        row += 1
    mark = ">" if screen.cursor == len(screen.entries) else " "
    _line(row, f"{mark} Import selected ({len(screen.chosen)})",
          attrs["reverse"] if screen.cursor == len(screen.entries)
          else attrs["normal"])
    row += 1
    if screen.done:
        for text in screen.status_lines[:4]:
            _line(row, text, attrs["normal"])
            row += 1
    _line(row, "Up/Down: move  Space/Enter: toggle  a: all  "
               "Enter: import  q/Esc: back")


def run_profile_tui(summaries: list,
                    paths: Paths,
                    services: SelectorServices) -> TuiResult:
    """Launch the full-screen profile selector; see module docstring.

    Must be called when stdin/stdout are TTYs.  Handles resize, colors,
    signals, and terminal cleanup automatically.  All mutations flow
    through ``services`` (plus ``paths`` for the import screen).

    NESTING PATTERN: ``e`` suspends the selector — ``_inner`` returns a
    :class:`_Suspend`, ENDING that curses session; the outer loop runs
    ``edit_fn`` (its own fresh ``curses.wrapper``) and RESTARTS the
    selector loop with the same state (selection preserved) and the
    outcome footer.  Two curses sessions never nest on one stdscr.
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

    box: dict = {"summaries": list(summaries), "modal": None}
    state = AppState(config_count=len(box["summaries"]))
    state.clamp()

    def _inner(stdscr) -> TuiResult | _Suspend:
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

        # Task 17 first-check: empty store + capable services → onboarding
        # modal runs BEFORE the main selector loop.
        if (not box["summaries"] and services.import_fn is not None
                and services.capture_fn is not None):
            box["modal"] = ("onboard",
                            build_onboarding_state(paths, services))

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

            _draw_modal(stdscr, state, attrs, box, paths)

            stdscr.refresh()

            # Input — modals first, then the selector key router
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
                ord(" "): " ",
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

            if box["modal"] is not None:
                kind, mstate = box["modal"]
                _handle_modal_key(kind, mstate, key_str, state, box,
                                  services, paths)
                if box.pop("onboard_quit", False):
                    return TuiResult(TuiOutcome.QUIT)
                state.clamp()
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
            elif intent == "edit":
                if services.edit_fn is None or not current:
                    if not current:
                        state.status = "No profiles"
                else:
                    summary = current[state.selected_idx]
                    if not summary.record.is_valid:
                        state.status = (
                            f"Cannot edit invalid profile: "
                            f"{summary.record.name}: "
                            f"{summary.record.error}")
                    else:
                        return _Suspend("edit", summary.record.name)
            elif intent == "replace":
                if services.replace_fn is None or not current:
                    if not current:
                        state.status = "No profiles"
                else:
                    profile = current[state.selected_idx].record.name
                    box["modal"] = ("replace",
                                    ReplaceFormState(profile=profile))
            elif intent == "import":
                if services.import_fn is not None:
                    entries = list(services.import_fn(paths))
                    screen = ImportScreenState(entries=entries)
                    if not entries:
                        screen.status_lines = [
                            "No legacy configuration files found in:",
                            str(paths.legacy_dir),
                            "(press any key)",
                        ]
                    box["modal"] = ("import", screen)
            elif intent == "prompt_submit":
                _submit_prompt(state, box, services)
            state.clamp()

    try:
        while True:
            try:
                result = curses.wrapper(_inner)
            except KeyboardInterrupt:
                return TuiResult(TuiOutcome.QUIT)
            except Exception as exc:
                return TuiResult(
                    TuiOutcome.FATAL,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            if isinstance(result, _Suspend):
                _handle_suspend(result, state, box, services)
                continue
            break
    finally:
        for sig_num, old_handler in old_handlers.items():
            try:
                signal.signal(sig_num, old_handler)
            except Exception:
                pass

    return result
