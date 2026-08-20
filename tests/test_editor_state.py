"""Tests for the pure editor state machine (screens, keys, transitions).

No curses anywhere: ``handle_key`` is exercised as a pure function of
(state, key, doc).  The curses shell is covered by Task 16's PTY suite.
"""

import copy
import unittest

from opencode_config_switcher.editor import (
    EDITOR_ACTIONS,
    EditorDocument,
    EditorScreen,
    EditorState,
    StateTransition,
    handle_key,
    route_entry_count,
)

RL = EditorScreen.ROUTE_LIST
RE = EditorScreen.ROUTE_EDITOR
MF = EditorScreen.MODEL_ENTRY_FORM
SF = EditorScreen.SETTINGS_FORM
CF = EditorScreen.CONFIRM
RV = EditorScreen.RAW_VIEW

DOC = {
    "[opencode]": {
        "agents": {
            "build": {"model": "m0", "fallback_models": ["m1", "m2"]},
            "plan": {"model": "p0"},
        },
        "categories": {
            "coding": {"models": ["c0", "c1", "c2"]},
        },
    },
}

SCREEN_KEYS = ["up", "down", "left", "right", "pageup", "pagedown",
               "enter", "esc", " ", "a", "x", "R", "s", "S", "d",
               "q", "y", "n", ",", ".", "CTRL_C"]

SHELL_ACTING = {"quit", "save", "add", "rename", "confirm-yes"}


def make_doc():
    return EditorDocument("work", copy.deepcopy(DOC))


def make_state(screen, **kw):
    prev = {RE: RL, RV: RL, SF: RL, MF: RE, CF: RL}
    state = EditorState(screen=screen, field_count=4,
                        prev_screen=prev.get(screen))
    if screen is CF:
        state.confirm_pending_action = "delete-route"
    for k, v in kw.items():
        setattr(state, k, v)
    return state


# (action, screen-after) for EVERY key on EVERY screen.
TRUTH_TABLE = {
    RL: {
        "up": ("none", RL), "down": ("none", RL), "left": ("none", RL),
        "right": ("none", RL), "pageup": ("none", RL),
        "pagedown": ("none", RL), "enter": ("open-route", RE),
        "esc": ("none", RL), " ": ("none", RL), "a": ("add", RL),
        "x": ("delete", CF), "R": ("rename", RL),
        "s": ("open-settings", SF), "S": ("save", RL),
        "d": ("open-raw", RV), "q": ("quit", RL), "y": ("none", RL),
        "n": ("none", RL), ",": ("none", RL), ".": ("none", RL),
        "CTRL_C": ("quit", RL),
    },
    RE: {
        "up": ("none", RE), "down": ("none", RE), "left": ("none", RE),
        "right": ("none", RE), "pageup": ("none", RE),
        "pagedown": ("none", RE), "enter": ("open-entry", MF),
        "esc": ("close", RL), " ": ("none", RE), "a": ("open-form", MF),
        "x": ("delete", CF), "R": ("none", RE), "s": ("none", RE),
        "S": ("save", RE), "d": ("open-raw", RV), "q": ("quit", RE),
        "y": ("none", RE), "n": ("none", RE), ",": ("move-down", RE),
        ".": ("none", RE), "CTRL_C": ("quit", RE),
    },
    MF: {
        "up": ("none", MF), "down": ("none", MF),
        "left": ("cycle-left", MF), "right": ("cycle-right", MF),
        "pageup": ("none", MF), "pagedown": ("none", MF),
        "enter": ("none", MF), "esc": ("close", RE), " ": ("toggle", MF),
        "a": ("none", MF), "x": ("none", MF), "R": ("none", MF),
        "s": ("none", MF), "S": ("save", MF), "d": ("none", MF),
        "q": ("quit", MF), "y": ("none", MF), "n": ("none", MF),
        ",": ("none", MF), ".": ("none", MF), "CTRL_C": ("quit", MF),
    },
    SF: {
        "up": ("none", SF), "down": ("none", SF),
        "left": ("cycle-left", SF), "right": ("cycle-right", SF),
        "pageup": ("none", SF), "pagedown": ("none", SF),
        "enter": ("none", SF), "esc": ("close", RL), " ": ("toggle", SF),
        "a": ("none", SF), "x": ("none", SF), "R": ("none", SF),
        "s": ("none", SF), "S": ("save", SF), "d": ("none", SF),
        "q": ("quit", SF), "y": ("none", SF), "n": ("none", SF),
        ",": ("none", SF), ".": ("none", SF), "CTRL_C": ("quit", SF),
    },
    CF: {
        "up": ("none", CF), "down": ("none", CF), "left": ("none", CF),
        "right": ("none", CF), "pageup": ("none", CF),
        "pagedown": ("none", CF), "enter": ("none", CF),
        "esc": ("confirm-no", RL), " ": ("none", CF), "a": ("none", CF),
        "x": ("none", CF), "R": ("none", CF), "s": ("none", CF),
        "S": ("none", CF), "d": ("none", CF), "q": ("none", CF),
        "y": ("confirm-yes", RL), "n": ("confirm-no", RL),
        ",": ("none", CF), ".": ("none", CF), "CTRL_C": ("none", CF),
    },
    RV: {
        "up": ("none", RV), "down": ("none", RV), "left": ("none", RV),
        "right": ("none", RV), "pageup": ("none", RV),
        "pagedown": ("none", RV), "enter": ("none", RV),
        "esc": ("close", RL), " ": ("none", RV), "a": ("none", RV),
        "x": ("none", RV), "R": ("none", RV), "s": ("none", RV),
        "S": ("none", RV), "d": ("close", RL), "q": ("close", RL),
        "y": ("none", RV), "n": ("none", RV), ",": ("none", RV),
        ".": ("none", RV), "CTRL_C": ("quit", RV),
    },
}


class TruthTableTests(unittest.TestCase):
    def test_every_key_on_every_screen(self):
        for screen, rows in TRUTH_TABLE.items():
            for key, (want_action, want_screen) in rows.items():
                with self.subTest(screen=screen.value, key=repr(key)):
                    state = make_state(screen)
                    doc = make_doc()
                    trans = handle_key(state, key, doc)
                    self.assertIsInstance(trans, StateTransition)
                    self.assertEqual(trans.action, want_action)
                    self.assertEqual(state.screen, want_screen)
                    want_req = ((want_screen is not screen)
                                or (want_action in SHELL_ACTING))
                    self.assertEqual(trans.new_state_required, want_req)

    def test_action_always_in_vocabulary(self):
        for screen in EditorScreen:
            for key in SCREEN_KEYS:
                with self.subTest(screen=screen.value, key=repr(key)):
                    trans = handle_key(make_state(screen), key, make_doc())
                    self.assertIn(trans.action, EDITOR_ACTIONS)

    def test_raw_view_scroll_payloads(self):
        for key, delta in (("up", -1), ("down", 1),
                           ("pageup", -10), ("pagedown", 10)):
            with self.subTest(key=key):
                trans = handle_key(make_state(RV), key, make_doc())
                self.assertEqual((trans.action, trans.payload),
                                 ("none", delta))

    def test_unknown_keys_are_none(self):
        for screen in EditorScreen:
            for key in ("z", "Z", "\t", "Q", "\x1b"):
                with self.subTest(screen=screen.value, key=repr(key)):
                    trans = handle_key(make_state(screen), key, make_doc())
                    self.assertEqual(trans.action, "none")


class OriginStackTests(unittest.TestCase):
    def test_esc_walks_origin_stack(self):
        state = EditorState(field_count=4)
        doc = make_doc()
        self.assertEqual(handle_key(state, "enter", doc).action,
                         "open-route")
        state.entry_index = 2
        self.assertEqual(handle_key(state, "enter", doc).action,
                         "open-entry")
        self.assertIs(state.screen, MF)
        trans = handle_key(state, "esc", doc)
        self.assertEqual((trans.action, state.screen, state.entry_index),
                         ("close", RE, 2))
        trans = handle_key(state, "esc", doc)
        self.assertEqual((trans.action, state.screen), ("close", RL))
        trans = handle_key(state, "esc", doc)  # root: Esc is a no-op
        self.assertEqual((trans.action, state.screen), ("none", RL))

    def test_esc_defaults_to_route_list_when_prev_missing(self):
        state = EditorState(screen=RE)  # prev_screen=None
        trans = handle_key(state, "esc", make_doc())
        self.assertEqual((trans.action, state.screen), ("close", RL))

    def test_open_route_preserves_indices(self):
        state = make_state(RL, route_index=1, entry_index=2)
        handle_key(state, "enter", make_doc())
        self.assertEqual((state.route_index, state.entry_index), (1, 2))

    def test_open_settings_resets_field_index(self):
        state = make_state(RL, field_index=3)
        handle_key(state, "s", make_doc())
        self.assertEqual((state.screen, state.field_index), (SF, 0))

    def test_open_entry_resets_field_index(self):
        state = make_state(RE, field_index=3)
        handle_key(state, "enter", make_doc())
        self.assertEqual((state.screen, state.field_index), (MF, 0))

    def test_add_entry_sentinel_is_past_end(self):
        doc = make_doc()  # selected route 'build' has 3 entries
        state = make_state(RE, entry_index=0)
        trans = handle_key(state, "a", doc)
        self.assertEqual((trans.action, state.screen, state.entry_index,
                          state.field_index),
                         ("open-form", MF, 3, 0))


class NavigationClampTests(unittest.TestCase):
    def test_route_change_resets_entry_index_and_status(self):
        state = make_state(RL, route_index=0, entry_index=2,
                           status="hello")
        handle_key(state, "down", make_doc())
        self.assertEqual((state.route_index, state.entry_index,
                          state.status), (1, 0, ""))

    def test_route_up_clamps_at_zero(self):
        state = make_state(RL, route_index=0)
        handle_key(state, "up", make_doc())
        self.assertEqual(state.route_index, 0)

    def test_route_down_clamps_at_last(self):
        state = make_state(RL, route_index=2)  # 3 routes total
        handle_key(state, "down", make_doc())
        self.assertEqual(state.route_index, 2)

    def test_entry_up_clamps_at_zero(self):
        state = make_state(RE, entry_index=0)
        handle_key(state, "up", make_doc())
        self.assertEqual(state.entry_index, 0)

    def test_entry_down_clamps_at_last(self):
        state = make_state(RE, entry_index=2)  # 'build' has 3 entries
        handle_key(state, "down", make_doc())
        self.assertEqual(state.entry_index, 2)

    def test_field_up_clamps_at_zero(self):
        state = make_state(MF, field_index=0)
        handle_key(state, "up", make_doc())
        self.assertEqual(state.field_index, 0)

    def test_field_down_clamps_at_last(self):
        state = make_state(MF, field_index=3, field_count=4)
        handle_key(state, "down", make_doc())
        self.assertEqual(state.field_index, 3)

    def test_form_enter_advances_then_saves_on_last(self):
        state = make_state(MF, field_index=0, field_count=4)
        self.assertEqual(handle_key(state, "enter", make_doc()).action,
                         "none")
        self.assertEqual(state.field_index, 1)
        state.field_index = 3
        self.assertEqual(handle_key(state, "enter", make_doc()).action,
                         "save")


class DirtyQuitTests(unittest.TestCase):
    def test_dirty_q_opens_confirm_with_exact_status(self):
        state = make_state(RL, dirty=True)
        trans = handle_key(state, "q", make_doc())
        self.assertEqual(
            (trans.action, state.screen, state.status,
             state.confirm_pending_action),
            ("none", CF, "Unsaved changes — quit anyway? (y/n)", "quit"))

    def test_dirty_ctrl_c_same_as_q(self):
        state = make_state(RL, dirty=True)
        handle_key(state, "CTRL_C", make_doc())
        self.assertIs(state.screen, CF)

    def test_clean_q_and_ctrl_c_quit_directly(self):
        for key in ("q", "CTRL_C"):
            with self.subTest(key=key):
                state = make_state(RL, dirty=False)
                trans = handle_key(state, key, make_doc())
                self.assertEqual((trans.action, trans.new_state_required),
                                 ("quit", True))

    def test_confirm_yes_emits_quit_payload(self):
        state = make_state(RL, dirty=True)
        handle_key(state, "q", make_doc())
        trans = handle_key(state, "y", make_doc())
        self.assertEqual((trans.action, trans.payload),
                         ("confirm-yes", "quit"))
        self.assertIs(state.screen, RL)

    def test_confirm_no_returns_and_clears(self):
        state = make_state(RL, dirty=True)
        handle_key(state, "q", make_doc())
        trans = handle_key(state, "n", make_doc())
        self.assertEqual((trans.action, state.screen), ("confirm-no", RL))
        self.assertEqual((state.status, state.confirm_pending_action,
                          state.confirm_answer, state.dirty),
                         ("", "", False, True))

    def test_confirm_esc_same_as_no(self):
        state = make_state(RL, dirty=True)
        handle_key(state, "q", make_doc())
        trans = handle_key(state, "esc", make_doc())
        self.assertEqual((trans.action, state.screen), ("confirm-no", RL))

    def test_confirm_ignores_everything_else(self):
        for key in ("z", "enter", "S", "q", "x", "up", " "):
            with self.subTest(key=repr(key)):
                state = make_state(CF)  # pending delete-route
                before = copy.deepcopy(state.__dict__)
                trans = handle_key(state, key, make_doc())
                self.assertEqual((trans.action, state.screen),
                                 ("none", CF))
                self.assertEqual(state.__dict__, before)

    def test_confirm_yes_with_empty_pending_ignored(self):
        state = make_state(CF, confirm_pending_action="")
        doc = make_doc()
        before = copy.deepcopy(doc.document)
        trans = handle_key(state, "y", doc)
        self.assertEqual((trans.action, state.screen), ("none", CF))
        self.assertEqual(doc.document, before)

    def test_dirty_quit_from_form_returns_to_form(self):
        state = make_state(MF, dirty=True)
        handle_key(state, "q", make_doc())
        self.assertIs(state.screen, CF)
        handle_key(state, "n", make_doc())
        self.assertIs(state.screen, MF)


class DeleteRouteTests(unittest.TestCase):
    def test_x_shows_confirm_with_exact_status(self):
        state = make_state(RL, route_index=0)
        trans = handle_key(state, "x", make_doc())
        self.assertEqual((trans.action, state.screen, state.status,
                          state.confirm_pending_action),
                         ("delete", CF, "Delete route 'build'?",
                          "delete-route"))

    def test_confirm_yes_deletes_route_in_core(self):
        doc = make_doc()
        state = make_state(RL, route_index=0)
        handle_key(state, "x", doc)
        trans = handle_key(state, "y", doc)
        self.assertEqual((trans.action, trans.payload),
                         ("confirm-yes", "delete-route"))
        self.assertEqual(
            [(r.kind, r.name) for r in doc.routes()],
            [("agent", "plan"), ("category", "coding")])
        self.assertTrue(state.dirty)
        self.assertIs(state.screen, RL)
        self.assertEqual(state.confirm_pending_action, "")

    def test_confirm_yes_clamps_route_index(self):
        doc = make_doc()
        state = make_state(RL, route_index=2)  # 'coding' is last
        handle_key(state, "x", doc)
        handle_key(state, "y", doc)
        self.assertEqual(state.route_index, 1)

    def test_confirm_no_keeps_document(self):
        doc = make_doc()
        before = copy.deepcopy(doc.document)
        state = make_state(RL)
        handle_key(state, "x", doc)
        handle_key(state, "n", doc)
        self.assertEqual(doc.document, before)
        self.assertFalse(state.dirty)


class EntryOperationTests(unittest.TestCase):
    def test_move_down_payload_index_and_dirty(self):
        doc = make_doc()
        before = copy.deepcopy(doc.document)
        state = make_state(RE, entry_index=0)
        trans = handle_key(state, ",", doc)
        self.assertEqual((trans.action, trans.payload, state.entry_index,
                          state.dirty, trans.new_state_required),
                         ("move-down", (0, 1), 1, True, False))
        self.assertEqual(doc.document, before)  # pure-emit: no mutation

    def test_move_up_payload_index_and_dirty(self):
        state = make_state(RE, entry_index=1)
        trans = handle_key(state, ".", make_doc())
        self.assertEqual((trans.action, trans.payload, state.entry_index,
                          state.dirty),
                         ("move-up", (1, 0), 0, True))

    def test_move_at_boundary_is_none_without_dirty(self):
        doc = make_doc()  # 'build': 3 entries
        state = make_state(RE, entry_index=2)
        trans = handle_key(state, ",", doc)
        self.assertEqual((trans.action, trans.payload, state.entry_index,
                          state.dirty), ("none", None, 2, False))
        state = make_state(RE, entry_index=0)
        trans = handle_key(state, ".", doc)
        self.assertEqual((trans.action, trans.payload, state.entry_index,
                          state.dirty), ("none", None, 0, False))

    def test_x_entry_confirm_status_and_pure_emit(self):
        doc = make_doc()
        before = copy.deepcopy(doc.document)
        state = make_state(RE, entry_index=1)
        trans = handle_key(state, "x", doc)
        self.assertEqual((trans.action, state.screen, state.status,
                          state.confirm_pending_action),
                         ("delete", CF, "Delete entry 1?", "delete-entry"))
        trans = handle_key(state, "y", doc)
        self.assertEqual((trans.action, trans.payload),
                         ("confirm-yes", "delete-entry"))
        self.assertIs(state.screen, RE)
        self.assertEqual(doc.document, before)  # Task 14 does surgery


class SpaceIgnoreTests(unittest.TestCase):
    def test_space_is_none_and_inert_on_list_and_editor(self):
        for screen in (RL, RE):
            with self.subTest(screen=screen.value):
                state = make_state(screen, route_index=1, entry_index=1)
                trans = handle_key(state, " ", make_doc())
                self.assertEqual(
                    (trans.action, state.route_index, state.entry_index,
                     state.dirty), ("none", 1, 1, False))


class EmptyDocumentTests(unittest.TestCase):
    def test_routeless_document_inert(self):
        doc = EditorDocument("empty", {})
        for key in ("enter", "x", "R", "up", "down"):
            with self.subTest(key=key):
                state = make_state(RL)
                trans = handle_key(state, key, doc)
                self.assertEqual((trans.action, state.screen),
                                 ("none", RL))

    def test_add_still_emits_on_empty_document(self):
        state = make_state(RL)
        trans = handle_key(state, "a", EditorDocument("empty", {}))
        self.assertEqual(trans.action, "add")

    def test_entryless_route_survives_keys(self):
        doc = EditorDocument("bare", {"[opencode]": {
            "agents": {"only": {}}}})   # no model, no fallbacks: 0 entries
        state = make_state(RE, entry_index=5)   # stale past-end index
        for key in (",", ".", "x", "enter", "down"):
            with self.subTest(key=key):
                trans = handle_key(state, key, doc)
                self.assertEqual(trans.action, "none")
        self.assertFalse(state.dirty)


class FuzzTests(unittest.TestCase):
    def test_all_keys_all_screens_never_raise(self):
        docs = [
            make_doc(),
            EditorDocument("empty", {}),
            EditorDocument("weird", {"[opencode]": {
                "agents": {"a": {"models": "oops"}},
                "categories": {"c": {}},
            }}),
        ]
        keys = SCREEN_KEYS + ["z", "Z", "\t", "\x1b", "Q"]
        checked = 0
        for base in docs:
            for screen in EditorScreen:
                for dirty in (False, True):
                    for pending in ("", "quit", "delete-route",
                                    "delete-entry"):
                        for key in keys:
                            doc = EditorDocument(
                                base.name, copy.deepcopy(base.document))
                            state = make_state(screen, dirty=dirty)
                            state.confirm_pending_action = pending
                            trans = handle_key(state, key, doc)
                            self.assertIsInstance(trans, StateTransition)
                            self.assertIn(trans.action, EDITOR_ACTIONS)
                            checked += 1
        self.assertGreater(checked, 3000)


class DocumentModelTests(unittest.TestCase):
    def test_routes_agents_then_categories_in_insertion_order(self):
        self.assertEqual(
            [(r.kind, r.name) for r in make_doc().routes()],
            [("agent", "build"), ("agent", "plan"), ("category", "coding")])

    def test_routes_missing_sections(self):
        doc = EditorDocument("x", {"[opencode]": {
            "agents": {"a": {"model": "m"}}}})
        self.assertEqual([r.name for r in doc.routes()], ["a"])
        self.assertEqual(EditorDocument("x", {"[opencode]": {}}).routes(),
                         [])

    def test_routes_missing_or_malformed_harness(self):
        self.assertEqual(EditorDocument("x", {}).routes(), [])
        self.assertEqual(
            EditorDocument("x", {"[opencode]": "nope"}).routes(), [])
        self.assertEqual(EditorDocument("x", {"other": {}}).routes(), [])

    def test_routes_skips_non_dict_blocks(self):
        doc = EditorDocument("x", {"[opencode]": {
            "agents": {"bad": "str", "good": {"model": "m"}},
            "categories": {"c": 5},
        }})
        self.assertEqual([r.name for r in doc.routes()], ["good"])

    def test_models_list_len_semantics(self):
        routes = {r.name: r for r in make_doc().routes()}
        self.assertIsNone(routes["build"].models_list_len)
        self.assertEqual(routes["coding"].models_list_len, 3)
        doc = EditorDocument("x", {"[opencode]": {
            "categories": {"c": {"models": "not-a-list"}}}})
        self.assertEqual(doc.routes()[0].models_list_len, 0)

    def test_route_entry_count(self):
        routes = {r.name: r for r in make_doc().routes()}
        self.assertEqual(route_entry_count(routes["build"]), 3)
        self.assertEqual(route_entry_count(routes["plan"]), 1)
        self.assertEqual(route_entry_count(routes["coding"]), 3)
        self.assertEqual(route_entry_count(None), 0)
        doc = EditorDocument("x", {"[opencode]": {
            "agents": {
                "nofb": {"fallback_models": ["a", "b"]},
                "badfb": {"model": "m", "fallback_models": "oops"},
            },
            "categories": {"nomodels": {}},
        }})
        odd = {r.name: r for r in doc.routes()}
        self.assertEqual(route_entry_count(odd["nofb"]), 2)
        self.assertEqual(route_entry_count(odd["badfb"]), 1)
        self.assertEqual(route_entry_count(odd["nomodels"]), 0)

    def test_route_entry_count_agent_canonical_models(self):
        doc = EditorDocument("x", {"[opencode]": {"agents": {
            "can": {"models": ["a", "b"]},
            "empty": {"models": []},
            "mixed": {"model": "m", "fallback_models": ["f"],
                      "models": ["c0", "c1", "c2"]},
            "malformed": {"models": "oops", "model": "m"},
        }}})
        routes = {r.name: r for r in doc.routes()}
        self.assertEqual(route_entry_count(routes["can"]), 2)
        self.assertEqual(route_entry_count(routes["empty"]), 0)
        self.assertEqual(route_entry_count(routes["mixed"]), 3)
        self.assertEqual(route_entry_count(routes["malformed"]), 1)


class ContractTests(unittest.TestCase):
    def test_state_transition_shape(self):
        self.assertEqual(StateTransition._fields,
                         ("new_state_required", "action", "payload"))

    def test_screen_members(self):
        self.assertEqual(
            [s.value for s in EditorScreen],
            ["ROUTE_LIST", "ROUTE_EDITOR", "MODEL_ENTRY_FORM",
             "SETTINGS_FORM", "CONFIRM", "RAW_VIEW"])

    def test_state_defaults(self):
        state = EditorState()
        self.assertIs(state.screen, RL)
        self.assertIsNone(state.prev_screen)
        self.assertEqual(
            (state.route_index, state.entry_index, state.field_index),
            (0, 0, 0))
        self.assertEqual((state.dirty, state.status,
                          state.confirm_pending_action,
                          state.confirm_answer, state.field_count),
                         (False, "", "", None, 1))

    def test_state_is_mutable_and_document_names_exposed(self):
        state = EditorState()
        state.dirty = True
        doc = make_doc()
        self.assertEqual((doc.name, type(doc.document).__name__),
                         ("work", "dict"))


if __name__ == "__main__":
    unittest.main()
