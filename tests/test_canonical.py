"""Tests for the canonical models-chain helpers (``transform.py``).

Binding contract (consumed by the Task 3 editor write path, the Task 5
legacy-import transform, and the Task 7 ``migrate`` subcommand):
- ``canonicalize_definition`` folds legacy ``model``(+settings) +
  ``fallback_models`` into a canonical ``models`` chain with OMO parity
  (oh-my-openagent dist/cli-node/index.js:87447-87491 ``normalizeDefinition``,
  87418-87435 ``primaryModelRef``): primary first, existing ``models`` second
  (``kind == "agent"`` only — category chains replace, never carry over),
  fallbacks last; every dict entry passes through ``normalize_model_entry``;
  entries reducing to ``{"model": ...}`` alone collapse to bare strings.
- Blocks without ``fallback_models`` are returned unchanged (shallow copy).
- ``definition_needs_migration`` is true iff ``fallback_models`` is present.
- ``migrate_document`` rewrites ``agents``/``categories`` routes under every
  harness-shaped (``[...]``) key, returns ``(new_document, converted_count)``,
  and never touches ``model_fallback`` / ``runtime_fallback`` / catalog keys.
- All helpers are pure: inputs are never mutated.
"""

import json
import unittest

from opencode_config_switcher.transform import (
    canonicalize_definition,
    definition_needs_migration,
    migrate_document,
)


class CanonicalizeAgentDefinition(unittest.TestCase):
    def test_full_legacy_agent_folds_into_canonical_chain(self):
        block = {
            "model": "p/m",
            "reasoning": "max",
            "fallback_models": [{"model": "a/b", "reasoning": "max"}, "c/d"],
        }
        self.assertEqual(
            canonicalize_definition(block, "agent"),
            {"models": [
                {"model": "p/m", "reasoning": "max"},
                {"model": "a/b", "reasoning": "max"},
                "c/d",
            ]},
        )

    def test_model_only_agent_returned_unchanged_shallow_copy(self):
        block = {"model": "p/m", "reasoning": "max", "description": "keep"}
        result = canonicalize_definition(block, "agent")
        self.assertEqual(result, block)
        self.assertIsNot(result, block)

    def test_mixed_agent_primary_existing_fallbacks_order(self):
        block = {
            "model": "p/m",
            "models": ["x/y", {"model": "x/z", "variant": "high"}],
            "fallback_models": ["f/1"],
        }
        self.assertEqual(
            canonicalize_definition(block, "agent"),
            {"models": [
                "p/m",
                "x/y",
                {"model": "x/z", "reasoning": "high"},
                "f/1",
            ]},
        )

    def test_agent_without_model_has_no_primary(self):
        block = {"fallback_models": ["f/1", {"model": "f/2"}]}
        self.assertEqual(
            canonicalize_definition(block, "agent"),
            {"models": ["f/1", "f/2"]},
        )

    def test_model_only_dict_entries_collapse_to_strings(self):
        block = {"model": "p/m",
                 "fallback_models": [{"model": "a/b"}, {"model": "c/d"}]}
        self.assertEqual(canonicalize_definition(block, "agent"),
                         {"models": ["p/m", "a/b", "c/d"]})

    def test_definition_level_settings_keys_all_deleted(self):
        block = {
            "model": "p/m",
            "reasoning": "max",
            "variant": "high",
            "reasoningEffort": "low",
            "thinking": {"type": "enabled"},
            "textVerbosity": "low",
            "provider_options": {"a": 1},
            "providerOptions": {"b": 2},
            "fallback_models": [],
        }
        result = canonicalize_definition(block, "agent")
        self.assertEqual(set(result), {"models"})
        self.assertEqual(
            result["models"],
            [{"model": "p/m", "reasoning": "max",
              "provider_options": {"a": 1, "b": 2,
                                   "thinking": {"type": "enabled"},
                                   "textVerbosity": "low"}}],
        )

    def test_non_route_keys_preserved_verbatim(self):
        block = {
            "model": "p/m",
            "fallback_models": ["a/b"],
            "description": "desc",
            "tools": {"bash": True},
            "_comment": "note",
            "temperature": 0.4,
        }
        result = canonicalize_definition(block, "agent")
        self.assertEqual(result["description"], "desc")
        self.assertEqual(result["tools"], {"bash": True})
        self.assertEqual(result["_comment"], "note")
        self.assertEqual(result["temperature"], 0.4)
        self.assertEqual(result["models"], ["p/m", "a/b"])

    def test_fallback_models_string_wrapped_into_list(self):
        block = {"model": "p/m", "fallback_models": "a/b"}
        self.assertEqual(canonicalize_definition(block, "agent"),
                         {"models": ["p/m", "a/b"]})

    def test_fallback_dict_without_model_key_kept_verbatim(self):
        block = {"model": "p/m", "fallback_models": [{"note": "no ref"}]}
        self.assertEqual(canonicalize_definition(block, "agent"),
                         {"models": ["p/m", {"note": "no ref"}]})

    def test_purity_input_not_mutated(self):
        block = {"model": "p/m", "reasoning": "max",
                 "fallback_models": [{"model": "a/b", "variant": "max"}]}
        snapshot = json.loads(json.dumps(block))
        canonicalize_definition(block, "agent")
        self.assertEqual(block, snapshot)


class CanonicalizeCategoryDefinition(unittest.TestCase):
    def test_category_primary_plus_fallbacks_existing_models_dropped(self):
        # OMO parity (87475): category `existing` is always [] — prior
        # `models` entries are replaced by the primary+fallback chain
        block = {
            "model": "p/m",
            "models": ["x/y"],
            "fallback_models": ["a/b", {"model": "c/d"}],
        }
        self.assertEqual(
            canonicalize_definition(block, "category"),
            {"models": ["p/m", "a/b", "c/d"]},
        )

    def test_category_primary_with_settings_folds(self):
        block = {"model": "p/m", "variant": "high",
                 "fallback_models": [{"model": "a/b", "reasoning": "max"}],
                 "description": "cat"}
        self.assertEqual(
            canonicalize_definition(block, "category"),
            {"models": [{"model": "p/m", "reasoning": "high"},
                        {"model": "a/b", "reasoning": "max"}],
             "description": "cat"},
        )

    def test_category_model_only_returned_unchanged_shallow_copy(self):
        block = {"model": "p/m", "description": "cat"}
        result = canonicalize_definition(block, "category")
        self.assertEqual(result, block)
        self.assertIsNot(result, block)

    def test_category_model_only_dict_entries_collapse_to_strings(self):
        # canonicalize collapses (editor `_collapse_agent_entry` convention),
        # unlike the import-pipeline category path which never collapses
        block = {"model": "p/m", "fallback_models": [{"model": "a/b"}]}
        self.assertEqual(canonicalize_definition(block, "category"),
                         {"models": ["p/m", "a/b"]})


class DefinitionNeedsMigration(unittest.TestCase):
    def test_true_iff_fallback_models_present(self):
        self.assertTrue(definition_needs_migration({"fallback_models": []}))
        self.assertTrue(definition_needs_migration(
            {"model": "m", "fallback_models": ["a"]}))
        self.assertFalse(definition_needs_migration({"model": "m"}))
        self.assertFalse(definition_needs_migration({"models": ["a"]}))
        self.assertFalse(definition_needs_migration({}))

    def test_non_dict_blocks_are_false(self):
        for block in (None, "text", ["fallback_models"], 42):
            with self.subTest(block=block):
                self.assertFalse(definition_needs_migration(block))


class MigrateDocument(unittest.TestCase):
    def test_counts_only_converted_routes(self):
        document = {
            "[opencode]": {
                "agents": {
                    "sisyphus": {"model": "p/m", "fallback_models": ["a/b"]},
                    "build": {"model": "q/m"},
                    "oracle": {"models": ["r/m"]},
                },
                "categories": {
                    "quick": {"model": "s/m", "fallback_models": ["t/m"]},
                },
            },
        }
        migrated, count = migrate_document(document)
        self.assertEqual(count, 2)
        self.assertEqual(migrated["[opencode]"]["agents"]["sisyphus"],
                         {"models": ["p/m", "a/b"]})
        self.assertEqual(migrated["[opencode]"]["agents"]["build"],
                         {"model": "q/m"})
        self.assertEqual(migrated["[opencode]"]["agents"]["oracle"],
                         {"models": ["r/m"]})
        self.assertEqual(migrated["[opencode]"]["categories"]["quick"],
                         {"models": ["s/m", "t/m"]})

    def test_model_fallback_and_runtime_fallback_untouched_count_zero(self):
        document = {"[opencode]": {"model_fallback": True,
                                   "runtime_fallback": {"enabled": True}}}
        migrated, count = migrate_document(document)
        self.assertEqual(count, 0)
        self.assertEqual(migrated, document)

    def test_catalog_and_non_harness_keys_untouched(self):
        document = {
            "[opencode]": {"models": {"alias": "p/m"}},
            "model_fallback": True,
            "agents": {"a": {"fallback_models": ["b"]}},
            "profiles": {"x": {"agents": {"c": {"fallback_models": ["d"]}}}},
        }
        migrated, count = migrate_document(document)
        self.assertEqual(count, 0)
        self.assertEqual(migrated, document)
        # catalog alias is data, not a chain — never wrapped or renamed
        self.assertEqual(migrated["[opencode]"]["models"], {"alias": "p/m"})

    def test_multiple_harness_blocks_each_migrated(self):
        document = {
            "[opencode]": {"agents": {"a": {"fallback_models": ["x"]}}},
            "[codex]": {"categories": {"b": {"fallback_models": ["y"]}}},
        }
        migrated, count = migrate_document(document)
        self.assertEqual(count, 2)
        self.assertEqual(migrated["[opencode]"]["agents"]["a"],
                         {"models": ["x"]})
        self.assertEqual(migrated["[codex]"]["categories"]["b"],
                         {"models": ["y"]})

    def test_non_dict_harness_value_and_routes_pass_through(self):
        document = {"[opencode]": "not-a-block",
                    "[codex]": {"agents": {"a": "not-a-route"}}}
        migrated, count = migrate_document(document)
        self.assertEqual(count, 0)
        self.assertEqual(migrated, document)

    def test_empty_document_migrates_to_empty(self):
        self.assertEqual(migrate_document({}), ({}, 0))

    def test_purity_input_not_mutated(self):
        document = {
            "[opencode]": {
                "agents": {"a": {"model": "p/m",
                                 "fallback_models": [{"model": "a/b"}]}},
            },
        }
        snapshot = json.loads(json.dumps(document))
        migrated, _ = migrate_document(document)
        self.assertEqual(document, snapshot)
        self.assertIsNot(migrated["[opencode]"]["agents"]["a"],
                         document["[opencode]"]["agents"]["a"])


if __name__ == "__main__":
    unittest.main()
