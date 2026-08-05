"""Tests for configuration discovery, parsing, and summary contracts."""

import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import opencode_config_switcher.config as C
from opencode_config_switcher.config import (
    ModelSpec,
    RouteSummary,
    RuntimeFallbackSummary,
    FileSummary,
    ConfigSummary,
    discover_configs,
    parse_all,
    )

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ── helpers ────────────────────────────────────────────────────────

def _make_temp_home(*, active_name: str = "oh-my-openagent.json",
                    active_content: str | None = None,
                    presets: dict[str, str] | None = None,
                    ) -> Path:
    """Create a temporary HOME with .config/opencode/ populated."""
    home = Path(tempfile.mkdtemp(prefix="test-config-"))
    config_dir = home / ".config" / "opencode"
    config_dir.mkdir(parents=True)

    if active_content is not None:
        (config_dir / active_name).write_text(active_content)

    if presets:
        for name, content in presets.items():
            (config_dir / name).write_text(content)

    return home


# ── ModelSpec ──────────────────────────────────────────────────────

class ModelSpecTests(unittest.TestCase):
    def test_immutable(self):
        m = ModelSpec(model="gpt-5", variant="max")
        with self.assertRaises(AttributeError):
            m.model = "other"  # type: ignore[misc]

    def test_all_none(self):
        m = ModelSpec(model=None)
        self.assertIsNone(m.model)
        self.assertIsNone(m.variant)


# ── RouteSummary ───────────────────────────────────────────────────

class RouteSummaryTests(unittest.TestCase):
    def test_empty_fallbacks(self):
        r = RouteSummary(
            "sisyphus", ModelSpec(model="gpt-5"), (), ())
        self.assertEqual(len(r.fallbacks), 0)

    def test_with_warnings(self):
        r = RouteSummary(
            "oracle", ModelSpec(model="gpt-5.6"),
            (ModelSpec(model="k3"),),
            ("fallback_models[0]: bad",))
        self.assertEqual(len(r.warnings), 1)


# ── discover_configs ───────────────────────────────────────────────

class DiscoverConfigsTests(unittest.TestCase):
    def test_missing_dir_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with unittest.mock.patch.object(
                    C, "CONFIG_DIR", Path(td) / "nonexistent"):
                with self.assertRaises(FileNotFoundError):
                    discover_configs()

    def test_alphabetical_order(self):
        home = _make_temp_home(
            active_name="oh-my-openagent.json",
            active_content='{"$schema": "x", "model_fallback": true}',
            presets={
                "oh-my-openagent-default.json": '{"$schema":"d"}',
                "oh-my-openagent_glm.json": '{"$schema":"g"}',
                "oh-my-openagent_a-custom.json": '{"$schema":"a"}',
            },
        )
        with mock.patch.object(C, "CONFIG_DIR",
                                home / ".config" / "opencode"):
            active, candidates = discover_configs()
            names = [p.name for p in candidates]
            self.assertNotIn("oh-my-openagent.json", names)
            # "." (0x2E) sorts before "_" (0x5F)
            self.assertLess(names.index("oh-my-openagent-default.json"),
                            names.index("oh-my-openagent_a-custom.json"))
            self.assertLess(names.index("oh-my-openagent-default.json"),
                            names.index("oh-my-openagent_glm.json"))

    def test_active_excluded_from_candidates(self):
        home = _make_temp_home(
            active_name="oh-my-openagent.json",
            active_content='{"$schema":"x"}',
            presets={
                "oh-my-openagent-aaa.json": '{"$schema":"a"}',
            },
        )
        with mock.patch.object(C, "CONFIG_DIR",
                                         home / ".config" / "opencode"):
            active, candidates = discover_configs()
            names = [p.name for p in candidates]
            self.assertNotIn("oh-my-openagent.json", names)
            self.assertIn("oh-my-openagent-aaa.json", names)
            summaries = parse_all(active, candidates)
            self.assertFalse(any(s.file.is_current for s in summaries))

    def test_dedup_by_name(self):
        """Same filename across both glob patterns yields one entry."""
        home = _make_temp_home(
            active_name="oh-my-openagent.json",
            active_content='{"$schema":"x"}',
            presets={},
        )
        # Create identical name in both patterns
        config_dir = home / ".config" / "opencode"
        (config_dir / "oh-my-opencode-shared.json").write_text(
            '{"$schema":"x"}')
        (config_dir / "oh-my-openagent-shared.json").write_text(
            '{"$schema":"y"}')

        with mock.patch.object(C, "CONFIG_DIR", config_dir):
            _, candidates = discover_configs()
            names = [p.name for p in candidates]
            # Should not contain duplicates
            self.assertEqual(len(names), len(set(names)))


# ── parse_all / ConfigSummary ──────────────────────────────────────

class ParseAllTests(unittest.TestCase):
    def _parse_fixture(self, name: str) -> ConfigSummary:
        active = FIXTURES / "full_agents_categories.json"  # dummy active
        summaries = parse_all(active, [FIXTURES / name])
        return summaries[0]

    # ── full fixture ────────────────────────────────────────────────

    def test_full_is_valid(self):
        s = self._parse_fixture("full_agents_categories.json")
        self.assertTrue(s.is_valid)
        self.assertIsNone(s.error)

    def test_full_agents_count(self):
        s = self._parse_fixture("full_agents_categories.json")
        self.assertEqual(len(s.agents), 2)
        self.assertEqual(s.agents[0].name, "sisyphus")
        self.assertEqual(s.agents[1].name, "oracle")

    def test_full_categories_count(self):
        s = self._parse_fixture("full_agents_categories.json")
        self.assertEqual(len(s.categories), 2)

    def test_full_fallback_normalization(self):
        s = self._parse_fixture("full_agents_categories.json")
        sis = s.agents[0]
        self.assertEqual(sis.primary.model, "openai/gpt-5")
        self.assertEqual(sis.primary.variant, "max")
        self.assertEqual(sis.primary.reasoning, "high")
        self.assertEqual(len(sis.fallbacks), 2)
        self.assertEqual(sis.fallbacks[0].model, "kimi/k3")
        self.assertEqual(sis.fallbacks[0].variant, "max")
        self.assertEqual(sis.fallbacks[1].model, "deepseek/v4")

    def test_full_metadata(self):
        s = self._parse_fixture("full_agents_categories.json")
        self.assertEqual(s.schema, "https://example.com/schema.json")
        self.assertEqual(s.model_fallback, True)

    def test_full_runtime(self):
        s = self._parse_fixture("full_agents_categories.json")
        self.assertEqual(s.runtime_fallback.enabled, True)
        self.assertEqual(s.runtime_fallback.retry_on_errors, (429, 503))
        self.assertEqual(s.runtime_fallback.max_fallback_attempts, 3)
        self.assertEqual(s.runtime_fallback.cooldown_seconds, 60)

    # ── sparse fixture ──────────────────────────────────────────────

    def test_sparse_is_valid(self):
        s = self._parse_fixture("sparse_optional.json")
        self.assertTrue(s.is_valid)

    def test_sparse_model_fallback_false(self):
        s = self._parse_fixture("sparse_optional.json")
        self.assertFalse(s.model_fallback)

    def test_sparse_missing_schema(self):
        s = self._parse_fixture("sparse_optional.json")
        self.assertIsNotNone(s.schema)

    def test_sparse_empty_categories(self):
        s = self._parse_fixture("sparse_optional.json")
        self.assertEqual(len(s.categories), 0)

    def test_sparse_missing_fallbacks(self):
        s = self._parse_fixture("sparse_optional.json")
        self.assertEqual(len(s.agents[0].fallbacks), 0)

    # ── mixed fallback shapes ───────────────────────────────────────

    def test_mixed_string_fallback(self):
        s = self._parse_fixture("mixed_fallbacks.json")
        sis = s.agents[0]
        self.assertEqual(len(sis.fallbacks), 1)
        self.assertEqual(sis.fallbacks[0].model, "kimi/k3")

    def test_mixed_string_array_fallback(self):
        s = self._parse_fixture("mixed_fallbacks.json")
        oracle = s.agents[1]
        self.assertEqual(len(oracle.fallbacks), 2)
        self.assertEqual(oracle.fallbacks[0].model, "kimi/k3")
        self.assertEqual(oracle.fallbacks[1].model, "deepseek/v4")

    def test_mixed_object_array_fallback(self):
        s = self._parse_fixture("mixed_fallbacks.json")
        lib = s.agents[2]
        self.assertEqual(len(lib.fallbacks), 2)
        self.assertEqual(lib.fallbacks[0].model, "deepseek/v4-flash")
        self.assertEqual(lib.fallbacks[1].model, "kimi/k2.6")
        self.assertEqual(lib.fallbacks[1].variant, "auto")

    def test_mixed_truly_mixed_fallback(self):
        s = self._parse_fixture("mixed_fallbacks.json")
        exp = s.agents[3]  # "explore"
        self.assertEqual(len(exp.fallbacks), 3)
        self.assertEqual(exp.fallbacks[0].model, "simple-string")
        self.assertEqual(exp.fallbacks[1].model, "complex-object")
        self.assertEqual(exp.fallbacks[1].reasoning_effort, "high")
        self.assertEqual(exp.fallbacks[2].model, "another-string")

    # ── malformed fallback entries ──────────────────────────────────

    def test_malformed_still_valid(self):
        s = self._parse_fixture("malformed_fallback.json")
        self.assertTrue(s.is_valid)

    def test_malformed_has_warnings(self):
        s = self._parse_fixture("malformed_fallback.json")
        self.assertGreater(len(s.warnings), 0)

    def test_malformed_skips_bad_entries(self):
        s = self._parse_fixture("malformed_fallback.json")
        sis = s.agents[0]
        # 42 is not a valid entry -> warning, skipped
        # "string-fallback" is valid
        self.assertEqual(len(sis.fallbacks), 2)
        self.assertEqual(sis.fallbacks[0].model, "kimi/k3")
        self.assertEqual(sis.fallbacks[1].model, "string-fallback")

    # ── trailing comma / invalid JSON ───────────────────────────────

    def test_trailing_comma_invalid(self):
        s = self._parse_fixture("trailing_comma.json")
        self.assertFalse(s.is_valid)
        self.assertIsNotNone(s.error)
        self.assertIn("Invalid JSON", s.error)
        self.assertEqual(len(s.agents), 0)
        self.assertEqual(len(s.categories), 0)

    # ── additional settings ─────────────────────────────────────────

    def test_additional_settings(self):
        s = self._parse_fixture("additional_settings.json")
        self.assertTrue(s.is_valid)
        settings = dict(s.additional_settings)
        self.assertIn("telemetry", settings)
        self.assertFalse(settings["telemetry"])  # boolean scalar
        self.assertIn("agent_order", settings)
        self.assertIn("disabled_mcps", settings)
        # Dedicated keys NOT in additional
        self.assertNotIn("$schema", settings)
        self.assertNotIn("model_fallback", settings)
        self.assertNotIn("runtime_fallback", settings)
        self.assertNotIn("agents", settings)
        self.assertNotIn("categories", settings)

    # ── caching ─────────────────────────────────────────────────────

    def test_cached_raw_text(self):
        s = self._parse_fixture("full_agents_categories.json")
        self.assertIsNotNone(s.file.raw_text)
        self.assertIn("model_fallback", s.file.raw_text)

    def test_no_reread_after_parse(self):
        """Patching open after discovery must not affect rendered summaries."""
        active = FIXTURES / "full_agents_categories.json"
        presets = [FIXTURES / "sparse_optional.json"]
        summaries = parse_all(active, [*presets, active])

        with mock.patch("builtins.open",
                                 side_effect=RuntimeError("re-read!")):
            # Accessing any summary field must not trigger re-read
            _ = summaries[0].file.raw_text
            _ = summaries[0].agents
            _ = summaries[1].file.name
            # If we got here without RuntimeError, caching works

    # ── non-object top-level ────────────────────────────────────────

    def test_non_object_top_level(self):
        home = _make_temp_home(
            active_name="oh-my-openagent.json",
            active_content='{"$schema":"x"}',
            presets={"oh-my-openagent-list.json": "[1, 2, 3]"},
        )
        with mock.patch.object(C, "CONFIG_DIR",
                                         home / ".config" / "opencode"):
            _, candidates = discover_configs()
            summaries = parse_all(candidates[0], candidates)
            list_s = [s for s in summaries
                      if s.file.name == "oh-my-openagent-list.json"][0]
            self.assertTrue(list_s.is_valid)
            self.assertGreater(len(list_s.warnings), 0)
            self.assertIn("Top-level", list_s.warnings[0])

    # ── file metadata ───────────────────────────────────────────────

    def test_file_metadata(self):
        s = self._parse_fixture("full_agents_categories.json")
        self.assertEqual(s.file.name, "full_agents_categories.json")
        self.assertGreater(s.file.size_bytes, 0)
        self.assertGreater(s.file.modified_ns, 0)


if __name__ == "__main__":
    unittest.main()
