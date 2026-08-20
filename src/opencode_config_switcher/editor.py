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

Chain surgery layer (Task 14, binding for Tasks 15/16):
    ``chain_entries``/``write_chain`` translate between a route block
    and an ordered entry chain: category → its ``models`` list; agent →
    ``models`` when the block carries a canonical chain, else the
    legacy composition ``model`` (index 0) + ``fallback_models``
    (indices 1+) so legacy profiles remain editable.  Agent writes are
    ALWAYS canonical: ``write_chain`` stores ``block["models"]``,
    folds the definition-level settings keys (``reasoning``,
    ``provider_options`` …) into a STRING entry 0 and removes them
    from the block together with the legacy ``model`` /
    ``fallback_models`` keys, and applies the Task 5 observed-pair
    collapse — an agent model ref that is a dict whose ONLY key is
    ``model`` collapses to the bare string; category
    dict entries NEVER collapse.  ``add_route``/``rename_route``/
    ``delete_route_by_name``/``move_chain_entry``/``remove_chain_entry``
    /``set_entry`` mutate the document and report via ``OperationResult``
    without touching ``EditorState`` (the shell marks dirty).
    ``apply_transition`` is that shell glue for a ``handle_key``
    transition: it performs the deferred move/delete-entry surgery,
    marks dirty where the core could not (delete-entry — moves already
    set it in-core), clamps ``entry_index`` after removal, and returns
    a ``ShellPrompt`` for "add" (None otherwise).      Removing an
    agent's primary promotes fallback[0] via write-back; an empty
    agent chain removes the chain keys.

Forms layer (Task 15, binding for Task 16):
    ``FormField``/``FormState`` model the two FORM screens as PURE
    state (no curses).  ``build_entry_form``/``build_settings_form``
    prefill from a chain entry / the ``[opencode]`` harness block,
    stashing every non-editable key in ``extra_preserved`` (merged
    back verbatim on save).  ``validate_and_collect`` runs the Task
    13 parsers (kind-specific messages; blank numerics are ABSENT),
    ``apply_entry_form``/``apply_settings_form`` write through the
    Task 14 surgery, and ``handle_form_key`` is the key glue the
    shell routes through ("move"/"cycle"/"toggle"/"none").
"""
# allow: SIZE_OK — plan-pinned single-module layout (Tasks 14/15/16
# extend this file): pure core + thin shell, mirroring tui.py.

import curses
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, NamedTuple

from opencode_config_switcher.transform import _DEFINITION_SETTINGS_KEYS


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
    len(models) for a canonical chain, else (1 if 'model' present) +
    len(fallback_models list).  Malformed or missing pieces count
    as 0."""
    if item is None:
        return 0
    if item.kind == "category":
        models = item.block.get("models")
        return len(models) if isinstance(models, list) else 0
    models = item.block.get("models")
    if isinstance(models, list):
        return len(models)
    count = 1 if "model" in item.block else 0
    fallbacks = item.block.get("fallback_models")
    if isinstance(fallbacks, list):
        count += len(fallbacks)
    return count


# ── chain surgery (Task 14) ────────────────────────────────────────
#
# The PURE-EMIT boundary above defers all entry-level document work to
# this layer.  The operations mutate the document and report via
# ``OperationResult`` — they never touch ``EditorState``; marking
# dirty is the shell's job (via ``apply_transition`` or directly).


class OperationResult(NamedTuple):
    ok: bool
    message: str


_SECTION_FOR_KIND = {"agent": "agents", "category": "categories"}


def chain_entries(item: RouteItem) -> list:
    """Ordered chain of one route as LIVE entry references inside a
    fresh list: category → its ``models`` list; agent → ``models``
    when the block carries a canonical chain, else the legacy
    composition ``model`` (when the key exists) followed by
    ``fallback_models`` (when a list) so legacy profiles remain
    editable.  Malformed pieces read as absent (≙
    ``route_entry_count``)."""
    if item.kind == "category":
        models = item.block.get("models")
        return list(models) if isinstance(models, list) else []
    models = item.block.get("models")
    if isinstance(models, list):
        return list(models)
    entries: list = []
    if "model" in item.block:
        entries.append(item.block["model"])
    fallbacks = item.block.get("fallback_models")
    if isinstance(fallbacks, list):
        entries.extend(fallbacks)
    return entries


def _collapse_agent_entry(entry):
    """Task 5 observed-pair rule: an agent model ref that is a dict
    whose ONLY key is ``model`` collapses to the bare model string."""
    if isinstance(entry, dict) and set(entry) == {"model"}:
        return entry["model"]
    return entry


def write_chain(item: RouteItem, entries: list) -> None:
    """Write a full chain back into ``item.block`` in place.

    Category: ``block["models"] = entries`` — dict entries never
    collapse (Task 5 asymmetry); an absent ``models`` key is not
    invented for an empty write.  Agent: ALWAYS canonical —
    ``block["models"] = entries`` with ``_collapse_agent_entry``
    applied to every entry, the legacy ``model`` / ``fallback_models``
    keys removed, and the definition-level settings keys
    (``_DEFINITION_SETTINGS_KEYS``, task-1 parity) folded into a
    STRING entry 0 and then removed from the block (settings next to
    a dict entry 0 or an empty chain stay in place, as today).
    Every other block key (reasoning on categories, description,
    tools…) is preserved untouched; the input list is never mutated.
    """
    block = item.block
    if item.kind == "category":
        if entries or "models" in block:
            block["models"] = entries
        return
    chain = list(entries)
    settings = {key: block[key] for key in _DEFINITION_SETTINGS_KEYS
                if key in block}
    folded: list[str] = []
    if chain and settings and isinstance(chain[0], str):
        chain[0] = {"model": chain[0], **settings}
        folded = list(settings)
    if chain:
        block["models"] = [_collapse_agent_entry(entry)
                           for entry in chain]
    else:
        block.pop("models", None)
    block.pop("model", None)
    block.pop("fallback_models", None)
    for key in folded:
        block.pop(key, None)


def add_route(doc: EditorDocument, kind: str, name: str) -> OperationResult:
    """Create the route block under the kind's section map — agents
    start canonical with ``{"models": []}``, categories with ``{}`` —
    creating the map (and the ``[opencode]`` harness) when absent."""
    section_name = _SECTION_FOR_KIND.get(kind)
    if section_name is None:
        return OperationResult(False, f"Unknown route kind: {kind}")
    if not name:
        return OperationResult(False, "Route name must not be empty")
    harness = doc.document.setdefault("[opencode]", {})
    if not isinstance(harness, dict):
        return OperationResult(
            False, "Cannot add route: '[opencode]' is not an object")
    section = harness.setdefault(section_name, {})
    if not isinstance(section, dict):
        return OperationResult(
            False, f"Cannot add route: '{section_name}' is not an object")
    if name in section:
        return OperationResult(False, f"Route '{name}' already exists")
    section[name] = {"models": []} if kind == "agent" else {}
    return OperationResult(True, f"Route added: {name}")


def rename_route(doc: EditorDocument, kind: str, old: str,
                 new: str) -> OperationResult:
    """Rename preserving the route's insertion position (the section
    dict is rebuilt with the new key at the old slot, same block)."""
    section = _route_section(doc, kind)
    if section is None:
        return OperationResult(False, f"Route '{old}' not found")
    if not new:
        return OperationResult(False, "Route name must not be empty")
    if old not in section:
        return OperationResult(False, f"Route '{old}' not found")
    if new in section:
        return OperationResult(False, f"Route '{new}' already exists")
    rebuilt = {(new if key == old else key): value
               for key, value in section.items()}
    section.clear()
    section.update(rebuilt)
    return OperationResult(True, f"Route renamed: {old} -> {new}")


def delete_route_by_name(doc: EditorDocument, kind: str,
                         name: str) -> OperationResult:
    section = _route_section(doc, kind)
    if section is None or name not in section:
        return OperationResult(False, f"Route '{name}' not found")
    del section[name]
    return OperationResult(True, f"Route deleted: {name}")


def _route_section(doc: EditorDocument, kind: str) -> dict | None:
    """The kind's section map, or None when anything along the path is
    missing or malformed (malformed ≙ absent)."""
    section_name = _SECTION_FOR_KIND.get(kind)
    if section_name is None:
        return None
    harness = doc.document.get("[opencode]") \
        if isinstance(doc.document, dict) else None
    if not isinstance(harness, dict):
        return None
    section = harness.get(section_name)
    return section if isinstance(section, dict) else None


def move_chain_entry(item: RouteItem, from_index: int,
                     to_index: int) -> OperationResult:
    """Bounds-checked remove+insert at the target (adjacent or
    arbitrary); ``from == to`` is an ok TRUE no-op (no rewrite)."""
    entries = chain_entries(item)
    if not 0 <= from_index < len(entries):
        return OperationResult(False, f"Invalid entry index: {from_index}")
    if not 0 <= to_index < len(entries):
        return OperationResult(False, f"Invalid entry index: {to_index}")
    if from_index != to_index:
        entries.insert(to_index, entries.pop(from_index))
        write_chain(item, entries)
    return OperationResult(True, "Entry moved")


def remove_chain_entry(item: RouteItem, index: int) -> OperationResult:
    """Remove one entry.  Agent index 0 removal is PRIMARY PROMOTION:
    the write-back re-splits the chain, so fallback[0] becomes
    ``model`` (collapsing per the observed-pair rule) and an emptied
    chain removes both keys."""
    entries = chain_entries(item)
    if not 0 <= index < len(entries):
        return OperationResult(False, f"Invalid entry index: {index}")
    del entries[index]
    write_chain(item, entries)
    return OperationResult(True, "Entry removed")


def set_entry(item: RouteItem, index: int, entry) -> OperationResult:
    """Replace the entry at *index*; ``index == len(chain)`` is the
    past-end APPEND sentinel (Task 13's ``a`` key).  The agent collapse
    rule applies on write; category entries keep their dict form."""
    entries = chain_entries(item)
    if not 0 <= index <= len(entries):
        return OperationResult(False, f"Invalid entry index: {index}")
    if index == len(entries):
        entries.append(entry)
    else:
        entries[index] = entry
    write_chain(item, entries)
    return OperationResult(True, "Entry saved")


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


# ── shell glue (Task 14) ───────────────────────────────────────────
#
# ``apply_transition`` performs the shell-side document work the PURE
# core deferred (move payloads, delete-entry confirm) and hands the
# shell a prompt spec for actions needing a Textbox ("add").  It never
# duplicates in-core work: route deletion already happened inside
# ``handle_key``, and moves were already flagged dirty there.


class ShellPrompt(NamedTuple):
    kind: str       # prompt flow, e.g. "route-name"
    prompt: str     # Textbox label for the shell to render
    target: str     # what the collected name targets ("agent"|"category")


def apply_transition(doc: EditorDocument, state: EditorState,
                     transition: StateTransition) -> ShellPrompt | None:
    """Shell-side work for one ``handle_key`` transition: move
    payloads → ``move_chain_entry``; confirm-yes "delete-entry" →
    ``remove_chain_entry`` (+dirty, +entry_index clamp); "add" → a
    ``ShellPrompt`` naming the Textbox to open (kind follows the
    selected route, defaulting to agent).  None for everything else."""
    action, payload = transition.action, transition.payload
    if action in ("move-up", "move-down"):
        if isinstance(payload, tuple) and len(payload) == 2:
            item = _selected_route(doc, state)
            if item is not None:
                move_chain_entry(item, payload[0], payload[1])
        return None
    if action == "confirm-yes" and payload == "delete-entry":
        item = _selected_route(doc, state)
        if item is not None \
                and remove_chain_entry(item, state.entry_index).ok:
            state.dirty = True   # the core could not set it here
            state.entry_index = _clamp_index(
                state.entry_index, route_entry_count(item))
        return None
    if action == "add":
        routes = doc.routes()
        kind = routes[state.route_index].kind \
            if 0 <= state.route_index < len(routes) else "agent"
        return ShellPrompt("route-name", "New route name: ", kind)
    return None


def _selected_route(doc: EditorDocument,
                    state: EditorState) -> RouteItem | None:
    routes = doc.routes()
    if 0 <= state.route_index < len(routes):
        return routes[state.route_index]
    return None


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


# ── forms layer (Task 15) ───────────────────────────────────────────
#
# PURE form state for the two FORM screens.  Task 16 renders
# ``FormState`` in the curses shell and routes form keys through
# ``handle_form_key``; the apply_* functions perform the document
# work (entry via Task 14 ``set_entry``; settings in place on the
# harness block) and report via ``OperationResult``.

# Entry keys the form edits; every other key lands in extra_preserved
# (unknown keys AND known-but-uneditable ones: provider_options,
# thinking, textVerbosity, variant, reasoningEffort, maxTokens,
# providerOptions — shown as read-only info lines by Task 16).
_ENTRY_EDITABLE = frozenset(
    {"model", "reasoning", "temperature", "top_p", "max_tokens"})

# Settings keys that rebuild block["runtime_fallback"] on apply.
_RUNTIME_FIELDS = ("enabled", "retry_on_errors", "max_fallback_attempts",
                   "cooldown_seconds")


class FormField(NamedTuple):
    name: str      # form-field grammar: entry → model/reasoning/
    #               temperature/top_p/max_tokens; settings →
    #               model_fallback/enabled/retry_on_errors/
    #               max_fallback_attempts/cooldown_seconds.
    kind: str      # "text" | "number" | "toggle" | "enum"
    value: object  # text: str · number: str|None (None ≙ blank) ·
    #               toggle: bool|None (None ≙ unset) · enum: str|None
    extra: dict    # number: {"number_kind": parse_number kind} ·
    #               enum: {"choices": REASONING_CYCLE}


@dataclass
class FormState:
    fields: list[FormField]
    cursor: int = 0
    error: str = ""                      # last apply/validation failure
    extra_preserved: dict = field(default_factory=dict)


def _number_text(value) -> str | None:
    """Editable text for a number field; None/blank ≙ None (absent)."""
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def build_entry_form(entry: dict | str) -> FormState:
    """MODEL_ENTRY_FORM state from one chain entry.

    String entry → model text + everything blank.  Dict entry →
    model/reasoning/numbers prefilled (absent → None ≙ blank; custom
    reasoning kept raw — ``format_reasoning_custom`` is display
    only).  ``extra_preserved`` holds every key the form does NOT
    edit, merged back verbatim on save.
    """
    if isinstance(entry, dict):
        model = entry.get("model")
        reasoning = entry.get("reasoning")
        source = entry
    else:
        model = entry
        reasoning = None
        source = {}
    fields = [
        FormField("model", "text", "" if model is None else str(model),
                  {}),
        FormField("reasoning", "enum", reasoning,
                  {"choices": REASONING_CYCLE}),
        FormField("temperature", "number",
                  _number_text(source.get("temperature")),
                  {"number_kind": "temperature"}),
        FormField("top_p", "number", _number_text(source.get("top_p")),
                  {"number_kind": "top_p"}),
        FormField("max_tokens", "number",
                  _number_text(source.get("max_tokens")),
                  {"number_kind": "max_tokens"}),
    ]
    extra = {key: value for key, value in source.items()
             if key not in _ENTRY_EDITABLE}
    return FormState(fields, 0, "", extra)


def build_settings_form(harness_block: dict) -> FormState:
    """SETTINGS_FORM state from the ``[opencode]`` harness block.

    model_fallback toggle from the block; enabled/retry_on_errors/
    max_fallback_attempts/cooldown_seconds from
    ``block["runtime_fallback"]`` when it is a dict, else blank.
    ``extra_preserved`` holds every other harness key (agents,
    categories, …) — merged back verbatim on save.
    """
    runtime = harness_block.get("runtime_fallback")
    runtime = runtime if isinstance(runtime, dict) else {}
    retry = runtime.get("retry_on_errors")
    retry_text = ", ".join(str(v) for v in retry) \
        if isinstance(retry, list) else ""
    fields = [
        FormField("model_fallback", "toggle",
                  harness_block.get("model_fallback"), {}),
        FormField("enabled", "toggle", runtime.get("enabled"), {}),
        FormField("retry_on_errors", "text", retry_text, {}),
        FormField("max_fallback_attempts", "number",
                  _number_text(runtime.get("max_fallback_attempts")),
                  {"number_kind": "max_fallback_attempts"}),
        FormField("cooldown_seconds", "number",
                  _number_text(runtime.get("cooldown_seconds")),
                  {"number_kind": "cooldown_seconds"}),
    ]
    extra = {key: value for key, value in harness_block.items()
             if key not in ("model_fallback", "runtime_fallback")}
    return FormState(fields, 0, "", extra)


def validate_and_collect(form: FormState) -> tuple[dict | None,
                                                    str | None]:
    """Validate a form and collect its present values.

    Returns ``(collected, None)`` or ``(None, error_message)``.  The
    collected dict contains ONLY present values — blank numerics and
    unset toggles/enums are ABSENT.  Entry form (has a ``model``
    field): blank model → ``Model ID must not be empty``; numbers via
    ``parse_number`` (kind-specific messages); reasoning None →
    absent, in-cycle → string, custom → preserved raw.  Settings
    form: retry via ``parse_retry_errors``.
    """
    names = {f.name for f in form.fields}
    if "model" in names:
        return _collect_entry_form(form)
    return _collect_settings_form(form)


def _field_text(value) -> str:
    return "" if value is None else str(value)


def _collect_entry_form(form: FormState) -> tuple[dict | None, str | None]:
    by_name = {f.name: f for f in form.fields}
    model = by_name.get("model")
    model_text = _field_text(model.value) if model is not None else ""
    if not model_text.strip():
        return None, "Model ID must not be empty"
    collected: dict = {"model": model_text}
    reasoning = by_name.get("reasoning")
    if reasoning is not None and reasoning.value is not None:
        collected["reasoning"] = reasoning.value
    error = _collect_numbers(form, collected)
    if error is not None:
        return None, error
    return collected, None


def _collect_settings_form(form: FormState) -> tuple[dict | None,
                                                     str | None]:
    by_name = {f.name: f for f in form.fields}
    collected: dict = {}
    for name in ("model_fallback", "enabled"):
        f = by_name.get(name)
        if f is not None and f.value is not None:   # False counts as set
            collected[name] = f.value
    retry = by_name.get("retry_on_errors")
    if retry is not None:
        try:
            values = parse_retry_errors(_field_text(retry.value))
        except FieldError as exc:
            return None, str(exc)
        if values is not None:
            collected["retry_on_errors"] = values
    error = _collect_numbers(form, collected)
    if error is not None:
        return None, error
    return collected, None


def _collect_numbers(form: FormState, collected: dict) -> str | None:
    """Parse every number field; blank → absent.  Error message or
    None."""
    for f in form.fields:
        if f.kind != "number":
            continue
        try:
            value = parse_number(
                _field_text(f.value),
                kind=f.extra.get("number_kind", f.name))
        except FieldError as exc:
            return str(exc)
        if value is not None:
            collected[f.name] = value
    return None


def apply_entry_form(item: RouteItem, index: int,
                     form: FormState) -> OperationResult:
    """Validate + save the MODEL_ENTRY_FORM into the chain.

    Validation failure → ``(False, error)`` with the form kept open
    (``form.error`` set) and NO document write.  Success → collected
    values merged with ``extra_preserved`` (verbatim) into Task 14
    ``set_entry`` — agent collapse rules apply; ``index == len(chain)``
    is the past-end append sentinel.
    """
    collected, error = validate_and_collect(form)
    if collected is None:
        form.error = error or ""
        return OperationResult(False, form.error)
    result = set_entry(item, index, {**collected, **form.extra_preserved})
    form.error = "" if result.ok else result.message
    return result


def apply_settings_form(harness_block: dict,
                        form: FormState) -> OperationResult:
    """Validate + save the SETTINGS_FORM onto the harness block.

    ``model_fallback`` written only when the toggle is not None
    (None ≙ key removed).  ``runtime_fallback`` rebuilt from the set
    sub-fields ONLY when ≥1 is set (enabled bool counts); all
    blank/None → the key is removed entirely.  Blank numerics → key
    absent.  Every other harness key passes through untouched.
    """
    collected, error = validate_and_collect(form)
    if collected is None:
        form.error = error or ""
        return OperationResult(False, form.error)
    if "model_fallback" in collected:
        harness_block["model_fallback"] = collected["model_fallback"]
    else:
        harness_block.pop("model_fallback", None)
    runtime = {key: value for key, value in collected.items()
               if key in _RUNTIME_FIELDS}
    if runtime:
        harness_block["runtime_fallback"] = runtime
    else:
        harness_block.pop("runtime_fallback", None)
    for key, value in form.extra_preserved.items():
        harness_block[key] = value
    form.error = ""
    return OperationResult(True, "Settings saved")


def _cycle_toggle(value) -> bool | None:
    """True → False → None (unset) → True; anything else → True."""
    if value is True:
        return False
    if value is False:
        return None
    return True


def handle_form_key(form: FormState, key: str) -> str:
    """Pure key glue for a FormState (Task 16 routes through this).

    up/down → move cursor (clamped) → "move"; left/right on an enum
    field → ``cycle_reasoning`` ±1 updates the field → "cycle";
    space on a toggle field → True→False→None→True → "toggle"; every
    other key → "none".
    """
    if key == "up":
        if form.cursor > 0:
            form.cursor -= 1
        return "move"
    if key == "down":
        if form.cursor < len(form.fields) - 1:
            form.cursor += 1
        return "move"
    current = form.fields[form.cursor] if form.fields else None
    if current is not None and current.kind == "enum" \
            and key in ("left", "right"):
        direction = 1 if key == "right" else -1
        form.fields[form.cursor] = current._replace(
            value=cycle_reasoning(current.value, direction))
        return "cycle"
    if current is not None and current.kind == "toggle" and key == " ":
        form.fields[form.cursor] = current._replace(
            value=_cycle_toggle(current.value))
        return "toggle"
    return "none"


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
    form: FormState | None = None
    _best_effort(lambda: surface.keypad(True))
    _best_effort(lambda: curses.curs_set(0))
    raw_offset = 0
    while True:
        _render(surface, state, doc, raw_offset, form)
        key = _read_key(surface)
        if key is None:
            continue
        if state.screen in (EditorScreen.MODEL_ENTRY_FORM,
                            EditorScreen.SETTINGS_FORM) and form:
            trans = _form_transition(state, doc, form, key)
        else:
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
        if trans.action == "save" and form is not None \
                and state.screen in (EditorScreen.MODEL_ENTRY_FORM,
                                     EditorScreen.SETTINGS_FORM):
            if _apply_form(state, doc, form):
                form = None
        elif trans.action == "save":
            save_cb(profile_name, document)
            return EditorResult(EditorOutcome.SAVED, document, None)
        else:
            prompt = apply_transition(doc, state, trans)
            if trans.action in ("open-entry", "open-form",
                                "open-settings"):
                form = _build_form(doc, state, trans.action)
            elif trans.action in ("add", "rename"):
                _shell_add_or_rename(surface, doc, state, trans, prompt)
        # Keep the form across CONFIRM (a declined dirty-quit returns
        # to it); drop it on every other non-form screen.
        if state.screen not in (EditorScreen.MODEL_ENTRY_FORM,
                                EditorScreen.SETTINGS_FORM,
                                EditorScreen.CONFIRM):
            form = None


def _build_form(doc: EditorDocument, state: EditorState,
                action: str) -> FormState:
    """FormState for one freshly opened form screen."""
    if action == "open-settings":
        harness = doc.document.get("[opencode]") \
            if isinstance(doc.document, dict) else None
        return build_settings_form(harness if isinstance(harness, dict)
                                   else {})
    item = _selected_route(doc, state)
    if action == "open-form" or item is None:
        return build_entry_form("")
    entries = chain_entries(item)
    entry = entries[state.entry_index] \
        if 0 <= state.entry_index < len(entries) else ""
    return build_entry_form(entry)


def _form_transition(state: EditorState, doc: EditorDocument,
                     form: FormState, key: str) -> StateTransition:
    """Route one key on an open form; see the forms-layer docstring.

    q/CTRL_C/Esc/Enter/S go through the PURE core (global quit /
    close / advance-or-save); printable and backspace keys edit the
    field under the FormState cursor (text/number kinds); everything
    else rides Task 15's ``handle_form_key`` (move/toggle/cycle).
    """
    if key in ("q", "CTRL_C", "esc"):
        return handle_key(state, key, doc)
    if key in ("enter", "S"):
        state.field_count = len(form.fields)
        state.field_index = (form.cursor if key == "enter"
                             else len(form.fields) - 1)
        trans = handle_key(state, key, doc)
        if trans.action != "save":
            form.cursor = state.field_index
        return trans
    if key == "backspace":
        field = form.fields[form.cursor]
        if field.kind in ("text", "number"):
            text = "" if field.value is None else str(field.value)
            form.fields[form.cursor] = field._replace(
                value=text[:-1] or None)
        return StateTransition(False, "none", None)
    if len(key) == 1 and key != " ":
        field = form.fields[form.cursor]
        if field.kind in ("text", "number"):
            text = "" if field.value is None else str(field.value)
            form.fields[form.cursor] = field._replace(value=text + key)
        return StateTransition(False, "none", None)
    handle_form_key(form, key)
    return StateTransition(False, "none", None)


def _apply_form(state: EditorState, doc: EditorDocument,
                form: FormState) -> bool:
    """Validate + apply the open form; True when it closed."""
    if any(field.name == "model" for field in form.fields):
        item = _selected_route(doc, state)
        if item is None:
            form.error = "No route selected"
            state.status = form.error
            return False
        result = apply_entry_form(item, state.entry_index, form)
    else:
        if not isinstance(doc.document, dict):
            form.error = "Document is not an object"
            state.status = form.error
            return False
        harness = doc.document.setdefault("[opencode]", {})
        if not isinstance(harness, dict):
            form.error = "'[opencode]' is not an object"
            state.status = form.error
            return False
        result = apply_settings_form(harness, form)
    state.status = result.message
    if not result.ok:
        return False
    state.dirty = True
    _back_to_prev(state)
    return True


def _shell_add_or_rename(surface, doc: EditorDocument,
                         state: EditorState,
                         trans: StateTransition,
                         prompt: "ShellPrompt | None") -> None:
    """Collect the name for add/rename and run the Task 14 surgery."""
    label = (prompt.prompt if prompt is not None
             else "New name: " if trans.action == "rename"
             else "New route name: ")
    name = _prompt_text(surface, label)
    if trans.action == "add":
        target = prompt.target if prompt is not None else "agent"
        result = add_route(doc, target, name)
    else:
        item = _selected_route(doc, state)
        if item is None or not name:
            state.status = "Route name must not be empty" \
                if not name else "No route selected"
            return
        result = rename_route(doc, item.kind, item.name, name)
    state.status = result.message
    if result.ok:
        state.dirty = True


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
        127: "backspace", curses.KEY_BACKSPACE: "backspace",
    }
    if code in special:
        return special[code]
    if 32 < code < 127:
        return chr(code)
    return None


def _entry_display(entry) -> str:
    """One chain entry rendered for the ROUTE_EDITOR list."""
    if isinstance(entry, dict):
        model = entry.get("model")
        suffix = "" if len(entry) <= 1 else f" (+{len(entry) - 1} fields)"
        return f"{model}{suffix}"
    return str(entry)


def _render(surface, state: EditorState, doc: EditorDocument,
            raw_offset: int, form: "FormState | None" = None) -> None:
    try:
        import json as _json

        surface.erase()
        max_y, max_x = surface.getmaxyx()
        lines: list[tuple[str, int]] = []
        bold = 0
        head = [
            (f" Profile: {doc.name}", 0),
            (f" Screen:  {state.screen.value}", 0),
        ]
        if state.dirty:
            head.append((" [modified]", bold))
        if state.status:
            head.append((f" {state.status}", 0))
        lines.extend(head)

        if state.screen is EditorScreen.RAW_VIEW:
            lines.append((f" Raw JSON (scroll offset {raw_offset})", 0))
            raw_lines = _json.dumps(doc.document, indent=2,
                                    ensure_ascii=False).splitlines()
            for raw_line in raw_lines[raw_offset:]:
                lines.append((f" {raw_line}", 0))
        elif state.screen is EditorScreen.ROUTE_LIST:
            for index, item in enumerate(doc.routes()):
                count = (item.models_list_len if item.kind == "category"
                         else route_entry_count(item))
                mark = ">" if index == state.route_index else " "
                lines.append((f" {mark} {item.kind} {item.name} "
                              f"({count} entries)", 0))
            if not doc.routes():
                lines.append((" (no routes — a to add)", 0))
        elif state.screen is EditorScreen.ROUTE_EDITOR:
            item = _selected_route(doc, state)
            if item is not None:
                lines.append((f" Route: {item.name} ({item.kind})", bold))
                for index, entry in enumerate(chain_entries(item)):
                    mark = ">" if index == state.entry_index else " "
                    lines.append((f"   {mark} {index}. "
                                  f"{_entry_display(entry)}", 0))
        elif form is not None:
            for index, field in enumerate(form.fields):
                mark = ">" if index == form.cursor else " "
                lines.append((f" {mark} {field.name}: "
                              f"{'' if field.value is None else field.value}",
                              0))
            extras = ", ".join(form.extra_preserved) or "none"
            lines.append((f" preserved keys: {extras}", 0))
            if form.error:
                lines.append((f" ! {form.error}", bold))

        for row, (text, attr) in enumerate(lines[:max(0, max_y - 1)]):
            surface.addstr(row, 0, text[:max(0, max_x - 1)], attr)
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
