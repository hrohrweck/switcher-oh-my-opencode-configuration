"""Tests for the Task 15 forms layer (model-entry + settings forms).

Pure form-state contracts (NO curses — Task 16 renders ``FormState``):
``FormField``/``FormState`` shape, the two builders (string/dict entry;
harness settings block), ``validate_and_collect`` (Task 13 parsers,
kind-specific messages, blank numerics ABSENT), ``apply_entry_form``
(collect + merge ``extra_preserved`` → Task 14 ``set_entry``, agent
collapse asymmetry inherited) and ``apply_settings_form``
(model_fallback None-cycle removes the key; ``runtime_fallback``
written only when ≥1 sub-field is set, else removed), plus the
``handle_form_key`` glue.  Killer tests: the no-edit round trip
deep-equals the source (entry with provider_options+thinking; full
settings block) and the ground-truth probe (only reasoning changed).
"""

import copy
import json
import unittest
from pathlib import Path

from opencode_config_switcher.editor import (
    REASONING_CYCLE,
    EditorDocument,
    FormField,
    FormState,
    OperationResult,
    RouteItem,
    apply_entry_form,
    apply_settings_form,
    build_entry_form,
    build_settings_form,
    chain_entries,
    handle_form_key,
    validate_and_collect,
)

FIXTURE = (Path(__file__).resolve().parent / "fixtures"
           / "groundtruth_migrated.json")


def set_value(form: FormState, name: str, value) -> None:
    """Test helper: replace one field's value (the Task 16 typing seam)."""
    form.fields[:] = [f._replace(value=value) if f.name == name else f
                      for f in form.fields]


def field(form: FormState, name: str) -> FormField:
    return next(f for f in form.fields if f.name == name)


# ── build_entry_form ────────────────────────────────────────────────

class BuildEntryFormTests(unittest.TestCase):
    def test_string_entry_builds_text_model_and_blank_rest(self):
        form = build_entry_form("m1")
        self.assertEqual(form.fields, [
            FormField("model", "text", "m1", {}),
            FormField("reasoning", "enum", None,
                      {"choices": REASONING_CYCLE}),
            FormField("temperature", "number", None,
                      {"number_kind": "temperature"}),
            FormField("top_p", "number", None,
                      {"number_kind": "top_p"}),
            FormField("max_tokens", "number", None,
                      {"number_kind": "max_tokens"}),
        ])
        self.assertEqual(form.cursor, 0)
        self.assertEqual(form.error, "")
        self.assertEqual(form.extra_preserved, {})

    def test_full_dict_entry_prefills_every_field(self):
        form = build_entry_form({"model": "p/m", "reasoning": "high",
                                 "temperature": 0.5, "top_p": 0.9,
                                 "max_tokens": 4096})
        self.assertEqual(form.fields, [
            FormField("model", "text", "p/m", {}),
            FormField("reasoning", "enum", "high",
                      {"choices": REASONING_CYCLE}),
            FormField("temperature", "number", "0.5",
                      {"number_kind": "temperature"}),
            FormField("top_p", "number", "0.9",
                      {"number_kind": "top_p"}),
            FormField("max_tokens", "number", "4096",
                      {"number_kind": "max_tokens"}),
        ])
        self.assertEqual(form.extra_preserved, {})

    def test_dict_entry_absent_numbers_are_blank_none(self):
        form = build_entry_form({"model": "m", "reasoning": "low"})
        for name in ("temperature", "top_p", "max_tokens"):
            self.assertIsNone(field(form, name).value, name)
        self.assertEqual(form.extra_preserved, {})

    def test_dict_without_model_key_builds_blank_model(self):
        form = build_entry_form({"reasoning": "high"})
        self.assertEqual(field(form, "model").value, "")
        self.assertEqual(field(form, "reasoning").value, "high")
        self.assertEqual(form.extra_preserved, {})

    def test_extra_preserved_unknown_and_known_uneditable_keys(self):
        entry = {
            "model": "m", "reasoning": "high", "temperature": 0.5,
            "provider_options": {"anthropic": {"bet": ["x"]}},
            "thinking": {"enabled": True, "budget": 10},
            "textVerbosity": "concise", "variant": "v1",
            "reasoningEffort": "low", "maxTokens": 100,
            "providerOptions": {"k": 1},
            "totally_unknown": [1, 2],
        }
        form = build_entry_form(entry)
        self.assertEqual(form.extra_preserved, {
            "provider_options": {"anthropic": {"bet": ["x"]}},
            "thinking": {"enabled": True, "budget": 10},
            "textVerbosity": "concise", "variant": "v1",
            "reasoningEffort": "low", "maxTokens": 100,
            "providerOptions": {"k": 1},
            "totally_unknown": [1, 2],
        })

    def test_custom_reasoning_value_preserved_raw(self):
        form = build_entry_form({"model": "m", "reasoning": "turbo"})
        self.assertEqual(field(form, "reasoning").value, "turbo")


# ── validate_and_collect: entry form ────────────────────────────────

class ValidateCollectEntryTests(unittest.TestCase):
    def test_blank_model_error(self):
        for model_value in ("", "   ", None):
            with self.subTest(model=model_value):
                form = build_entry_form("placeholder")
                set_value(form, "model", model_value)
                self.assertEqual(
                    validate_and_collect(form),
                    (None, "Model ID must not be empty"))

    def test_temperature_out_of_range_message(self):
        form = build_entry_form({"model": "m"})
        set_value(form, "temperature", "3")
        self.assertEqual(
            validate_and_collect(form),
            (None, "temperature must be within 0..2"))

    def test_top_p_out_of_range_message(self):
        form = build_entry_form({"model": "m"})
        set_value(form, "top_p", "1.5")
        self.assertEqual(
            validate_and_collect(form),
            (None, "top_p must be within 0..1"))

    def test_max_tokens_float_text_message(self):
        form = build_entry_form({"model": "m"})
        set_value(form, "max_tokens", "2.5")
        self.assertEqual(
            validate_and_collect(form),
            (None, "Invalid number: '2.5'"))

    def test_not_a_number_message(self):
        form = build_entry_form({"model": "m"})
        set_value(form, "temperature", "warm")
        self.assertEqual(
            validate_and_collect(form),
            (None, "Invalid number: 'warm'"))

    def test_blank_numerics_absent_from_collected(self):
        form = build_entry_form({"model": "m", "reasoning": None})
        set_value(form, "temperature", "  ")   # whitespace-only = blank
        self.assertEqual(validate_and_collect(form),
                         ({"model": "m"}, None))

    def test_numbers_round_trip_through_text(self):
        form = build_entry_form({"model": "m", "temperature": 0.5,
                                 "top_p": 0.9, "max_tokens": 4096})
        self.assertEqual(
            validate_and_collect(form),
            ({"model": "m", "temperature": 0.5, "top_p": 0.9,
              "max_tokens": 4096}, None))

    def test_in_cycle_reasoning_collected(self):
        form = build_entry_form({"model": "m", "reasoning": "high"})
        self.assertEqual(validate_and_collect(form),
                         ({"model": "m", "reasoning": "high"}, None))

    def test_custom_reasoning_collected_raw(self):
        form = build_entry_form({"model": "m", "reasoning": "turbo"})
        self.assertEqual(validate_and_collect(form),
                         ({"model": "m", "reasoning": "turbo"}, None))

    def test_unset_reasoning_absent(self):
        form = build_entry_form("m")   # reasoning None
        self.assertEqual(validate_and_collect(form),
                         ({"model": "m"}, None))


# ── build_settings_form ─────────────────────────────────────────────

SETTINGS_BLOCK = {
    "model_fallback": True,
    "runtime_fallback": {
        "enabled": False,
        "retry_on_errors": [500, 503],
        "max_fallback_attempts": 3,
        "cooldown_seconds": 60,
    },
    "agents": {"a": {"model": "m"}},
    "categories": {"c": {"models": ["x"]}},
}


class BuildSettingsFormTests(unittest.TestCase):
    def test_fields_from_full_block(self):
        form = build_settings_form(copy.deepcopy(SETTINGS_BLOCK))
        self.assertEqual(form.fields, [
            FormField("model_fallback", "toggle", True, {}),
            FormField("enabled", "toggle", False, {}),
            FormField("retry_on_errors", "text", "500, 503", {}),
            FormField("max_fallback_attempts", "number", "3",
                      {"number_kind": "max_fallback_attempts"}),
            FormField("cooldown_seconds", "number", "60",
                      {"number_kind": "cooldown_seconds"}),
        ])
        self.assertEqual(form.cursor, 0)
        self.assertEqual(form.error, "")
        self.assertEqual(form.extra_preserved, {
            "agents": {"a": {"model": "m"}},
            "categories": {"c": {"models": ["x"]}},
        })

    def test_absent_runtime_all_blank(self):
        form = build_settings_form({"model_fallback": False})
        self.assertEqual(field(form, "enabled").value, None)
        self.assertEqual(field(form, "retry_on_errors").value, "")
        self.assertIsNone(field(form, "max_fallback_attempts").value)
        self.assertIsNone(field(form, "cooldown_seconds").value)
        self.assertEqual(field(form, "model_fallback").value, False)
        self.assertEqual(form.extra_preserved, {})

    def test_malformed_runtime_reads_blank(self):
        form = build_settings_form({"runtime_fallback": "oops"})
        self.assertEqual(field(form, "enabled").value, None)
        self.assertEqual(field(form, "retry_on_errors").value, "")
        self.assertIsNone(field(form, "max_fallback_attempts").value)

    def test_absent_model_fallback_is_none(self):
        form = build_settings_form({})
        self.assertIsNone(field(form, "model_fallback").value)


# ── validate_and_collect: settings form ─────────────────────────────

class ValidateCollectSettingsTests(unittest.TestCase):
    def test_full_collect_round_trip(self):
        form = build_settings_form(copy.deepcopy(SETTINGS_BLOCK))
        self.assertEqual(
            validate_and_collect(form),
            ({"model_fallback": True, "enabled": False,
              "retry_on_errors": [500, 503],
              "max_fallback_attempts": 3, "cooldown_seconds": 60}, None))

    def test_retry_edit_round_trip_with_spaces(self):
        form = build_settings_form({})
        set_value(form, "retry_on_errors", " 429 , 500 ")
        self.assertEqual(
            validate_and_collect(form),
            ({"retry_on_errors": [429, 500]}, None))

    def test_retry_invalid_message(self):
        form = build_settings_form({})
        set_value(form, "retry_on_errors", "a,b")
        self.assertEqual(
            validate_and_collect(form),
            (None, "retry_on_errors must be a comma-separated list "
                   "of integers"))

    def test_blank_all_collects_empty(self):
        form = build_settings_form({})
        self.assertEqual(validate_and_collect(form), ({}, None))

    def test_enabled_false_counts_as_set(self):
        form = build_settings_form({})
        set_value(form, "enabled", False)
        self.assertEqual(validate_and_collect(form),
                         ({"enabled": False}, None))

    def test_invalid_numeric_message(self):
        form = build_settings_form({})
        set_value(form, "cooldown_seconds", "soon")
        self.assertEqual(
            validate_and_collect(form),
            (None, "Invalid number: 'soon'"))

    def test_float_cooldown_collected(self):
        form = build_settings_form({})
        set_value(form, "cooldown_seconds", "30.5")
        self.assertEqual(validate_and_collect(form),
                         ({"cooldown_seconds": 30.5}, None))


# ── apply_entry_form ────────────────────────────────────────────────

class ApplyEntryFormTests(unittest.TestCase):
    def test_error_keeps_form_and_writes_nothing(self):
        item = RouteItem("agent", "a", {"model": "m0"}, None)
        form = build_entry_form("")
        result = apply_entry_form(item, 0, form)
        self.assertEqual(result,
                         OperationResult(False, "Model ID must not be empty"))
        self.assertEqual(form.error, "Model ID must not be empty")
        self.assertEqual(item.block, {"model": "m0"})   # no doc write
        self.assertEqual(field(form, "model").value, "")  # form stays

    def test_blank_model_then_recovery_saves(self):
        item = RouteItem("agent", "a", {}, None)
        form = build_entry_form("")
        self.assertFalse(apply_entry_form(item, 0, form).ok)
        set_value(form, "model", "m1")
        result = apply_entry_form(item, 0, form)
        self.assertEqual(result, OperationResult(True, "Entry saved"))
        self.assertEqual(form.error, "")
        self.assertEqual(item.block, {"models": ["m1"]})

    def test_agent_collapse_only_model_dict_to_string(self):
        item = RouteItem("agent", "a", {"model": "m0",
                                        "fallback_models": ["m1"]}, None)
        form = build_entry_form("placeholder")
        set_value(form, "model", "p9")
        result = apply_entry_form(item, 0, form)
        self.assertEqual(result, OperationResult(True, "Entry saved"))
        self.assertEqual(item.block, {"models": ["p9", "m1"]})

    def test_agent_with_reasoning_stays_dict(self):
        item = RouteItem("agent", "a", {"model": "m0"}, None)
        form = build_entry_form({"model": "p9", "reasoning": "max"})
        apply_entry_form(item, 0, form)
        self.assertEqual(item.block,
                         {"models": [{"model": "p9", "reasoning": "max"}]})

    def test_category_never_collapses_only_model_dict(self):
        item = RouteItem("category", "c",
                         {"models": ["c0", {"model": "c1"}]}, 2)
        form = build_entry_form("placeholder")
        set_value(form, "model", "c2")
        result = apply_entry_form(item, 1, form)
        self.assertEqual(result, OperationResult(True, "Entry saved"))
        self.assertEqual(item.block["models"],
                         ["c0", {"model": "c2"}])

    def test_extra_preserved_passthrough_deep_equal(self):
        entry = {"model": "m", "reasoning": "high",
                 "provider_options": {"x": {"y": [1, 2]}},
                 "thinking": {"enabled": True, "budget": 10}}
        item = RouteItem("agent", "a",
                         {"model": entry, "fallback_models": []}, None)
        form = build_entry_form(entry)
        result = apply_entry_form(item, 0, form)   # build → immediately apply
        self.assertEqual(result, OperationResult(True, "Entry saved"))
        self.assertEqual(chain_entries(item)[0], entry)   # deep-equal

    def test_past_end_sentinel_index_appends(self):
        item = RouteItem("agent", "a", {"model": "m0",
                                        "fallback_models": ["m1"]}, None)
        form = build_entry_form("")   # NEW entry (Task 13 'a' flow)
        set_value(form, "model", "m2")
        result = apply_entry_form(item, 2, form)   # index == len(chain)
        self.assertEqual(result, OperationResult(True, "Entry saved"))
        self.assertEqual(item.block["models"], ["m0", "m1", "m2"])

    def test_custom_reasoning_round_trip_on_agent(self):
        item = RouteItem("agent", "a", {}, None)
        form = build_entry_form({"model": "m", "reasoning": "turbo"})
        apply_entry_form(item, 0, form)
        self.assertEqual(item.block["models"],
                         [{"model": "m", "reasoning": "turbo"}])

    def test_legacy_agent_save_via_form_writes_canonical_with_fold(self):
        item = RouteItem("agent", "a", {"model": "m0", "reasoning": "max",
                                        "fallback_models": ["m1"]}, None)
        form = build_entry_form(chain_entries(item)[1])
        set_value(form, "model", "m9")
        result = apply_entry_form(item, 1, form)
        self.assertEqual(result, OperationResult(True, "Entry saved"))
        self.assertEqual(item.block, {"models": [
            {"model": "m0", "reasoning": "max"}, "m9"]})

    def test_numbers_and_reasoning_written_only_when_present(self):
        item = RouteItem("category", "c", {"models": [{}]}, 1)
        form = build_entry_form({"model": "m", "temperature": 0.7})
        apply_entry_form(item, 0, form)
        self.assertEqual(item.block["models"],
                         [{"model": "m", "temperature": 0.7}])


# ── apply_settings_form ─────────────────────────────────────────────

class ApplySettingsFormTests(unittest.TestCase):
    def test_round_trip_full_block_deep_equal(self):
        block = copy.deepcopy(SETTINGS_BLOCK)
        expected = copy.deepcopy(block)
        form = build_settings_form(block)
        result = apply_settings_form(block, form)   # no edits
        self.assertEqual(result, OperationResult(True, "Settings saved"))
        self.assertEqual(block, expected)

    def test_change_cooldown_only_retry_untouched(self):
        block = copy.deepcopy(SETTINGS_BLOCK)
        form = build_settings_form(block)
        set_value(form, "cooldown_seconds", "90")
        result = apply_settings_form(block, form)
        self.assertEqual(result, OperationResult(True, "Settings saved"))
        self.assertEqual(block["runtime_fallback"], {
            "enabled": False,
            "retry_on_errors": [500, 503],
            "max_fallback_attempts": 3,
            "cooldown_seconds": 90,
        })
        self.assertEqual(block["model_fallback"], True)
        self.assertEqual(block["agents"], {"a": {"model": "m"}})

    def test_blank_numeric_removes_key(self):
        block = {"runtime_fallback": {"enabled": True,
                                      "max_fallback_attempts": 3,
                                      "cooldown_seconds": 60}}
        form = build_settings_form(block)
        set_value(form, "max_fallback_attempts", None)
        apply_settings_form(block, form)
        self.assertEqual(block["runtime_fallback"],
                         {"enabled": True, "cooldown_seconds": 60})

    def test_all_blank_removes_runtime_key_entirely(self):
        block = copy.deepcopy(SETTINGS_BLOCK)
        form = build_settings_form(block)
        set_value(form, "enabled", None)
        set_value(form, "retry_on_errors", "")
        set_value(form, "max_fallback_attempts", None)
        set_value(form, "cooldown_seconds", None)
        result = apply_settings_form(block, form)
        self.assertEqual(result, OperationResult(True, "Settings saved"))
        self.assertNotIn("runtime_fallback", block)
        self.assertEqual(block["model_fallback"], True)   # untouched
        self.assertIn("agents", block)

    def test_block_without_runtime_blank_all_invents_no_key(self):
        block = {"agents": {"a": {}}}
        form = build_settings_form(block)
        result = apply_settings_form(block, form)
        self.assertEqual(result, OperationResult(True, "Settings saved"))
        self.assertEqual(block, {"agents": {"a": {}}})

    def test_model_fallback_none_cycle_removes_key(self):
        block = {"model_fallback": True}
        form = build_settings_form(block)
        self.assertEqual(handle_form_key(form, " "), "toggle")
        self.assertIs(field(form, "model_fallback").value, False)
        apply_settings_form(block, form)
        self.assertIs(block["model_fallback"], False)   # False is SET
        self.assertEqual(handle_form_key(form, " "), "toggle")
        self.assertIsNone(field(form, "model_fallback").value)
        apply_settings_form(block, form)
        self.assertNotIn("model_fallback", block)   # None = removed

    def test_model_fallback_cycle_back_to_true(self):
        block = {}
        form = build_settings_form(block)
        for _ in range(3):   # None → True → False → None … wait: None→True
            handle_form_key(form, " ")
        self.assertIs(field(form, "model_fallback").value, None)
        handle_form_key(form, " ")
        self.assertIs(field(form, "model_fallback").value, True)
        apply_settings_form(block, form)
        self.assertIs(block["model_fallback"], True)

    def test_enabled_only_writes_minimal_runtime(self):
        block = {}
        form = build_settings_form(block)
        handle_form_key(form, "down")   # cursor → enabled
        handle_form_key(form, " ")      # None → True
        apply_settings_form(block, form)
        self.assertEqual(block, {"runtime_fallback": {"enabled": True}})

    def test_invalid_retry_keeps_block_unchanged(self):
        block = copy.deepcopy(SETTINGS_BLOCK)
        before = copy.deepcopy(block)
        form = build_settings_form(block)
        set_value(form, "retry_on_errors", "500, oops")
        result = apply_settings_form(block, form)
        self.assertEqual(
            result,
            OperationResult(False, "retry_on_errors must be a "
                                   "comma-separated list of integers"))
        self.assertEqual(form.error,
                         "retry_on_errors must be a comma-separated "
                         "list of integers")
        self.assertEqual(block, before)   # no doc write


# ── handle_form_key ─────────────────────────────────────────────────

class HandleFormKeyTests(unittest.TestCase):
    def test_up_down_move_cursor_with_clamp(self):
        form = build_entry_form("m")
        self.assertEqual(handle_form_key(form, "down"), "move")
        self.assertEqual(form.cursor, 1)
        self.assertEqual(handle_form_key(form, "up"), "move")
        self.assertEqual(form.cursor, 0)
        self.assertEqual(handle_form_key(form, "up"), "move")   # clamp
        self.assertEqual(form.cursor, 0)
        for _ in range(10):
            self.assertEqual(handle_form_key(form, "down"), "move")
        self.assertEqual(form.cursor, len(form.fields) - 1)

    def test_right_on_enum_cycles_and_returns_cycle(self):
        form = build_entry_form("m")   # reasoning None (unset)
        handle_form_key(form, "down")  # cursor → reasoning
        self.assertEqual(handle_form_key(form, "right"), "cycle")
        self.assertEqual(field(form, "reasoning").value, "off")
        self.assertEqual(handle_form_key(form, "right"), "cycle")
        self.assertEqual(field(form, "reasoning").value, "minimal")
        self.assertEqual(handle_form_key(form, "left"), "cycle")
        self.assertEqual(field(form, "reasoning").value, "off")
        self.assertEqual(handle_form_key(form, "left"), "cycle")
        self.assertIsNone(field(form, "reasoning").value)   # unset

    def test_enum_wraparound(self):
        form = build_entry_form({"model": "m", "reasoning": "auto"})
        handle_form_key(form, "down")
        handle_form_key(form, "right")   # auto → unset (wrap)
        self.assertIsNone(field(form, "reasoning").value)

    def test_custom_reasoning_cycles_to_unset(self):
        form = build_entry_form({"model": "m", "reasoning": "turbo"})
        handle_form_key(form, "down")
        self.assertEqual(handle_form_key(form, "right"), "cycle")
        self.assertIsNone(field(form, "reasoning").value)

    def test_space_cycles_toggle_true_false_none_true(self):
        form = build_settings_form({"model_fallback": True})
        seen = [field(form, "model_fallback").value]
        for _ in range(3):
            self.assertEqual(handle_form_key(form, " "), "toggle")
            seen.append(field(form, "model_fallback").value)
        self.assertEqual(seen, [True, False, None, True])

    def test_space_on_non_toggle_is_none(self):
        form = build_entry_form("m")
        self.assertEqual(handle_form_key(form, " "), "none")
        self.assertEqual(field(form, "model").value, "m")   # untouched

    def test_left_right_on_non_enum_is_none(self):
        form = build_entry_form("m")
        self.assertEqual(handle_form_key(form, "left"), "none")
        self.assertEqual(handle_form_key(form, "right"), "none")

    def test_other_keys_are_none(self):
        form = build_entry_form("m")
        for key in ("x", "enter", "S", "esc", "q", "CTRL_C", "pageup"):
            with self.subTest(key=key):
                self.assertEqual(handle_form_key(form, key), "none")

    def test_cycled_enum_value_flows_through_collect(self):
        form = build_entry_form({"model": "m", "reasoning": "high"})
        handle_form_key(form, "down")
        handle_form_key(form, "right")   # high → xhigh
        self.assertEqual(validate_and_collect(form),
                         ({"model": "m", "reasoning": "xhigh"}, None))


# ── real-surface probe: ground-truth-derived entry ──────────────────

class GroundTruthProbeTests(unittest.TestCase):
    @staticmethod
    def metis_with_derived_entry():
        doc = EditorDocument(
            "work", json.loads(FIXTURE.read_text()))
        item = next(r for r in doc.routes() if r.name == "metis")
        derived = copy.deepcopy(chain_entries(item)[1])   # fb[0]
        derived["provider_options"] = {"provider-12": {"bet": ["ctx"]}}
        derived["thinking"] = {"enabled": True, "budget": 32000}
        item.block["fallback_models"][0] = derived
        return item, derived

    def test_cycle_reasoning_changes_only_reasoning(self):
        item, derived = self.metis_with_derived_entry()
        original = copy.deepcopy(derived)
        form = build_entry_form(chain_entries(item)[1])
        handle_form_key(form, "down")    # cursor → reasoning ("max")
        self.assertEqual(handle_form_key(form, "left"), "cycle")
        # max → high
        result = apply_entry_form(item, 1, form)
        self.assertEqual(result, OperationResult(True, "Entry saved"))
        self.assertEqual(chain_entries(item)[1],
                         {**original, "reasoning": "high"})

    def test_build_then_immediate_apply_is_noop(self):
        item, derived = self.metis_with_derived_entry()
        form = build_entry_form(chain_entries(item)[1])
        result = apply_entry_form(item, 1, form)
        self.assertEqual(result, OperationResult(True, "Entry saved"))
        self.assertEqual(chain_entries(item)[1], derived)

    def test_settings_on_groundtruth_harness_round_trip(self):
        document = json.loads(FIXTURE.read_text())
        harness = document["[opencode]"]
        expected = copy.deepcopy(document)
        form = build_settings_form(harness)
        result = apply_settings_form(harness, form)
        self.assertEqual(result, OperationResult(True, "Settings saved"))
        self.assertEqual(document, expected)


# ── contracts ───────────────────────────────────────────────────────

class FormContractTests(unittest.TestCase):
    def test_form_field_fields(self):
        self.assertEqual(FormField._fields,
                         ("name", "kind", "value", "extra"))
        f = FormField("model", "text", "m", {"k": 1})
        self.assertEqual((f.name, f.kind, f.value, f.extra),
                         ("model", "text", "m", {"k": 1}))

    def test_form_state_defaults(self):
        state = FormState(fields=[])
        self.assertEqual(state.cursor, 0)
        self.assertEqual(state.error, "")
        self.assertEqual(state.extra_preserved, {})

    def test_form_state_extra_preserved_not_shared(self):
        a = FormState(fields=[])
        b = FormState(fields=[])
        a.extra_preserved["k"] = 1
        self.assertEqual(b.extra_preserved, {})


if __name__ == "__main__":
    unittest.main()
