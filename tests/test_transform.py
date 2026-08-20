"""Tests for the legacy → v3 import transform pipeline (``transform.py``).

Binding contract (Task 9 CLI import + Task 17 onboarding consume these):
- KILLER: ``transform_legacy`` over the sanitized REAL ground-truth pair
  (``fixtures/groundtruth_legacy.json`` → ``fixtures/groundtruth_migrated.json``)
  deep-equals the migrated document with ZERO warnings.  The fixtures keep the
  observed field forms verbatim (agent fallback single-model dicts collapse to
  bare strings; category ``models`` single-model entries stay objects) modulo
  one consistent real-ID → ``provider-{n}/model-{n}`` mapping.
- Pipeline order: strip_metadata → rename_agents → bump_model_versions
  (agents+categories, route-level ``model`` only) → rename_keys →
  remap_disabled → restructure categories (model+fallback_models → models) →
  normalize_model_entry on every dict model-ref → wrap
  ``{"$schema": OMO_SCHEMA_URL, "[opencode]": ...}``.
- All functions are pure (``discover_legacy``/``derive_profile_name`` do path
  math only); input dicts are never mutated.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from opencode_config_switcher.omoconfig import OMO_SCHEMA_URL
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.transform import (
    AGENT_NAME_MAP,
    HOOK_NAME_MAP,
    MODEL_VERSION_MAP,
    bump_model_versions,
    derive_profile_name,
    discover_legacy,
    normalize_model_entry,
    remap_disabled,
    rename_agents,
    rename_keys,
    strip_metadata,
    transform_legacy,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
OLD_LEGACY_SCHEMA = (
    "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/"
    "dev/assets/oh-my-opencode.schema.json"
)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TransformLegacyGroundTruth(unittest.TestCase):
    """The KILLER test: the real migration pair, sanitized."""

    def test_legacy_transform_equals_real_migrated_output(self):
        legacy = _load("groundtruth_legacy.json")
        expected = _load("groundtruth_migrated.json")
        document, warn = transform_legacy(legacy)
        self.assertEqual(warn, ())
        self.assertEqual(document, expected)
        self.assertEqual(document["$schema"], OMO_SCHEMA_URL)
        self.assertEqual(list(document), ["$schema", "[opencode]"])
        self.assertNotIn("_migrations", document)

    def test_legacy_fixture_is_legacy_shaped_and_sanitized(self):
        legacy = _load("groundtruth_legacy.json")
        self.assertEqual(legacy["$schema"], OLD_LEGACY_SCHEMA)
        self.assertNotIn("[opencode]", legacy)  # flat legacy layout
        agents = legacy["agents"]["sisyphus"]
        self.assertIn("variant", agents)  # legacy-only field kept
        self.assertEqual(
            agents["fallback_models"],
            [{"model": "provider-2/model-2"}, {"model": "provider-3/model-3"}],
        )

    def test_empty_legacy_transforms_to_empty_opencode_block(self):
        document, warn = transform_legacy({})
        self.assertEqual(
            document, {"$schema": OMO_SCHEMA_URL, "[opencode]": {}})
        self.assertEqual(warn, ())

    def test_non_dict_raw_returns_graceful_error_tuple(self):
        for bad in (None, [], ["x"], "text", 42):
            with self.subTest(raw=bad):
                self.assertEqual(
                    transform_legacy(bad),
                    ({}, ("legacy document is not an object",)),
                )


class UpstreamConstants(unittest.TestCase):
    def test_agent_name_map_reproduces_upstream_exactly(self):
        self.assertEqual(
            AGENT_NAME_MAP,
            {
                "omo": "sisyphus",
                "OmO": "sisyphus",
                "Sisyphus": "sisyphus",
                "Sisyphus (Ultraworker)": "sisyphus",
                "sisyphus": "sisyphus",
                "Hephaestus (Deep Agent)": "hephaestus",
                "OmO-Plan": "prometheus",
                "omo-plan": "prometheus",
                "Planner-Sisyphus": "prometheus",
                "planner-sisyphus": "prometheus",
                "Prometheus - Plan Builder": "prometheus",
                "Prometheus (Plan Builder)": "prometheus",
                "prometheus": "prometheus",
                "orchestrator-sisyphus": "atlas",
                "Atlas": "atlas",
                "Atlas (Plan Executor)": "atlas",
                "atlas": "atlas",
                "plan-consultant": "metis",
                "Metis - Plan Consultant": "metis",
                "Metis (Plan Consultant)": "metis",
                "metis": "metis",
                "Momus - Plan Critic": "momus",
                "Momus (Plan Critic)": "momus",
                "momus": "momus",
                "Sisyphus-Junior": "sisyphus-junior",
                "sisyphus-junior": "sisyphus-junior",
                "build": "build",
                "oracle": "oracle",
                "librarian": "librarian",
                "explore": "explore",
                "multimodal-looker": "multimodal-looker",
            },
        )

    def test_hook_name_map_reproduces_upstream_exactly(self):
        self.assertEqual(
            HOOK_NAME_MAP,
            {
                "anthropic-auto-compact":
                    "anthropic-context-window-limit-recovery",
                "sisyphus-orchestrator": "atlas",
                "sisyphus-gpt-hephaestus-reminder": "no-sisyphus-gpt",
                "empty-message-sanitizer": None,
                "delegate-task-english-directive": None,
                "gpt-permission-continuation": None,
                "thinking-block-validator": None,
                "session-recovery": None,
            },
        )

    def test_model_version_map_reproduces_upstream_exactly(self):
        self.assertEqual(
            MODEL_VERSION_MAP,
            {"anthropic/claude-opus-4-4": "anthropic/claude-opus-4-8"},
        )


class StripMetadata(unittest.TestCase):
    def test_removes_top_level_metadata_keys_and_keeps_rest(self):
        raw = {
            "$schema": OLD_LEGACY_SCHEMA,
            "_migrations": ["a"],
            "appliedMigrations": ["b"],
            "model_fallback": True,
            "agents": {"build": {"model": "m"}},
        }
        stripped = strip_metadata(raw)
        self.assertEqual(stripped, {"model_fallback": True,
                                    "agents": {"build": {"model": "m"}}})
        # purity: returns a NEW dict; the input keeps its metadata
        self.assertIn("$schema", raw)
        stripped["agents"] = None
        self.assertEqual(raw["agents"], {"build": {"model": "m"}})

    def test_metadata_free_document_passes_through(self):
        raw = {"model_fallback": False}
        self.assertEqual(strip_metadata(raw), {"model_fallback": False})


class RenameAgents(unittest.TestCase):
    def test_display_name_exact_match(self):
        agents = {"Prometheus (Plan Builder)": {"model": "a"},
                  "Metis - Plan Consultant": {"model": "b"}}
        self.assertEqual(
            rename_agents(agents),
            {"prometheus": {"model": "a"}, "metis": {"model": "b"}},
        )

    def test_lowercase_match_when_exact_misses(self):
        self.assertEqual(
            rename_agents({"SISYPHUS": {"model": "a"}, "ORACLE": {}}),
            {"sisyphus": {"model": "a"}, "oracle": {}},
        )

    def test_unmapped_name_passes_through(self):
        self.assertEqual(
            rename_agents({"custom-agent": {"model": "a"}}),
            {"custom-agent": {"model": "a"}},
        )

    def test_collision_later_route_overwrites_earlier(self):
        # documented behavior: both keys map to "sisyphus"; the LATER
        # route in iteration order wins
        agents = {"omo": {"model": "early"}, "Sisyphus": {"model": "late"}}
        self.assertEqual(rename_agents(agents), {"sisyphus": {"model": "late"}})

    def test_purity_input_not_mutated(self):
        agents = {"omo": {"model": "a"}}
        rename_agents(agents)
        self.assertEqual(agents, {"omo": {"model": "a"}})


class BumpModelVersions(unittest.TestCase):
    def test_bumps_route_level_string_model(self):
        routes = {
            "sisyphus": {"model": "anthropic/claude-opus-4-4",
                         "fallback_models": ["anthropic/claude-opus-4-4"]},
            "build": {"model": "other/model"},
        }
        bumped = bump_model_versions(routes)
        self.assertEqual(
            bumped["sisyphus"]["model"], "anthropic/claude-opus-4-8")
        # entries inside fallback lists are NOT bumped (route-level only)
        self.assertEqual(
            bumped["sisyphus"]["fallback_models"],
            ["anthropic/claude-opus-4-4"],
        )
        self.assertEqual(bumped["build"]["model"], "other/model")

    def test_non_string_or_missing_model_left_alone(self):
        routes = {"a": {"model": None}, "b": {"description": "x"},
                  "c": {"model": {"nested": True}}}
        self.assertEqual(bump_model_versions(routes), routes)

    def test_purity_input_not_mutated(self):
        routes = {"a": {"model": "anthropic/claude-opus-4-4"}}
        bump_model_versions(routes)
        self.assertEqual(routes["a"]["model"], "anthropic/claude-opus-4-4")


class RenameKeys(unittest.TestCase):
    def test_omo_agent_renamed_value_preserved(self):
        config = {"omo_agent": "claude-code", "agents": {}}
        self.assertEqual(
            rename_keys(config),
            {"sisyphus_agent": "claude-code", "agents": {}},
        )

    def test_omo_agent_overwrites_existing_sisyphus_agent(self):
        config = {"sisyphus_agent": "old", "omo_agent": "new"}
        self.assertEqual(rename_keys(config), {"sisyphus_agent": "new"})

    def test_lsp_deleted(self):
        self.assertEqual(rename_keys({"lsp": {}, "a": 1}), {"a": 1})

    def test_hashline_edit_promoted_and_empty_experimental_dropped(self):
        config = {"experimental": {"hashline_edit": True}}
        self.assertEqual(rename_keys(config), {"hashline_edit": True})

    def test_non_empty_experimental_preserved_without_hashline_edit(self):
        config = {"experimental": {"hashline_edit": True, "other": 2}}
        self.assertEqual(
            rename_keys(config),
            {"experimental": {"other": 2}, "hashline_edit": True},
        )

    def test_existing_top_level_hashline_edit_wins(self):
        config = {"hashline_edit": False,
                  "experimental": {"hashline_edit": True}}
        self.assertEqual(
            rename_keys(config), {"hashline_edit": False})

    def test_no_experimental_no_crash(self):
        self.assertEqual(rename_keys({"model_fallback": True}),
                         {"model_fallback": True})


class RemapDisabled(unittest.TestCase):
    def test_disabled_agents_renamed_exact_then_lower(self):
        config = {
            "disabled_agents": [
                "Sisyphus (Ultraworker)", "SISYPHUS-JUNIOR", "unknown", 7,
            ],
        }
        self.assertEqual(
            remap_disabled(config),
            {"disabled_agents": ["sisyphus", "sisyphus-junior", "unknown", 7]},
        )

    def test_disabled_hooks_renamed_and_null_dropped(self):
        config = {
            "disabled_hooks": [
                "anthropic-auto-compact", "sisyphus-orchestrator",
                "empty-message-sanitizer", "unknown-hook", 3,
            ],
        }
        self.assertEqual(
            remap_disabled(config),
            {"disabled_hooks": [
                "anthropic-context-window-limit-recovery", "atlas",
                "unknown-hook", 3,
            ]},
        )

    def test_fully_null_dropped_hook_list_stays_empty_list(self):
        # defined behavior: the key is KEPT with an empty list (mirrors
        # upstream flatMap assignment), not removed
        config = {"disabled_hooks": ["session-recovery",
                                     "gpt-permission-continuation"]}
        self.assertEqual(remap_disabled(config), {"disabled_hooks": []})

    def test_absent_keys_not_invented(self):
        self.assertEqual(remap_disabled({"agents": {}}), {"agents": {}})

    def test_non_list_values_untouched(self):
        config = {"disabled_agents": "nope", "disabled_hooks": {"a": 1}}
        self.assertEqual(remap_disabled(config), config)


class NormalizeModelEntry(unittest.TestCase):
    PATH = ("agents", "sisyphus")

    def test_variant_alone_becomes_reasoning_no_warning(self):
        entry, warn = normalize_model_entry(
            {"model": "m", "variant": "max"}, self.PATH)
        self.assertEqual(entry, {"model": "m", "reasoning": "max"})
        self.assertEqual(warn, ())

    def test_precedence_reasoning_beats_reasoningEffort(self):
        entry, warn = normalize_model_entry(
            {"model": "m", "reasoning": "high", "reasoningEffort": "low"},
            self.PATH)
        self.assertEqual(entry, {"model": "m", "reasoning": "high"})
        self.assertEqual(
            warn,
            ("conflict: agents.sisyphus dropped reasoningEffort='low' "
             "kept reasoning='high'",),
        )

    def test_precedence_reasoningEffort_beats_variant(self):
        entry, warn = normalize_model_entry(
            {"model": "m", "reasoningEffort": "low", "variant": "max"},
            self.PATH)
        self.assertEqual(entry, {"model": "m", "reasoning": "low"})
        self.assertEqual(
            warn,
            ("conflict: agents.sisyphus dropped variant='max' "
             "kept reasoningEffort='low'",),
        )

    def test_three_way_conflict_warning_exact_format(self):
        entry, warn = normalize_model_entry(
            {"model": "m", "reasoning": "high", "reasoningEffort": "low",
             "variant": "max"},
            self.PATH)
        self.assertEqual(entry, {"model": "m", "reasoning": "high"})
        self.assertEqual(
            warn,
            ("conflict: agents.sisyphus dropped reasoningEffort='low' "
             "variant='max' kept reasoning='high'",),
        )

    def test_thinking_disabled_maps_to_reasoning_off(self):
        entry, warn = normalize_model_entry(
            {"model": "m", "thinking": {"type": "disabled"}}, self.PATH)
        self.assertEqual(entry, {"model": "m", "reasoning": "off"})
        self.assertEqual(warn, ())

    def test_thinking_disabled_does_not_override_explicit_reasoning(self):
        entry, warn = normalize_model_entry(
            {"model": "m", "reasoning": "high",
             "thinking": {"type": "disabled"}},
            self.PATH)
        self.assertEqual(entry, {"model": "m", "reasoning": "high"})
        self.assertEqual(warn, ())

    def test_thinking_enabled_folds_into_provider_options(self):
        thinking = {"type": "enabled", "budgetTokens": 4096}
        entry, warn = normalize_model_entry(
            {"model": "m", "thinking": thinking}, self.PATH)
        self.assertEqual(
            entry, {"model": "m", "provider_options": {"thinking": thinking}})
        self.assertEqual(warn, ())

    def test_textVerbosity_folds_into_provider_options(self):
        entry, warn = normalize_model_entry(
            {"model": "m", "textVerbosity": "low"}, self.PATH)
        self.assertEqual(
            entry, {"model": "m", "provider_options": {"textVerbosity": "low"}})
        self.assertEqual(warn, ())

    def test_maxTokens_renamed_when_max_tokens_absent(self):
        entry, warn = normalize_model_entry(
            {"model": "m", "maxTokens": 8192}, self.PATH)
        self.assertEqual(entry, {"model": "m", "max_tokens": 8192})
        self.assertEqual(warn, ())

    def test_max_tokens_wins_over_maxTokens(self):
        entry, warn = normalize_model_entry(
            {"model": "m", "max_tokens": 1024, "maxTokens": 8192}, self.PATH)
        self.assertEqual(entry, {"model": "m", "max_tokens": 1024})
        self.assertEqual(warn, ())

    def test_providerOptions_merges_into_provider_options(self):
        entry, warn = normalize_model_entry(
            {"model": "m", "providerOptions": {"b": 2}}, self.PATH)
        self.assertEqual(
            entry, {"model": "m", "provider_options": {"b": 2}})
        self.assertEqual(warn, ())

    def test_providerOptions_and_provider_options_merge(self):
        # "merges into": camel entries overlay snake entries
        entry, warn = normalize_model_entry(
            {"model": "m", "provider_options": {"a": 1, "shared": "snake"},
             "providerOptions": {"b": 2, "shared": "camel"}},
            self.PATH)
        self.assertEqual(
            entry,
            {"model": "m",
             "provider_options": {"a": 1, "b": 2, "shared": "camel"}},
        )
        self.assertEqual(warn, ())

    def test_unknown_keys_preserved_and_no_fields_invented(self):
        entry, warn = normalize_model_entry(
            {"model": "m", "temperature": 0.7, "top_p": 0.5}, self.PATH)
        self.assertEqual(
            entry, {"model": "m", "temperature": 0.7, "top_p": 0.5})
        self.assertEqual(warn, ())

    def test_all_drop_keys_removed_in_one_pass(self):
        entry, warn = normalize_model_entry(
            {"model": "m", "variant": "max",
             "thinking": {"type": "disabled"},
             "textVerbosity": "low", "maxTokens": 8, "providerOptions": {}},
            self.PATH)
        self.assertEqual(
            entry,
            {"model": "m", "reasoning": "max",
             "provider_options": {"textVerbosity": "low"}, "max_tokens": 8},
        )
        self.assertEqual(warn, ())

    def test_purity_input_not_mutated(self):
        original = {"model": "m", "variant": "max"}
        entry, _ = normalize_model_entry(original, self.PATH)
        self.assertEqual(original, {"model": "m", "variant": "max"})
        entry["model"] = "changed"
        self.assertEqual(original["model"], "m")


class TransformLegacyPipeline(unittest.TestCase):
    def test_metadata_stripped_and_document_wrapped(self):
        legacy = {
            "$schema": OLD_LEGACY_SCHEMA,
            "_migrations": ["x"],
            "appliedMigrations": ["y"],
            "model_fallback": True,
        }
        document, warn = transform_legacy(legacy)
        self.assertEqual(
            document,
            {"$schema": OMO_SCHEMA_URL,
             "[opencode]": {"model_fallback": True}},
        )
        self.assertEqual(warn, ())

    def test_agent_rename_collision_inside_pipeline(self):
        legacy = {
            "agents": {
                "omo": {"model": "a", "variant": "max"},
                "Sisyphus": {"model": "b"},
            },
        }
        document, _ = transform_legacy(legacy)
        block = document["[opencode]"]["agents"]
        self.assertEqual(block, {"sisyphus": {"model": "b"}})

    def test_disabled_remap_flows_through_pipeline(self):
        legacy = {
            "disabled_agents": ["orchestrator-sisyphus"],
            "disabled_hooks": ["session-recovery"],
        }
        document, _ = transform_legacy(legacy)
        block = document["[opencode]"]
        self.assertEqual(block["disabled_agents"], ["atlas"])
        self.assertEqual(block["disabled_hooks"], [])

    def test_rename_keys_flow_through_pipeline(self):
        legacy = {"omo_agent": "cc", "lsp": {"foo": 1},
                  "experimental": {"hashline_edit": True}}
        document, _ = transform_legacy(legacy)
        self.assertEqual(
            document["[opencode]"], {"sisyphus_agent": "cc",
                                     "hashline_edit": True},
        )

    def test_model_bump_flows_through_pipeline(self):
        legacy = {
            "agents": {"sisyphus":
                       {"model": "anthropic/claude-opus-4-4",
                        "fallback_models":
                            [{"model": "anthropic/claude-opus-4-4"}]}},
            "categories": {"quick":
                           {"model": "anthropic/claude-opus-4-4",
                            "fallback_models": []}},
        }
        document, _ = transform_legacy(legacy)
        block = document["[opencode]"]
        self.assertEqual(
            block["agents"]["sisyphus"]["model"],
            "anthropic/claude-opus-4-8")
        # fallback entries are NOT bumped, and the bumped category primary
        # flows into models[0]
        self.assertEqual(
            block["agents"]["sisyphus"]["fallback_models"],
            ["anthropic/claude-opus-4-4"],
        )
        self.assertEqual(
            block["categories"]["quick"]["models"],
            ["anthropic/claude-opus-4-8"],
        )

    def test_category_model_and_fallback_models_merge_into_models(self):
        legacy = {
            "categories": {
                "cat": {
                    "model": "m-primary",
                    "variant": "high",
                    "fallback_models": [
                        {"model": "m1"},
                        {"model": "m2", "variant": "max"},
                    ],
                },
            },
        }
        document, warn = transform_legacy(legacy)
        self.assertEqual(
            document["[opencode]"]["categories"]["cat"],
            {"models": [
                {"model": "m-primary", "reasoning": "high"},
                {"model": "m1"},          # {model}-only stays an object here
                {"model": "m2", "reasoning": "max"},
            ]},
        )
        self.assertEqual(warn, ())

    def test_category_primary_without_settings_becomes_bare_string(self):
        legacy = {"categories": {"cat": {"model": "m",
                                         "fallback_models": [{"model": "f"}]}}}
        document, _ = transform_legacy(legacy)
        self.assertEqual(
            document["[opencode]"]["categories"]["cat"]["models"],
            ["m", {"model": "f"}],
        )

    def test_category_already_having_models_left_structurally_alone(self):
        legacy = {"categories": {"cat": {"models": ["plain/model"]}}}
        document, warn = transform_legacy(legacy)
        # bare-string entries untouched by normalize
        self.assertEqual(
            document["[opencode]"]["categories"]["cat"],
            {"models": ["plain/model"]},
        )
        self.assertEqual(warn, ())

    def test_agent_single_model_fallback_dict_collapses_to_string(self):
        legacy = {
            "agents": {"sisyphus": {
                "model": "m",
                "fallback_models": [{"model": "f1"},
                                    {"model": "f2", "variant": "max"}],
            }},
        }
        document, _ = transform_legacy(legacy)
        self.assertEqual(
            document["[opencode]"]["agents"]["sisyphus"]["fallback_models"],
            ["f1", {"model": "f2", "reasoning": "max"}],
        )

    def test_fallback_dict_without_model_key_untouched_no_crash(self):
        legacy = {
            "agents": {"sisyphus": {
                "model": "m",
                "fallback_models": [{"note": "not a model ref"}],
            }},
        }
        document, warn = transform_legacy(legacy)
        self.assertEqual(
            document["[opencode]"]["agents"]["sisyphus"]["fallback_models"],
            [{"note": "not a model ref"}],
        )
        self.assertEqual(warn, ())

    def test_conflict_warning_path_pinned_at_pipeline_level(self):
        legacy = {
            "agents": {"sisyphus": {
                "model": "m",
                "fallback_models": [
                    {"model": "f", "reasoning": "high", "variant": "max"},
                ],
            }},
        }
        _, warn = transform_legacy(legacy)
        self.assertEqual(
            warn,
            ("conflict: agents.sisyphus.fallback_models.0 dropped "
             "variant='max' kept reasoning='high'",),
        )

    def test_purity_legacy_document_not_mutated(self):
        legacy = _load("groundtruth_legacy.json")
        snapshot = json.loads(json.dumps(legacy))
        transform_legacy(legacy)
        self.assertEqual(legacy, snapshot)


class DiscoverLegacy(unittest.TestCase):
    def test_globs_both_prefixes_excludes_bak_dedupes_sorts(self):
        with TemporaryDirectory() as tmp:
            legacy_dir = Path(tmp) / ".config" / "opencode"
            legacy_dir.mkdir(parents=True)
            names = [
                "oh-my-openagent.json",
                "oh-my-openagent-fast.json",
                "oh-my-opencode-night.json",
                "oh-my-openagent-fast.json.BAK",
                "oh-my-openagent-old.json.bak",  # non-.json suffix: not globbed
                "unrelated.json",
                "oh-my-openagentX.json",          # glob *-adjacent
            ]
            for name in names:
                (legacy_dir / name).write_text("{}", encoding="utf-8")
            paths = Paths.build(Path(tmp))
            found = discover_legacy(paths)
            self.assertEqual(
                [p.name for p in found],
                [
                    "oh-my-openagent-fast.json",
                    "oh-my-openagent.json",
                    "oh-my-openagentX.json",
                    "oh-my-opencode-night.json",
                ],
            )

    def test_missing_legacy_dir_returns_empty_list(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(discover_legacy(Paths.build(Path(tmp))), [])


class DeriveProfileName(unittest.TestCase):
    def test_canonical_bare_stems_map_to_default(self):
        for name in ("oh-my-openagent.json", "oh-my-opencode.json"):
            with self.subTest(name=name):
                self.assertEqual(
                    derive_profile_name(Path("/x") / name), "default")

    def test_preset_names_strip_the_prefix(self):
        cases = {
            "oh-my-openagent-fast.json": "fast",
            "oh-my-opencode-night.json": "night",
            "oh-my-openagent-pre-1.json": "pre-1",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    derive_profile_name(Path("/x") / name), expected)

    def test_unprefixed_stem_passes_through(self):
        self.assertEqual(
            derive_profile_name(Path("/x/other.json")), "other")


if __name__ == "__main__":
    unittest.main()
