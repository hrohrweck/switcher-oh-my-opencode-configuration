"""Editor foundations: screens, pure state machine, and input components.

Architecture mirrors v2 ``tui.py``: a PURE core (screens, ``handle_key``,
parsers — no curses, no I/O, no time, no randomness) plus a THIN curses
shell (``run_editor`` / ``run_editor_curses``) that owns every curses
interaction.  Tasks 14/15 build screen LOGIC on top of this state
machine; Task 16 wires the shell into the selector.

PURE-EMIT boundary (binding for Task 14):
    Entry-level operations — ``,``/``.`` (move entry) and ``x`` on a
    route ENTRY — emit their action with ``payload=(from_index,
    to_index)`` (moves) or land in CONFIRM with pending
    ``"delete-entry"``; they update ``entry_index`` (moves only), set
    ``dirty=True``, and perform NO document mutation.  Task 14
    implements the chain surgery (agent model/fallback re-split,
    category list swap, primary promotion).  Route DELETION is the one
    in-core mutation: ``x`` on ROUTE_LIST → CONFIRM; ``confirm-yes``
    with pending ``"delete-route"`` deletes the route from
    ``document["[opencode]"]`` here (clamp ``route_index``, back to
    ``prev_screen``, ``entry_index`` reset — the selected route
    changed).  Pending values: ``"quit"``, ``"delete-route"``,
    ``"delete-entry"``.

Shell contract:
    ``new_state_required`` is True iff the screen changed OR the action
    is one of quit/save/add/rename/confirm-yes (the shell must act).
    ``confirm-yes`` carries the pending action string as its payload;
    payload "quit" → shell returns CANCELLED, "delete-entry" → Task 14
    acts, "delete-route" was already executed in-core.
"""
# allow: SIZE_OK — plan-pinned single-module layout (Tasks 14/15/16
# extend this file): pure core + thin shell, mirroring tui.py.

import curses
from dataclasses import dataclass
from enum import Enum
from typing import Callable, NamedTuple


# ── vocabulary ─────────────────────────────────────────────────────

EDITOR_ACTIONS = (
    "none", "quit", "save", "open-route", "open-entry", "open-form",
    "open-settings", "open-raw", "close", "confirm-yes", "confirm-no",
    "move-up", "move-down", "add", "edit", "delete", "rename",
    "toggle", "cycle-left", "cycle-right",
)
"""Every action ``handle_key`` can emit.  ``edit`` is reserved for
Tasks 14/15 (the core opens forms via open-entry/open-form instead)."""

# Actions the shell must act on even when the screen did not change.
_SHELL_ACTING_ACTIONS = frozenset(
    {"quit", "save", "add", "rename", "confirm-yes"})


class EditorScreen(str, Enum):
    ROUTE_LIST = "ROUTE_LIST"
    ROUTE_EDITOR = "ROUTE_EDITOR"
    MODEL_ENTRY_FORM = "MODEL_ENTRY_FORM"
    SETTINGS_FORM = "SETTINGS_FORM"
    CONFIRM = "CONFIRM"
    RAW_VIEW = "RAW_VIEW"


class EditorOutcome(str, Enum):
    SAVED = "SAVED"
    CANCELLED = "CANCELLED"
    TERMINATED = "TERMINATED"


# ── document model ────────────────────────────────────────────────

class RouteItem(NamedTuple):
    kind: str          # "agent" | "category"
    name: str
    block: dict
    models_list_len: int | None   # categories: len(models); agents: None


class EditorDocument:
    """Wrapper around the profile dict; mutated ONLY through the
    documented in-core operations (route deletion) plus future
    Task 14/15 shell-level operations."""

    def __init__(self, name: str, document: dict):
        self.name = name
        self.document = document

    def routes(self) -> list[RouteItem]:
        """Agents then categories, document insertion order, from
        ``document["[opencode]"]``.  Missing sections → empty; missing
        or non-dict harness → empty; non-dict route blocks skipped
        (malformed ≙ absent, mirroring omoconfig)."""
        harness = self.document.get("[opencode]") \
            if isinstance(self.document, dict) else None
        if not isinstance(harness, dict):
            return []
        items: list[RouteItem] = []
        for section, kind in (("agents", "agent"),
                              ("categories", "category")):
            sec = harness.get(section)
            if not isinstance(sec, dict):
                continue
            for name, block in sec.items():
                if not isinstance(block, dict):
                    continue
                if kind == "category":
                    # None means "agent" (no models list); malformed → 0.
                    models = block.get("models")
                    mlen = len(models) if isinstance(models, list) \
                        else 0
                else:
                    mlen = None
                items.append(RouteItem(kind, name, block, mlen))
        return items


def route_entry_count(item: RouteItem | None) -> int:
    """Chain length of one route: category → len(models list); agent →
    (1 if 'model' present) + len(fallback_models list).  Malformed or
    missing pieces count as 0."""
    if item is None:
        return 0
    if item.kind == "category":
        models = item.block.get("models")
        return len(models) if isinstance(models, list) else 0
    count = 1 if "model" in item.block else 0
    fallbacks = item.block.get("fallback_models")
    if isinstance(fallbacks, list):
        count += len(fallbacks)
    return count


# ── state ─────────────────────────────────────────────────────────

@dataclass
class EditorState:
    """Mutable editor state; all transitions happen in ``handle_key``."""

    screen: EditorScreen = EditorScreen.ROUTE_LIST
    prev_screen: EditorScreen | None = None   # one-deep origin stack
    route_index: int = 0     # index into the combined agents-then-categories list
    entry_index: int = 0     # chain position (past-end sentinel = NEW entry)
    field_index: int = 0
    field_count: int = 1     # set by the shell when opening a form (Tasks 14/15)
    dirty: bool = False
    status: str = ""         # transient message / confirm question
    confirm_pending_action: str = ""   # "" | "quit" | "delete-route" | "delete-entry"
    confirm_answer: bool | None = None

    def clamp(self, route_count: int, entry_count: int,
              field_count: int) -> None:
        """Clamp all three indices into their ranges (0 when count 0)."""
        self.route_index = _clamp_index(self.route_index, route_count)
        self.entry_index = _clamp_index(self.entry_index, entry_count)
        self.field_index = _clamp_index(self.field_index, field_count)


def _clamp_index(index: int, count: int) -> int:
    if count <= 0:
        return 0
    return max(0, min(index, count - 1))


class StateTransition(NamedTuple):
    new_state_required: bool
    action: str
    payload: object


# ── pure state machine ────────────────────────────────────────────

def handle_key(state: EditorState, key: str,
               doc: EditorDocument) -> StateTransition:
    """Apply *key* to *state* (mutating it) and return the transition.

    Pure function of (state, key, doc): no I/O, no time, no
    randomness, no document mutation except the documented route
    deletion on confirm-yes.
    """
    before = state.screen
    action, payload = _dispatch(state, key, doc)
    required = (state.screen is not before) \
        or (action in _SHELL_ACTING_ACTIONS)
    return StateTransition(required, action, payload)


def _dispatch(state: EditorState, key: str,
              doc: EditorDocument) -> tuple[str, object]:
    if state.screen is EditorScreen.CONFIRM:
        return _key_confirm(state, key, doc)
    if key == "q" and state.screen is not EditorScreen.RAW_VIEW:
        return _global_quit(state)
    if key == "CTRL_C":
        return _global_quit(state)
    if state.screen is EditorScreen.RAW_VIEW:
        return _key_raw_view(state, key)
    if state.screen is EditorScreen.ROUTE_LIST:
        return _key_route_list(state, key, doc)
    if state.screen is EditorScreen.ROUTE_EDITOR:
        return _key_route_editor(state, key, doc)
    return _key_form(state, key)   # MODEL_ENTRY_FORM / SETTINGS_FORM


def _global_quit(state: EditorState) -> tuple[str, object]:
    if state.dirty:
        state.prev_screen = state.screen
        state.screen = EditorScreen.CONFIRM
        state.status = "Unsaved changes — quit anyway? (y/n)"
        state.confirm_pending_action = "quit"
        state.confirm_answer = None
        return "none", None
    return "quit", None


def _back_to_prev(state: EditorState) -> None:
    """Pop back to prev_screen and restore prev to the target's
    default origin (the navigation graph is fixed: ROUTE_EDITOR comes
    from ROUTE_LIST, forms from their opener, RAW_VIEW/CONFIRM from
    wherever they were opened)."""
    target = state.prev_screen \
        if state.prev_screen is not None else EditorScreen.ROUTE_LIST
    state.screen = target
    state.prev_screen = _DEFAULT_ORIGIN.get(target)


_DEFAULT_ORIGIN = {
    EditorScreen.ROUTE_LIST: None,
    EditorScreen.ROUTE_EDITOR: EditorScreen.ROUTE_LIST,
    EditorScreen.MODEL_ENTRY_FORM: EditorScreen.ROUTE_EDITOR,
    EditorScreen.SETTINGS_FORM: EditorScreen.ROUTE_LIST,
    EditorScreen.RAW_VIEW: EditorScreen.ROUTE_LIST,
    EditorScreen.CONFIRM: EditorScreen.ROUTE_LIST,
}


# -- CONFIRM ────────────────────────────────────────────────────────

def _key_confirm(state: EditorState, key: str,
                 doc: EditorDocument) -> tuple[str, object]:
    if key == "y":
        pending = state.confirm_pending_action
        if not pending:
            return "none", None   # nothing pending: ignored
        state.confirm_answer = True
        if pending == "delete-route":
            _execute_delete_route(state, doc)
        _back_to_prev(state)
        state.confirm_pending_action = ""
        state.status = ""
        return "confirm-yes", pending
    if key in ("n", "esc"):
        state.confirm_answer = False
        _back_to_prev(state)
        state.confirm_pending_action = ""
        state.status = ""
        return "confirm-no", None
    return "none", None   # everything else ignored (incl. q / CTRL_C)


def _execute_delete_route(state: EditorState, doc: EditorDocument
                          ) -> None:
    routes = doc.routes()
    if not 0 <= state.route_index < len(routes):
        return
    item = routes[state.route_index]
    harness = doc.document.get("[opencode]")
    if not isinstance(harness, dict):
        return
    section = harness.get("agents" if item.kind == "agent"
                          else "categories")
    if isinstance(section, dict) and item.name in section:
        del section[item.name]
    state.dirty = True
    state.route_index = _clamp_index(
        state.route_index, len(doc.routes()))
    state.entry_index = 0   # selected route changed


# -- ROUTE_LIST ─────────────────────────────────────────────────────

def _key_route_list(state: EditorState, key: str,
                    doc: EditorDocument) -> tuple[str, object]:
    count = len(doc.routes())
    if key in ("up", "down"):
        changed = False
        if key == "up" and state.route_index > 0:
            state.route_index -= 1
            changed = True
        elif key == "down" and state.route_index < count - 1:
            state.route_index += 1
            changed = True
        if changed:   # route change resets chain position + status
            state.entry_index = 0
            state.status = ""
        return "none", None
    if key == "enter":
        if count == 0:
            return "none", None
        state.prev_screen = EditorScreen.ROUTE_LIST
        state.screen = EditorScreen.ROUTE_EDITOR
        return "open-route", None   # NO index resets
    if key == "a":
        return "add", None   # the SHELL collects the name
    if key == "x":
        if count == 0:
            return "none", None
        name = doc.routes()[state.route_index].name
        state.prev_screen = EditorScreen.ROUTE_LIST
        state.screen = EditorScreen.CONFIRM
        state.status = f"Delete route '{name}'?"
        state.confirm_pending_action = "delete-route"
        state.confirm_answer = None
        return "delete", None
    if key == "R":
        if count == 0:
            return "none", None
        return "rename", None
    if key == "s":
        state.prev_screen = EditorScreen.ROUTE_LIST
        state.screen = EditorScreen.SETTINGS_FORM
        state.field_index = 0
        return "open-settings", None
    if key == "d":
        state.prev_screen = EditorScreen.ROUTE_LIST
        state.screen = EditorScreen.RAW_VIEW
        return "open-raw", None
    if key == "S":
        return "save", None
    return "none", None   # space + unknown keys


# -- ROUTE_EDITOR ───────────────────────────────────────────────────

def _key_route_editor(state: EditorState, key: str,
                      doc: EditorDocument) -> tuple[str, object]:
    routes = doc.routes()
    item = routes[state.route_index] \
        if 0 <= state.route_index < len(routes) else None
    count = route_entry_count(item)
    if key == "up":
        if state.entry_index > 0:
            state.entry_index -= 1
        return "none", None
    if key == "down":
        if state.entry_index < count - 1:
            state.entry_index += 1
        return "none", None
    if key == "enter":
        if count == 0:
            return "none", None
        state.prev_screen = EditorScreen.ROUTE_EDITOR
        state.screen = EditorScreen.MODEL_ENTRY_FORM
        state.field_index = 0
        return "open-entry", None
    if key == "a":
        # MODEL_ENTRY_FORM for a NEW entry: past-end sentinel.
        state.prev_screen = EditorScreen.ROUTE_EDITOR
        state.screen = EditorScreen.MODEL_ENTRY_FORM
        state.field_index = 0
        state.entry_index = count
        return "open-form", None
    if key == "x":
        if count == 0:
            return "none", None
        state.prev_screen = EditorScreen.ROUTE_EDITOR
        state.screen = EditorScreen.CONFIRM
        state.status = f"Delete entry {state.entry_index}?"
        state.confirm_pending_action = "delete-entry"
        state.confirm_answer = None
        return "delete", None
    if key == ",":
        if 0 <= state.entry_index < count - 1:
            src = state.entry_index
            dst = src + 1
            state.entry_index = dst
            state.dirty = True
            return "move-down", (src, dst)
        return "none", None   # boundary or stale index: no move, no dirty
    if key == ".":
        if 1 <= state.entry_index < count:
            src = state.entry_index
            dst = src - 1
            state.entry_index = dst
            state.dirty = True
            return "move-up", (src, dst)
        return "none", None
    if key == "d":
        state.prev_screen = EditorScreen.ROUTE_EDITOR
        state.screen = EditorScreen.RAW_VIEW
        return "open-raw", None
    if key == "S":
        return "save", None
    if key == "esc":
        _back_to_prev(state)
        return "close", None
    return "none", None   # space + unknown keys


# -- forms ──────────────────────────────────────────────────────────

def _key_form(state: EditorState, key: str) -> tuple[str, object]:
    if key == "up":
        if state.field_index > 0:
            state.field_index -= 1
        return "none", None
    if key == "down":
        if state.field_index < state.field_count - 1:
            state.field_index += 1
        return "none", None
    if key == " ":
        return "toggle", None
    if key == "left":
        return "cycle-left", None
    if key == "right":
        return "cycle-right", None
    if key == "enter":
        if state.field_index >= state.field_count - 1:
            return "save", None   # shell validates before save_cb
        state.field_index += 1
        return "none", None
    if key == "S":
        return "save", None
    if key == "esc":
        _back_to_prev(state)
        return "close", None
    return "none", None


# -- RAW_VIEW ───────────────────────────────────────────────────────

_RAW_SCROLL_DELTAS = {"up": -1, "down": 1, "pageup": -10, "pagedown": 10}


def _key_raw_view(state: EditorState, key: str) -> tuple[str, object]:
    if key in _RAW_SCROLL_DELTAS:
        return "none", _RAW_SCROLL_DELTAS[key]   # shell owns the offset
    if key in ("d", "q", "esc"):
        _back_to_prev(state)
        return "close", None
    return "none", None


# ── input components (pure) ───────────────────────────────────────

class FieldError(ValueError):
    """Validation failure with a user-renderable message."""


def parse_number(text: str, *, kind: str) -> float | int | None:
    """Parse a form number field; empty/whitespace → None (absent).

    temperature → float within 0..2; top_p → float within 0..1;
    max_tokens → positive int; any other kind → int when possible,
    else float.
    """
    if not text.strip():
        return None
    if kind in ("temperature", "top_p"):
        try:
            value = float(text)
        except ValueError:
            raise FieldError(f"Invalid number: {text!r}") from None
        if kind == "temperature" and not 0 <= value <= 2:
            raise FieldError("temperature must be within 0..2")
        if kind == "top_p" and not 0 <= value <= 1:
            raise FieldError("top_p must be within 0..1")
        return value
    if kind == "max_tokens":
        try:
            value = int(text)
        except ValueError:
            raise FieldError(f"Invalid number: {text!r}") from None
        if value <= 0:
            raise FieldError("max_tokens must be a positive integer")
        return value
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        raise FieldError(f"Invalid number: {text!r}") from None


def parse_retry_errors(text: str) -> list[int] | None:
    """Parse a comma-separated int list; empty → None (absent)."""
    if not text.strip():
        return None
    values: list[int] = []
    for segment in text.split(","):
        try:
            values.append(int(segment.strip()))
        except ValueError:
            raise FieldError(
                "retry_on_errors must be a comma-separated list of "
                "integers") from None
    return values


REASONING_CYCLE = ("unset", "off", "minimal", "low", "medium", "high",
                   "xhigh", "max", "auto")


def cycle_reasoning(current: str | None, direction: int) -> str | None:
    """Step through REASONING_CYCLE; None ≡ "unset" (field absent).

    Landing on the unset position returns None (field absent).  Any
    value outside the cycle (custom raw or ``<custom:...>`` wrapped)
    cycles to unset → None.
    """
    if current is None or current == "unset":
        index = 0
    elif current in REASONING_CYCLE:
        index = REASONING_CYCLE.index(current)
    else:
        return None   # custom → next press is unset
    new_index = (index + direction) % len(REASONING_CYCLE)
    return None if new_index == 0 else REASONING_CYCLE[new_index]


def format_reasoning_custom(value: str) -> str:
    """Display form for a reasoning value outside the enum."""
    return f"<custom:{value}>"


# ── result contract + thin curses shell ───────────────────────────

@dataclass(frozen=True)
class EditorResult:
    outcome: EditorOutcome
    document: dict
    error: str | None = None


_SaveCb = Callable[[str, dict], None]


def run_editor(surface, profile_name: str, document: dict,
               save_cb: _SaveCb) -> EditorResult:
    """Drive the pure core against an injected curses-like *surface*.

    Never lets curses.error (or anything else) escape: SAVED →
    ``save_cb(profile_name, document)`` then return; CANCELLED → return
    without saving; any exception → TERMINATED with the error string.
    """
    try:
        return _editor_loop(surface, profile_name, document, save_cb)
    except KeyboardInterrupt:
        return EditorResult(EditorOutcome.CANCELLED, document, None)
    except Exception as exc:  # noqa: BLE001 — shell boundary
        return EditorResult(EditorOutcome.TERMINATED, document,
                            f"{type(exc).__name__}: {exc}")


def run_editor_curses(stdscr, profile_name: str, document: dict,
                      save_cb: _SaveCb) -> EditorResult:
    """Thin adapter for a curses.wrapper-managed stdscr."""
    return run_editor(stdscr, profile_name, document, save_cb)


def _editor_loop(surface, profile_name: str, document: dict,
                 save_cb: _SaveCb) -> EditorResult:
    doc = EditorDocument(profile_name, document)
    state = EditorState()
    _best_effort(lambda: surface.keypad(True))
    _best_effort(lambda: curses.curs_set(0))
    raw_offset = 0
    while True:
        _render(surface, state, doc, raw_offset)
        key = _read_key(surface)
        if key is None:
            continue
        trans = handle_key(state, key, doc)
        if state.screen is EditorScreen.RAW_VIEW \
                and isinstance(trans.payload, int):
            raw_offset = max(0, raw_offset + trans.payload)
        elif state.screen is not EditorScreen.RAW_VIEW:
            raw_offset = 0
        if trans.action == "quit":
            return EditorResult(EditorOutcome.CANCELLED, document, None)
        if trans.action == "confirm-yes" and trans.payload == "quit":
            return EditorResult(EditorOutcome.CANCELLED, document, None)
        if trans.action == "save":
            save_cb(profile_name, document)
            return EditorResult(EditorOutcome.SAVED, document, None)
        if trans.action in ("add", "rename"):
            # Task 14 wires real add/rename; the shell only collects.
            label = ("New name: " if trans.action == "rename"
                     else "New route name: ")
            name = _prompt_text(surface, label)
            state.status = f"name: {name}" if name else ""


def _best_effort(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 — best-effort terminal setup
        pass


def _read_key(surface) -> str | None:
    try:
        code = surface.getch()
    except KeyboardInterrupt:
        raise
    except Exception:  # noqa: BLE001 — degrade to skip
        return None
    special = {
        curses.KEY_UP: "up", curses.KEY_DOWN: "down",
        curses.KEY_LEFT: "left", curses.KEY_RIGHT: "right",
        curses.KEY_PPAGE: "pageup", curses.KEY_NPAGE: "pagedown",
        10: "enter", 13: "enter", 27: "esc", 32: " ", 3: "CTRL_C",
    }
    if code in special:
        return special[code]
    if 32 < code < 127:
        return chr(code)
    return None


def _render(surface, state: EditorState, doc: EditorDocument,
            raw_offset: int) -> None:
    try:
        surface.erase()
        max_y, max_x = surface.getmaxyx()
        lines = [f" Profile: {doc.name}",
                 f" Screen:  {state.screen.value}"]
        if state.screen is EditorScreen.RAW_VIEW:
            lines.append(f" Raw JSON (scroll offset {raw_offset})")
        if state.dirty:
            lines.append(" [modified]")
        if state.status:
            lines.append(f" {state.status}")
        for row, text in enumerate(lines[:max(0, max_y - 1)]):
            surface.addstr(row, 0, text[:max(0, max_x - 1)])
        surface.addstr(max_y - 1, 0,
                       " Up/Down: move  Enter: open  a: add  x: delete  "
                       "S: save  q: quit"[:max(0, max_x - 1)])
        surface.refresh()
    except Exception:  # noqa: BLE001 — never let curses.error escape
        pass


def _prompt_text(surface, prompt: str) -> str:
    """Collect one line via curses.textpad.Textbox (best-effort)."""
    try:
        import curses.textpad
        max_y, max_x = surface.getmaxyx()
        surface.addstr(max_y - 2, 0, prompt[:max(0, max_x - 1)])
        surface.refresh()
        width = max(8, max_x - len(prompt) - 1)
        win = curses.newwin(1, width, max_y - 2,
                            min(len(prompt), max(0, max_x - 2)))
        box = curses.textpad.Textbox(win)
        text = box.edit()
        curses.delwin(win)
        return text.strip()
    except Exception:  # noqa: BLE001 — best-effort input
        return ""
