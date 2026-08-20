"""Tests for the Task 14 chain-surgery layer (routes + entries).

Locks the PURE-EMIT boundary's deferred work: ``chain_entries`` /
``write_chain`` — agents read canonical ``models`` lists and legacy
``model``+``fallback_models`` compositions, but ALWAYS write
canonical ``models`` (definition settings folded into a string entry
0; Task 5 observed-pair collapse: agent ``{model}``-only dicts
collapse to strings, category dicts never do) — route add/rename/
delete operations, entry move/remove/set with agent primary
promotion, and the shell glue ``apply_transition`` /
``ShellPrompt``.  The killer tests live on the ground-truth fixture:
one round trip migrates every legacy agent to ``models``; a second
round trip on the migrated document is a no-op.
"""

import copy
import json
import unittest
from pathlib import Path

from opencode_config_switcher.editor import (
    EditorDocument,
    EditorScreen,
    EditorState,
    OperationResult,
    RouteItem,
    ShellPrompt,
    StateTransition,
    add_route,
    apply_transition,
    chain_entries,
    delete_route_by_name,
    handle_key,
    move_chain_entry,
    remove_chain_entry,
    rename_route,
    route_entry_count,
    set_entry,
    write_chain,
)

from opencode_config_switcher.transform import transform_legacy

FIXTURE = (Path(__file__).resolve().parent / "fixtures"
           / "groundtruth_migrated.json")

RL = EditorScreen.ROUTE_LIST
RE = EditorScreen.ROUTE_EDITOR

# Hand fixture for operation mechanics (fixture above anchors the
# round trip; this one keeps assertions readable).
DOC = {
    "[opencode]": {
        "agents": {
            "build": {"model": "m0", "fallback_models": ["m1", "m2"]},
            "plan": {"model": "p0"},
            "solo": {},
        },
        "categories": {
            "coding": {"models": [
                "c0", {"model": "c1"},
                {"model": "c2", "reasoning": "high"},
            ]},
        },
    },
}


def groundtruth_doc():
    return EditorDocument("work", json.loads(FIXTURE.read_text()))


def make_doc():
    return EditorDocument("work", copy.deepcopy(DOC))


def route(doc, name):
    for item in doc.routes():
        if item.name == name:
            return item
    raise KeyError(name)


# ── the killer: ground-truth migration + canonical no-op ───────────

class RoundTripGroundTruthTests(unittest.TestCase):
    def test_round_trip_migrates_legacy_agents_to_canonical_models(self):
        legacy = json.loads(
            (FIXTURE.parent / "groundtruth_legacy.json").read_text())
        doc = EditorDocument("work", transform_legacy(legacy)[0])
        for item in doc.routes():
            write_chain(item, chain_entries(item))
        self.assertEqual(doc.document, json.loads(FIXTURE.read_text()))

    def test_migrated_metis_folds_reasoning_into_entry_zero(self):
        doc = groundtruth_doc()
        item = route(doc, "metis")
        write_chain(item, chain_entries(item))
        self.assertEqual(item.block, {"models": [
            {"model": "provider-1/model-1", "reasoning": "max"},
            {"model": "provider-12/model-12", "reasoning": "xhigh"},
            {"model": "provider-13/model-13", "reasoning": "max"},
            {"model": "provider-14/model-14", "reasoning": "max"},
        ]})

    def test_second_round_trip_on_canonical_document_is_noop(self):
        doc = groundtruth_doc()
        for item in doc.routes():
            write_chain(item, chain_entries(item))
        once = copy.deepcopy(doc.document)
        for item in doc.routes():
            write_chain(item, chain_entries(item))
        self.assertEqual(doc.document, once)

    def test_chain_length_matches_route_entry_count_everywhere(self):
        doc = groundtruth_doc()
        for item in doc.routes():
            with self.subTest(route=item.name):
                self.assertEqual(len(chain_entries(item)),
                                 route_entry_count(item))
        for item in doc.routes():
            write_chain(item, chain_entries(item))
        for item in doc.routes():
            with self.subTest(canonical_route=item.name):
                self.assertEqual(len(chain_entries(item)),
                                 route_entry_count(item))

    def test_entries_are_live_references(self):
        doc = groundtruth_doc()
        chain = chain_entries(route(doc, "visual-engineering"))
        models = doc.document["[opencode]"]["categories"][
            "visual-engineering"]["models"]
        self.assertIs(chain[1], models[1])
        junior_block = {"model": "m0", "fallback_models": ["m1", "m2"]}
        doc2 = EditorDocument("x", {"[opencode]": {"agents": {
            "sisyphus-junior": junior_block}}})
        jchain = chain_entries(route(doc2, "sisyphus-junior"))
        self.assertIs(jchain[0], junior_block["model"])
        self.assertIs(jchain[1], junior_block["fallback_models"][0])

    def test_canonical_agent_entries_are_live_references(self):
        doc = groundtruth_doc()
        item = route(doc, "sisyphus-junior")
        write_chain(item, chain_entries(item))
        models = item.block["models"]
        self.assertIs(chain_entries(item)[0], models[0])
        self.assertIs(chain_entries(item)[1], models[1])

    def test_model_only_agent_fallback_collapses_via_round_trip(self):
        # The fixture agents happen to carry reasoning on every dict
        # fallback; construct the collapse case explicitly.
        doc = EditorDocument("x", {"[opencode]": {"agents": {"a": {
            "model": "m", "fallback_models": [{"model": "f"}]}}}})
        item = doc.routes()[0]
        write_chain(item, chain_entries(item))
        self.assertEqual(item.block, {"models": ["m", "f"]})

    def test_empty_chain_write_then_reread(self):
        block = {"model": "m", "fallback_models": ["f"]}
        item = RouteItem("agent", "a", block, None)
        write_chain(item, [])
        self.assertEqual(chain_entries(item), [])
        self.assertEqual(route_entry_count(item), 0)
        self.assertEqual(block, {})


class GroundTruthMutationTests(unittest.TestCase):
    def test_move_fallback1_up_on_metis_changes_only_that_order(self):
        """QA scenario: one intended order change, everything else
        deep-equal (one canonical chain entry moved, nothing else)."""
        doc = groundtruth_doc()
        original = json.loads(FIXTURE.read_text())
        item = route(doc, "metis")   # 4 reasoning-bearing chain entries
        chain = chain_entries(item)
        chain.insert(1, chain.pop(2))   # entry[2] up
        write_chain(item, chain)
        expected = copy.deepcopy(original)
        models = expected["[opencode]"]["agents"]["metis"]["models"]
        models[1], models[2] = models[2], models[1]
        self.assertEqual(doc.document, expected)
        self.assertEqual(route_entry_count(item), 4)


# ── chain view ─────────────────────────────────────────────────────

class ChainViewTests(unittest.TestCase):
    def test_agent_model_then_fallbacks(self):
        self.assertEqual(chain_entries(route(make_doc(), "build")),
                         ["m0", "m1", "m2"])

    def test_agent_without_model_reads_fallbacks_only(self):
        doc = EditorDocument("x", {"[opencode]": {"agents": {"a": {
            "fallback_models": ["f1", "f2"]}}}})
        self.assertEqual(chain_entries(doc.routes()[0]), ["f1", "f2"])

    def test_agent_with_neither_key(self):
        doc = EditorDocument("x", {"[opencode]": {"agents": {"a": {
            "reasoning": "max"}}}})
        self.assertEqual(chain_entries(doc.routes()[0]), [])

    def test_agent_malformed_fallback_reads_absent(self):
        doc = EditorDocument("x", {"[opencode]": {"agents": {"a": {
            "model": "m", "fallback_models": "oops"}}}})
        self.assertEqual(chain_entries(doc.routes()[0]), ["m"])

    def test_agent_canonical_models_list_reads_directly(self):
        doc = EditorDocument("x", {"[opencode]": {"agents": {"a": {
            "models": ["m0", {"model": "m1", "reasoning": "high"}]}}}})
        self.assertEqual(chain_entries(doc.routes()[0]),
                         ["m0", {"model": "m1", "reasoning": "high"}])

    def test_agent_models_list_wins_over_legacy_keys(self):
        doc = EditorDocument("x", {"[opencode]": {"agents": {"a": {
            "model": "legacy", "fallback_models": ["lf"],
            "models": ["c0", "c1"]}}}})
        item = doc.routes()[0]
        self.assertEqual(chain_entries(item), ["c0", "c1"])
        self.assertEqual(route_entry_count(item), 2)

    def test_category_models_list(self):
        self.assertEqual(
            chain_entries(route(make_doc(), "coding")),
            ["c0", {"model": "c1"}, {"model": "c2", "reasoning": "high"}])

    def test_category_missing_or_malformed_models(self):
        for block in ({}, {"models": "oops"}, {"models": []}):
            with self.subTest(block=block):
                doc = EditorDocument("x", {"[opencode]": {
                    "categories": {"c": block}}})
                self.assertEqual(chain_entries(doc.routes()[0]), [])


# ── write-back: categories ─────────────────────────────────────────

class WriteChainCategoryTests(unittest.TestCase):
    def test_write_replaces_models_and_preserves_other_keys(self):
        block = {"description": "d", "models": ["a"]}
        item = RouteItem("category", "c", block, 1)
        write_chain(item, ["x", {"model": "y"}])
        self.assertEqual(block,
                         {"description": "d", "models": ["x", {"model": "y"}]})

    def test_category_never_collapses_model_only_dict(self):
        block = {"models": ["a"]}
        item = RouteItem("category", "c", block, 1)
        write_chain(item, [{"model": "x"}])
        self.assertEqual(block["models"], [{"model": "x"}])

    def test_empty_write_invents_no_models_key(self):
        block = {"description": "d"}
        item = RouteItem("category", "c", block, None)
        write_chain(item, [])
        self.assertEqual(block, {"description": "d"})

    def test_empty_write_clears_existing_models_key(self):
        block = {"models": ["a"], "tools": {"t": 1}}
        item = RouteItem("category", "c", block, 1)
        write_chain(item, [])
        self.assertEqual(block, {"models": [], "tools": {"t": 1}})


# ── write-back: agents ─────────────────────────────────────────────

class WriteChainAgentTests(unittest.TestCase):
    def test_write_stores_collapsed_canonical_models(self):
        block = {"reasoning": "high"}
        item = RouteItem("agent", "a", block, None)
        write_chain(item, [{"model": "p"}, {"model": "f1"},
                           {"model": "f2", "reasoning": "low"}, "f3"])
        self.assertEqual(block, {
            "reasoning": "high",
            "models": ["p", "f1", {"model": "f2", "reasoning": "low"},
                       "f3"]})

    def test_string_primary_stays_string(self):
        block = {}
        item = RouteItem("agent", "a", block, None)
        write_chain(item, ["m", "f"])
        self.assertEqual(block, {"models": ["m", "f"]})

    def test_single_entry_chain_writes_models_list(self):
        block = {}
        item = RouteItem("agent", "a", block, None)
        write_chain(item, ["m"])
        self.assertEqual(block, {"models": ["m"]})

    def test_empty_chain_removes_both_keys_keeps_others(self):
        block = {"model": "m", "reasoning": "max",
                 "fallback_models": ["f"]}
        item = RouteItem("agent", "a", block, None)
        write_chain(item, [])
        self.assertEqual(block, {"reasoning": "max"})

    def test_existing_key_positions_preserved(self):
        block = {"description": "d", "model": "m0",
                 "fallback_models": ["m1"], "tools": {"t": 1}}
        item = RouteItem("agent", "a", block, None)
        write_chain(item, ["m0", "m1"])
        self.assertEqual(list(block),
                         ["description", "tools", "models"])

    def test_legacy_save_folds_definition_reasoning_into_entry_zero(self):
        block = {"model": "m0", "reasoning": "max",
                 "fallback_models": ["m1", "m2"]}
        item = RouteItem("agent", "a", block, None)
        write_chain(item, chain_entries(item))
        self.assertEqual(block, {"models": [
            {"model": "m0", "reasoning": "max"}, "m1", "m2"]})

    def test_legacy_save_folds_every_definition_settings_key(self):
        block = {"model": "m", "reasoning": "high",
                 "provider_options": {"p": {"x": 1}},
                 "fallback_models": []}
        item = RouteItem("agent", "a", block, None)
        write_chain(item, chain_entries(item))
        self.assertEqual(block, {"models": [{
            "model": "m", "reasoning": "high",
            "provider_options": {"p": {"x": 1}}}]})

    def test_canonical_round_trip_unchanged(self):
        block = {"description": "d", "models": [
            "p", {"model": "f", "reasoning": "high"}]}
        item = RouteItem("agent", "a", block, None)
        expected = copy.deepcopy(block)
        write_chain(item, chain_entries(item))
        self.assertEqual(block, expected)

    def test_dict_entry_zero_leaves_definition_settings_in_place(self):
        block = {"reasoning": "max",
                 "model": {"model": "m", "reasoning": "high"},
                 "fallback_models": ["f"]}
        item = RouteItem("agent", "a", block, None)
        write_chain(item, chain_entries(item))
        self.assertEqual(block, {
            "reasoning": "max",
            "models": [{"model": "m", "reasoning": "high"}, "f"]})


# ── route operations ───────────────────────────────────────────────

class AddRouteTests(unittest.TestCase):
    def test_add_agent_creates_canonical_empty_models_block(self):
        doc = make_doc()
        result = add_route(doc, "agent", "newbie")
        self.assertEqual(result, OperationResult(True, "Route added: newbie"))
        self.assertEqual(
            doc.document["[opencode]"]["agents"]["newbie"], {"models": []})

    def test_add_rejects_duplicate_exact_message(self):
        doc = make_doc()
        before = copy.deepcopy(doc.document)
        self.assertEqual(add_route(doc, "agent", "build"),
                         OperationResult(False,
                                         "Route 'build' already exists"))
        self.assertEqual(doc.document, before)

    def test_add_rejects_empty_name(self):
        self.assertEqual(add_route(make_doc(), "agent", ""),
                         OperationResult(False,
                                         "Route name must not be empty"))

    def test_add_creates_harness_and_sections_on_demand(self):
        doc = EditorDocument("bare", {})
        self.assertEqual(add_route(doc, "category", "first"),
                         OperationResult(True, "Route added: first"))
        self.assertEqual(doc.document,
                         {"[opencode]": {"categories": {"first": {}}}})
        self.assertEqual(add_route(doc, "agent", "one"),
                         OperationResult(True, "Route added: one"))
        self.assertEqual(doc.document["[opencode]"]["agents"],
                         {"one": {"models": []}})
        self.assertEqual(list(doc.document["[opencode]"]),
                         ["categories", "agents"])

    def test_add_into_missing_section_of_existing_harness(self):
        doc = EditorDocument("x", {"[opencode]": {"agents": {"a": {}}}})
        add_route(doc, "category", "c")
        self.assertEqual(doc.document["[opencode]"]["categories"],
                         {"c": {}})

    def test_same_name_across_sections_allowed(self):
        # 'build' exists as an agent; adding category 'build' is fine.
        self.assertTrue(add_route(make_doc(), "category", "build").ok)

    def test_add_on_malformed_harness_fails_without_crash(self):
        doc = EditorDocument("x", {"[opencode]": "nope"})
        before = copy.deepcopy(doc.document)
        result = add_route(doc, "agent", "a")
        self.assertFalse(result.ok)
        self.assertEqual(doc.document, before)


class RenameRouteTests(unittest.TestCase):
    def test_rename_preserves_position_and_block_reference(self):
        doc = make_doc()
        block = doc.document["[opencode]"]["agents"]["build"]
        result = rename_route(doc, "agent", "build", "builder")
        self.assertEqual(
            result, OperationResult(True, "Route renamed: build -> builder"))
        agents = doc.document["[opencode]"]["agents"]
        self.assertEqual(list(agents), ["builder", "plan", "solo"])
        self.assertIs(agents["builder"], block)

    def test_rename_rejects_existing_target(self):
        doc = make_doc()
        before = copy.deepcopy(doc.document)
        self.assertEqual(rename_route(doc, "agent", "build", "plan"),
                         OperationResult(False,
                                         "Route 'plan' already exists"))
        self.assertEqual(doc.document, before)

    def test_rename_rejects_empty_new_name(self):
        self.assertEqual(rename_route(make_doc(), "agent", "build", ""),
                         OperationResult(False,
                                         "Route name must not be empty"))

    def test_rename_missing_old(self):
        self.assertEqual(rename_route(make_doc(), "agent", "ghost", "new"),
                         OperationResult(False,
                                         "Route 'ghost' not found"))

    def test_rename_category_preserves_fixture_position(self):
        doc = groundtruth_doc()
        self.assertTrue(rename_route(doc, "category", "quick", "fast").ok)
        names = list(json.loads(FIXTURE.read_text())["[opencode]"]
                     ["categories"])
        pos = names.index("quick")
        self.assertEqual(
            list(doc.document["[opencode]"]["categories"]),
            names[:pos] + ["fast"] + names[pos + 1:])


class DeleteRouteByNameTests(unittest.TestCase):
    def test_delete_removes_route(self):
        doc = make_doc()
        self.assertEqual(delete_route_by_name(doc, "category", "coding"),
                         OperationResult(True, "Route deleted: coding"))
        self.assertNotIn("coding",
                         doc.document["[opencode]"]["categories"])

    def test_delete_missing(self):
        doc = make_doc()
        before = copy.deepcopy(doc.document)
        self.assertEqual(delete_route_by_name(doc, "agent", "ghost"),
                         OperationResult(False, "Route 'ghost' not found"))
        self.assertEqual(doc.document, before)

    def test_delete_on_missing_harness(self):
        doc = EditorDocument("x", {})
        self.assertEqual(delete_route_by_name(doc, "agent", "a"),
                         OperationResult(False, "Route 'a' not found"))


# ── entry operations ───────────────────────────────────────────────

class MoveChainEntryTests(unittest.TestCase):
    def test_agent_adjacent_move_writes_canonical_order(self):
        doc = make_doc()
        item = route(doc, "build")
        self.assertEqual(move_chain_entry(item, 1, 0),
                         OperationResult(True, "Entry moved"))
        self.assertEqual(item.block, {"models": ["m1", "m0", "m2"]})

    def test_agent_arbitrary_move_down(self):
        doc = make_doc()
        item = route(doc, "build")
        move_chain_entry(item, 0, 2)
        self.assertEqual(item.block, {"models": ["m1", "m2", "m0"]})

    def test_category_move_last_to_front(self):
        doc = make_doc()
        item = route(doc, "coding")
        move_chain_entry(item, 2, 0)
        self.assertEqual(
            item.block["models"],
            [{"model": "c2", "reasoning": "high"}, "c0", {"model": "c1"}])

    def test_from_out_of_bounds(self):
        item = route(make_doc(), "build")
        for bad in (-1, 3, 99):
            with self.subTest(index=bad):
                self.assertEqual(
                    move_chain_entry(item, bad, 0),
                    OperationResult(False, f"Invalid entry index: {bad}"))

    def test_to_out_of_bounds(self):
        item = route(make_doc(), "build")
        self.assertEqual(move_chain_entry(item, 0, 3),
                         OperationResult(False, "Invalid entry index: 3"))
        self.assertEqual(move_chain_entry(item, 0, -1),
                         OperationResult(False, "Invalid entry index: -1"))

    def test_move_first_up_is_bounds_error(self):
        item = route(make_doc(), "build")
        self.assertEqual(move_chain_entry(item, 0, -1),
                         OperationResult(False, "Invalid entry index: -1"))

    def test_from_equals_to_is_ok_true_noop(self):
        doc = make_doc()
        before = copy.deepcopy(doc.document)
        item = route(doc, "build")
        self.assertEqual(move_chain_entry(item, 1, 1),
                         OperationResult(True, "Entry moved"))
        self.assertEqual(doc.document, before)


class RemoveChainEntryTests(unittest.TestCase):
    def test_remove_fallback(self):
        doc = make_doc()
        item = route(doc, "build")
        self.assertEqual(remove_chain_entry(item, 2),
                         OperationResult(True, "Entry removed"))
        self.assertEqual(item.block, {"models": ["m0", "m1"]})

    def test_remove_agent_primary_promotes_fallback(self):
        doc = make_doc()
        item = route(doc, "build")
        remove_chain_entry(item, 0)
        self.assertEqual(item.block, {"models": ["m1", "m2"]})

    def test_promotion_keeps_reasoning_dict_and_collapses_plain(self):
        block = {"model": "m", "fallback_models": [
            {"model": "f", "reasoning": "high"}, {"model": "g"}]}
        item = RouteItem("agent", "a", block, None)
        remove_chain_entry(item, 0)
        self.assertEqual(block, {"models": [
            {"model": "f", "reasoning": "high"}, "g"]})

    def test_remove_until_empty_removes_chain_keys(self):
        block = {"model": "m", "reasoning": "low",
                 "fallback_models": ["f"]}
        item = RouteItem("agent", "a", block, None)
        remove_chain_entry(item, 0)
        self.assertEqual(block,
                         {"models": [{"model": "f", "reasoning": "low"}]})
        remove_chain_entry(item, 0)
        self.assertEqual(block, {})

    def test_remove_from_empty_chain_is_error(self):
        item = RouteItem("agent", "a", {}, None)
        self.assertEqual(remove_chain_entry(item, 0),
                         OperationResult(False, "Invalid entry index: 0"))

    def test_remove_bounds(self):
        item = route(make_doc(), "build")
        self.assertEqual(remove_chain_entry(item, 3),
                         OperationResult(False, "Invalid entry index: 3"))
        self.assertEqual(remove_chain_entry(item, -1),
                         OperationResult(False, "Invalid entry index: -1"))

    def test_category_remove_middle(self):
        doc = make_doc()
        item = route(doc, "coding")
        remove_chain_entry(item, 1)
        self.assertEqual(item.block["models"],
                         ["c0", {"model": "c2", "reasoning": "high"}])


class SetEntryTests(unittest.TestCase):
    def test_replace_category_entry_keeps_dict_form(self):
        doc = make_doc()
        item = route(doc, "coding")
        self.assertEqual(set_entry(item, 1, {"model": "new"}),
                         OperationResult(True, "Entry saved"))
        self.assertEqual(item.block["models"],
                         ["c0", {"model": "new"},
                          {"model": "c2", "reasoning": "high"}])

    def test_replace_agent_primary_collapses_model_only(self):
        doc = make_doc()
        item = route(doc, "build")
        set_entry(item, 0, {"model": "p9"})
        self.assertEqual(item.block["models"], ["p9", "m1", "m2"])

    def test_replace_agent_fallback_keeps_reasoning_dict(self):
        doc = make_doc()
        item = route(doc, "build")
        set_entry(item, 1, {"model": "f9", "reasoning": "max"})
        self.assertEqual(item.block["models"],
                         ["m0", {"model": "f9", "reasoning": "max"}, "m2"])

    def test_replace_agent_fallback_collapses_model_only(self):
        doc = make_doc()
        item = route(doc, "build")
        set_entry(item, 1, {"model": "f9"})
        self.assertEqual(item.block["models"], ["m0", "f9", "m2"])

    def test_append_via_past_end_sentinel_agent(self):
        doc = make_doc()
        item = route(doc, "build")   # 3 entries; index 3 = NEW
        set_entry(item, 3, "m3")
        self.assertEqual(item.block["models"],
                         ["m0", "m1", "m2", "m3"])

    def test_append_via_past_end_sentinel_category(self):
        doc = make_doc()
        item = route(doc, "coding")   # 3 entries; index 3 = NEW
        set_entry(item, 3, {"model": "c3"})
        self.assertEqual(item.block["models"][-1], {"model": "c3"})

    def test_append_on_empty_chain_agent_writes_models_list(self):
        item = RouteItem("agent", "a", {}, None)
        set_entry(item, 0, "m")
        self.assertEqual(item.block, {"models": ["m"]})

    def test_index_past_sentinel_rejected(self):
        item = route(make_doc(), "build")
        self.assertEqual(set_entry(item, 4, "x"),
                         OperationResult(False, "Invalid entry index: 4"))
        self.assertEqual(set_entry(item, -1, "x"),
                         OperationResult(False, "Invalid entry index: -1"))


# ── shell glue ─────────────────────────────────────────────────────

class ApplyTransitionTests(unittest.TestCase):
    @staticmethod
    def re_state(route_index=0, entry_index=0, **kw):
        return EditorState(screen=RE, route_index=route_index,
                           entry_index=entry_index, field_count=4, **kw)

    def test_move_down_payload_performs_doc_surgery(self):
        doc = make_doc()
        state = self.re_state(entry_index=0)
        trans = handle_key(state, ",", doc)
        self.assertEqual((trans.action, trans.payload),
                         ("move-down", (0, 1)))
        self.assertIsNone(apply_transition(doc, state, trans))
        self.assertEqual(doc.document["[opencode]"]["agents"]["build"],
                         {"models": ["m1", "m0", "m2"]})
        self.assertTrue(state.dirty)

    def test_move_up_payload_performs_doc_surgery(self):
        doc = make_doc()
        state = self.re_state(entry_index=1)
        trans = handle_key(state, ".", doc)
        self.assertEqual((trans.action, trans.payload), ("move-up", (1, 0)))
        apply_transition(doc, state, trans)
        self.assertEqual(doc.document["[opencode]"]["agents"]["build"],
                         {"models": ["m1", "m0", "m2"]})

    def test_delete_entry_confirm_removes_and_clamps_last_index(self):
        doc = make_doc()
        state = self.re_state(entry_index=2)
        handle_key(state, "x", doc)
        trans = handle_key(state, "y", doc)
        self.assertEqual((trans.action, trans.payload),
                         ("confirm-yes", "delete-entry"))
        self.assertIsNone(apply_transition(doc, state, trans))
        self.assertEqual(doc.document["[opencode]"]["agents"]["build"],
                         {"models": ["m0", "m1"]})
        self.assertTrue(state.dirty)
        self.assertEqual(state.entry_index, 1)   # clamped from 2

    def test_delete_entry_confirm_promotes_primary(self):
        doc = make_doc()
        state = self.re_state(entry_index=0)
        handle_key(state, "x", doc)
        trans = handle_key(state, "y", doc)
        apply_transition(doc, state, trans)
        self.assertEqual(doc.document["[opencode]"]["agents"]["build"],
                         {"models": ["m1", "m2"]})
        self.assertEqual(state.entry_index, 0)

    def test_delete_route_confirm_does_no_extra_work(self):
        doc = make_doc()
        state = EditorState(screen=RL, field_count=4)
        handle_key(state, "x", doc)          # confirm for 'build'
        trans = handle_key(state, "y", doc)  # core deleted already
        before = copy.deepcopy(doc.document)
        self.assertIsNone(apply_transition(doc, state, trans))
        self.assertEqual(doc.document, before)

    def test_quit_confirm_returns_none(self):
        doc = make_doc()
        state = EditorState(screen=RL, dirty=True, field_count=4)
        handle_key(state, "q", doc)
        trans = handle_key(state, "y", doc)
        self.assertIsNone(apply_transition(doc, state, trans))

    def test_add_returns_route_name_prompt_with_selected_kind(self):
        doc = make_doc()
        state = EditorState(screen=RL, field_count=4)   # 'build' selected
        trans = handle_key(state, "a", doc)
        self.assertIsInstance(trans, StateTransition)
        prompt = apply_transition(doc, state, trans)
        self.assertEqual(prompt,
                         ShellPrompt("route-name", "New route name: ",
                                     "agent"))

    def test_add_kind_follows_category_selection(self):
        doc = make_doc()
        state = EditorState(screen=RL, route_index=3, field_count=4)
        trans = handle_key(state, "a", doc)   # 'coding' selected
        prompt = apply_transition(doc, state, trans)
        self.assertEqual(prompt.target, "category")

    def test_add_on_empty_document_defaults_agent(self):
        doc = EditorDocument("empty", {})
        state = EditorState(screen=RL, field_count=4)
        trans = handle_key(state, "a", doc)
        prompt = apply_transition(doc, state, trans)
        self.assertEqual(prompt,
                         ShellPrompt("route-name", "New route name: ",
                                     "agent"))

    def test_inert_transition_returns_none(self):
        doc = make_doc()
        state = self.re_state()
        trans = handle_key(state, "up", doc)
        self.assertEqual((trans.action, trans.payload), ("none", None))
        self.assertIsNone(apply_transition(doc, state, trans))

    def test_stale_route_index_is_safe_noop(self):
        doc = make_doc()
        state = self.re_state(route_index=99, entry_index=0)
        trans = StateTransition(False, "move-down", (0, 1))
        before = copy.deepcopy(doc.document)
        self.assertIsNone(apply_transition(doc, state, trans))
        self.assertEqual(doc.document, before)


# ── malformed documents ────────────────────────────────────────────

class MalformedDocumentTests(unittest.TestCase):
    def test_agents_as_list_is_skipped_no_crash(self):
        doc = EditorDocument("weird", {"[opencode]": {
            "agents": ["a", "b"],
            "categories": {"c": {"models": ["m0", "m1"]}}}})
        self.assertEqual([r.name for r in doc.routes()], ["c"])
        item = doc.routes()[0]
        state = EditorState(screen=RE, field_count=4)
        trans = handle_key(state, ",", doc)
        self.assertEqual((trans.action, trans.payload),
                         ("move-down", (0, 1)))
        apply_transition(doc, state, trans)
        self.assertEqual(item.block["models"], ["m1", "m0"])

    def test_non_dict_route_blocks_skipped(self):
        doc = EditorDocument("x", {"[opencode]": {
            "agents": {"bad": "str", "good": {"model": "m"}}}})
        self.assertEqual([r.name for r in doc.routes()], ["good"])


# ── contracts ──────────────────────────────────────────────────────

class ContractTests(unittest.TestCase):
    def test_operation_result_fields(self):
        self.assertEqual(OperationResult._fields, ("ok", "message"))
        result = OperationResult(True, "msg")
        self.assertEqual((result.ok, result.message), (True, "msg"))

    def test_shell_prompt_fields(self):
        self.assertEqual(ShellPrompt._fields, ("kind", "prompt", "target"))
        prompt = ShellPrompt("route-name", "New route name: ", "agent")
        self.assertEqual((prompt.kind, prompt.target),
                         ("route-name", "agent"))


if __name__ == "__main__":
    unittest.main()
