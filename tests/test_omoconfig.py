"""Tests for the omo.jsonc domain model: Paths, OmoDocument, LoadError,
replace_sections merge semantics, and summarize_routes route summaries.

Contract notes (binding for Tasks 4/5/6/11):
- ``load_omo_document`` reads the file exactly once; every later consumer
  (``.harness``, ``replace_sections``, ``summarize_routes``) works purely
  from the parsed ``OmoDocument.raw`` dict (no reread).
- ``replace_sections`` ordering: existing keys keep their ORIGINAL target
  insertion position; new profile keys append at the end in profile order;
  control keys (``profiles``/``_migrations``) always come from the target;
  ``$schema`` is always first (target's, else profile's, else
  ``OMO_SCHEMA_URL``).
- Route summaries reuse ``config.ModelSpec``/``config.RouteSummary`` shapes;
  per-route warnings live in ``RouteSummary.warnings``.  SKIPPED non-dict
  route blocks surface their ``{section}.{name}: expected object, got {type}``
  warning through the stdlib ``warnings`` channel because the binding
  two-tuple return signature has no slot for section-level warnings.
"""

import os
import unittest
import warnings as warnings_module
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from opencode_config_switcher.config import ModelSpec, RouteSummary
from opencode_config_switcher.omoconfig import (
    CONTROL_KEYS,
    HARNESS_BLOCKS,
    OMO_SCHEMA_URL,
    LoadError,
    OmoDocument,
    load_omo_document,
    replace_sections,
    summarize_routes,
)
from opencode_config_switcher.paths import DEFAULT, Paths

SCHEMA_A = "https://example.com/schema-a"
SCHEMA_B = "https://example.com/schema-b"


class ConstantsContract(unittest.TestCase):
    def test_constants_exact(self):
        self.assertEqual(
            OMO_SCHEMA_URL,
            "https://raw.githubusercontent.com/code-yeongyu/"
            "oh-my-openagent/dev/assets/omo.schema.json",
        )
        self.assertEqual(CONTROL_KEYS, ("profiles", "_migrations"))
        self.assertEqual(HARNESS_BLOCKS, ("[opencode]", "[codex]", "[senpi]"))


class PathsContract(unittest.TestCase):
    def test_build_field_values_for_temp_home(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = Paths.build(home)
            self.assertEqual(paths.home, home)
            self.assertEqual(paths.omo_path, home / ".omo" / "omo.jsonc")
            self.assertEqual(paths.omo_backup,
                             home / ".omo" / "omo.jsonc.BAK")
            self.assertEqual(paths.profiles_dir, home / ".omo" / "profiles")
            self.assertEqual(paths.active_marker,
                             home / ".omo" / "profiles" / ".active")
            self.assertEqual(paths.legacy_dir,
                             home / ".config" / "opencode")

    def test_build_is_pure_path_math(self):
        with TemporaryDirectory() as tmp:
            paths = Paths.build(Path(tmp))
            self.assertFalse(paths.omo_path.parent.exists())
            self.assertFalse(paths.profiles_dir.exists())

    def test_default_matches_path_home(self):
        self.assertIsInstance(DEFAULT, Paths)
        self.assertEqual(DEFAULT, Paths.build(Path.home()))

    def test_paths_is_frozen(self):
        paths = Paths.build(Path("/tmp/x"))
        with self.assertRaises(Exception):
            paths.home = Path("/elsewhere")  # type: ignore[misc]


class LoadOmoDocumentTests(unittest.TestCase):
    def test_round_trip_jsonc_with_comments_and_trailing_commas(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "omo.jsonc"
            p.write_text(
                "// OMO configuration\n"
                "{\n"
                '  "$schema": "https://example.com/s", // canonical\n'
                '  "[opencode]": {"model_fallback": true,},\n'
                '  "_migrations": ["m1", "m2"],\n'
                "}\n",
                encoding="utf-8",
            )
            doc = load_omo_document(p)
            self.assertIsInstance(doc, OmoDocument)
            self.assertNotIsInstance(doc, LoadError)
            self.assertEqual(doc.schema, "https://example.com/s")
            self.assertEqual(doc.migrations, ("m1", "m2"))
            self.assertEqual(doc.harness("[opencode]"),
                             {"model_fallback": True})
            self.assertIsNone(doc.harness("[codex]"))

    def test_missing_file_is_load_error_not_raise(self):
        p = Path("/nonexistent/definitely/omo.jsonc")
        result = load_omo_document(p)
        self.assertIsInstance(result, LoadError)
        self.assertEqual(result.path, p)
        self.assertEqual(result.message, f"File not found: {p}")

    def test_malformed_jsonc_is_load_error(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.jsonc"
            p.write_text('{"a": ', encoding="utf-8")
            result = load_omo_document(p)
            self.assertIsInstance(result, LoadError)
            self.assertIn("Invalid JSONC at line", result.message)

    def test_unreadable_file_is_load_error(self):
        if os.geteuid() == 0:
            self.skipTest("chmod 000 stays readable when running as root")
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "locked.jsonc"
            p.write_text("{}", encoding="utf-8")
            p.chmod(0)
            try:
                result = load_omo_document(p)
            finally:
                p.chmod(0o600)
            self.assertIsInstance(result, LoadError)

    def test_directory_path_is_load_error(self):
        with TemporaryDirectory() as tmp:
            result = load_omo_document(Path(tmp))
            self.assertIsInstance(result, LoadError)
            self.assertIn("Cannot read", result.message)
            self.assertIn("IsADirectoryError", result.message)

    def test_non_dict_top_level_is_load_error(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "list.jsonc"
            p.write_text("[1, 2]", encoding="utf-8")
            result = load_omo_document(p)
            self.assertIsInstance(result, LoadError)
            self.assertIn("expected object at top level", result.message)


class OmoDocumentContract(unittest.TestCase):
    def test_migrations_variants(self):
        self.assertEqual(
            OmoDocument(raw={"_migrations": ["a", "b"]}).migrations,
            ("a", "b"),
        )
        self.assertEqual(OmoDocument(raw={}).migrations, ())
        self.assertEqual(OmoDocument(raw={"_migrations": "x"}).migrations, ())

    def test_control_profiles_variants(self):
        profiles = {"work": {}}
        doc = OmoDocument(raw={"profiles": profiles})
        self.assertIs(doc.control_profiles, profiles)
        self.assertIsNone(OmoDocument(raw={}).control_profiles)
        self.assertIsNone(
            OmoDocument(raw={"profiles": ["not", "a", "dict"]}
                        ).control_profiles)

    def test_harness_known_present_absent_non_dict_unknown(self):
        block = {"agents": {}}
        doc = OmoDocument(raw={"[opencode]": block, "[codex]": "broken"})
        self.assertIs(doc.harness("[opencode]"), block)
        self.assertIsNone(doc.harness("[codex]"))      # present, non-dict
        self.assertIsNone(doc.harness("[senpi]"))      # known name, absent
        self.assertIsNone(doc.harness("agents"))       # not a harness name


class ReadOnceTests(unittest.TestCase):
    def test_consumers_do_not_reread_after_load(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "omo.jsonc"
            p.write_text(
                '{"$schema": "https://example.com/s", "[opencode]": '
                '{"agents": {"a1": {"model": "m1"}}}}',
                encoding="utf-8",
            )
            doc = load_omo_document(p)
            self.assertIsInstance(doc, OmoDocument)
            with patch.object(Path, "read_text",
                              side_effect=OSError("no reread allowed")):
                self.assertEqual(doc.schema, "https://example.com/s")
                self.assertEqual(doc.migrations, ())
                self.assertIsNone(doc.control_profiles)
                self.assertIsNotNone(doc.harness("[opencode]"))
                merged = replace_sections(
                    doc, OmoDocument(raw={"[opencode]": {"agents": {}}}))
                self.assertEqual(merged["$schema"], "https://example.com/s")
                agents, categories = summarize_routes(doc.harness("[opencode]"))
                self.assertEqual(len(agents), 1)
                self.assertEqual(categories, ())


class ReplaceSectionsTests(unittest.TestCase):
    def test_profile_replaces_opencode_and_leaves_rest_untouched(self):
        target_raw = {
            "$schema": SCHEMA_A,
            "[opencode]": {"model_fallback": True},
            "[codex]": {"key": "codex"},
            "codegraph": {"enabled": False},
            "_migrations": ["m-1"],
            "profiles": {"work": {}},
        }
        target = OmoDocument(raw=target_raw)
        profile_block = {"model_fallback": False, "agents": {}}
        profile = OmoDocument(raw={
            "[opencode]": profile_block,
            "profiles": {"evil": True},
            "_migrations": ["evil-migration"],
        })

        merged = replace_sections(target, profile)

        self.assertIsInstance(merged, dict)
        self.assertEqual(
            list(merged),
            ["$schema", "[opencode]", "[codex]", "codegraph",
             "_migrations", "profiles"],
        )
        self.assertEqual(merged["$schema"], SCHEMA_A)
        self.assertEqual(merged["[opencode]"], profile_block)
        self.assertEqual(merged["[codex]"], {"key": "codex"})
        self.assertEqual(merged["codegraph"], {"enabled": False})
        self.assertEqual(merged["_migrations"], ["m-1"])       # from target
        self.assertEqual(merged["profiles"], {"work": {}})     # from target
        # target document itself stays untouched
        self.assertEqual(target_raw["[opencode]"], {"model_fallback": True})
        self.assertEqual(target_raw["profiles"], {"work": {}})

    def test_new_keys_append_at_end_in_profile_order(self):
        target = OmoDocument(raw={"$schema": SCHEMA_A, "alpha": 1})
        profile = OmoDocument(raw={"zulu": 26, "bravo": 2})  # zulu BEFORE bravo
        merged = replace_sections(target, profile)
        self.assertEqual(list(merged), ["$schema", "alpha", "zulu", "bravo"])
        self.assertEqual(merged["zulu"], 26)
        self.assertEqual(merged["bravo"], 2)

    def test_empty_target_yields_schema_first_then_profile_keys(self):
        profile = OmoDocument(raw={"[opencode]": {"a": 1}, "theme": "dark"})
        merged = replace_sections(OmoDocument(raw={}), profile)
        self.assertEqual(list(merged), ["$schema", "[opencode]", "theme"])
        self.assertEqual(merged["$schema"], OMO_SCHEMA_URL)

    def test_target_schema_wins_over_profile_schema(self):
        merged = replace_sections(
            OmoDocument(raw={"$schema": SCHEMA_A, "a": 1}),
            OmoDocument(raw={"$schema": SCHEMA_B}),
        )
        self.assertEqual(merged["$schema"], SCHEMA_A)

    def test_profile_schema_used_when_target_has_none(self):
        merged = replace_sections(
            OmoDocument(raw={"a": 1}),
            OmoDocument(raw={"$schema": SCHEMA_B}),
        )
        self.assertEqual(merged["$schema"], SCHEMA_B)
        self.assertEqual(list(merged), ["$schema", "a"])

    def test_both_schemas_absent_yields_canonical_url_first(self):
        merged = replace_sections(
            OmoDocument(raw={"a": 1}),
            OmoDocument(raw={"b": 2}),
        )
        self.assertEqual(merged["$schema"], OMO_SCHEMA_URL)
        self.assertEqual(list(merged), ["$schema", "a", "b"])

    def test_schema_moves_to_front_even_when_target_had_it_later(self):
        merged = replace_sections(
            OmoDocument(raw={"a": 1, "$schema": SCHEMA_A}),
            OmoDocument(raw={}),
        )
        self.assertEqual(list(merged), ["$schema", "a"])

    def test_profile_control_keys_never_land(self):
        target = OmoDocument(raw={"keep": 1})
        profile = OmoDocument(raw={"profiles": {"x": 1}, "_migrations": ["m"]})
        merged = replace_sections(target, profile)
        self.assertNotIn("profiles", merged)
        self.assertNotIn("_migrations", merged)
        self.assertEqual(merged, {"$schema": OMO_SCHEMA_URL, "keep": 1})


class SummarizeRoutesTests(unittest.TestCase):
    FIXTURE = {
        "agents": {
            "a-string": {
                "model": "m1",
                "fallback_models": ["f1", "f2"],
            },
            "a-obj": {
                "model": "m2",
                "variant": "v",
                "fallback_models": [{"model": "f3", "reasoning": "high"}],
            },
            "a-mixed": {
                "model": "m3",
                "fallback_models": [
                    "f4",
                    {"model": "f5", "reasoningEffort": "low"},
                ],
            },
            "a-both": {
                "model": "m4",
                "models": ["b1", {"model": "b2"}],
                "fallback_models": ["f6"],
            },
            "a-bad": "not-a-dict",
        },
        "categories": {
            "c-list": {
                "models": ["m5", {"model": "m6", "reasoning": "max"}],
            },
            "c-empty": {"models": []},
        },
    }

    def test_mixed_fixture_exact_tuples_and_warnings(self):
        with warnings_module.catch_warnings(record=True) as caught:
            warnings_module.simplefilter("always")
            agents, categories = summarize_routes(self.FIXTURE)

        self.assertEqual(agents, (
            RouteSummary(
                name="a-string",
                primary=ModelSpec(model="m1"),
                fallbacks=(ModelSpec(model="f1"), ModelSpec(model="f2")),
                warnings=(),
            ),
            RouteSummary(
                name="a-obj",
                primary=ModelSpec(model="m2", variant="v"),
                fallbacks=(ModelSpec(model="f3", reasoning="high"),),
                warnings=(),
            ),
            RouteSummary(
                name="a-mixed",
                primary=ModelSpec(model="m3"),
                fallbacks=(ModelSpec(model="f4"),
                           ModelSpec(model="f5", reasoning_effort="low")),
                warnings=(),
            ),
            RouteSummary(
                name="a-both",
                primary=ModelSpec(model="m4"),
                # models entries first (order preserved), then fallback_models
                fallbacks=(ModelSpec(model="b1"), ModelSpec(model="b2"),
                           ModelSpec(model="f6")),
                warnings=(),
            ),
        ))
        self.assertEqual(categories, (
            RouteSummary(
                name="c-list",
                primary=ModelSpec(model="m5"),
                fallbacks=(ModelSpec(model="m6", reasoning="max"),),
                warnings=(),
            ),
            RouteSummary(
                name="c-empty",
                primary=ModelSpec(model=None),
                fallbacks=(),
                warnings=("categories.c-empty: empty models list",),
            ),
        ))

        # malformed route block: skipped from tuples, warned via stdlib channel
        self.assertNotIn("a-bad", [route.name for route in agents])
        self.assertIn(
            "agents.a-bad: expected object, got str",
            [str(entry.message) for entry in caught],
        )

    def test_none_harness_returns_empty_tuples(self):
        self.assertEqual(summarize_routes(None), ((), ()))

    def test_missing_sections_return_empty_tuples(self):
        agents, categories = summarize_routes({"model_fallback": True})
        self.assertEqual(agents, ())
        self.assertEqual(categories, ())


class CanonicalAgentFormBTests(unittest.TestCase):
    def test_object_primary_with_reasoning_and_string_fallback(self):
        agents, _ = summarize_routes({"agents": {"sisyphus": {
            "models": [{"model": "p/m", "reasoning": "max"}, "a/b"]}}})
        self.assertEqual(agents, (
            RouteSummary(
                name="sisyphus",
                primary=ModelSpec(model="p/m", reasoning="max"),
                fallbacks=(ModelSpec(model="a/b"),),
                warnings=(),
            ),
        ))

    def test_object_primary_keeps_variant_and_effort(self):
        agents, _ = summarize_routes({"agents": {"atlas": {
            "models": [
                {"model": "p/m", "variant": "big",
                 "reasoningEffort": "high"},
                {"model": "f/1", "reasoning": "low"},
                "f/2",
            ]}}})
        self.assertEqual(agents, (
            RouteSummary(
                name="atlas",
                primary=ModelSpec(model="p/m", variant="big",
                                  reasoning_effort="high"),
                fallbacks=(ModelSpec(model="f/1", reasoning="low"),
                           ModelSpec(model="f/2")),
                warnings=(),
            ),
        ))

    def test_single_string_entry_is_primary_without_fallbacks(self):
        agents, _ = summarize_routes({"agents": {"solo": {
            "models": ["only/m"]}}})
        self.assertEqual(agents, (
            RouteSummary(
                name="solo",
                primary=ModelSpec(model="only/m"),
                fallbacks=(),
                warnings=(),
            ),
        ))

    def test_legacy_fallback_models_append_after_models_chain(self):
        agents, _ = summarize_routes({"agents": {"metis": {
            "models": ["m1", "m2"],
            "fallback_models": ["f1"]}}})
        self.assertEqual(agents, (
            RouteSummary(
                name="metis",
                primary=ModelSpec(model="m1"),
                fallbacks=(ModelSpec(model="m2"), ModelSpec(model="f1")),
                warnings=(),
            ),
        ))


if __name__ == "__main__":
    unittest.main()
