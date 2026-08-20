"""Tests for the pure TUI data layer: ProfileSummary building, state
badges/details, menu rows, deterministic details formatting (golden test
against ``tests/fixtures/groundtruth_migrated.json``), warnings-channel
harvest, and raw overlay formatting.

Contract notes (binding for Task 12):
- ``build_summaries`` never rereads profile files; it reads only the
  ``.active`` marker and (for the active record) the live ``omo.jsonc``
  via ``drift_status``.
- ``summary.agents``/``summary.categories`` elements are ``RouteDisplay``
  (a ``RouteSummary`` subclass carrying ``models_list_len``) so the panel
  can render ``(n entries in models list)`` lines for form-B routes.
- The Warnings section aggregates, in order: section warnings harvested
  from the stdlib warnings channel, per-route warnings (agents then
  categories), and runtime_fallback parse warnings.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from opencode_config_switcher.config import ModelSpec, RouteSummary
from opencode_config_switcher.jsonc import dumps as jsonc_dumps
from opencode_config_switcher.omoconfig import (
    OMO_SCHEMA_URL,
    OmoDocument,
    replace_sections,
)
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.profiles import (
    list_profiles,
    read_active,
    set_active,
    write_profile,
)
from opencode_config_switcher.tui import display_width
from opencode_config_switcher.tui_data import (
    ProfileSummary,
    RouteDisplay,
    build_summaries,
    format_details,
    format_raw,
    menu_row,
    state_badge,
    state_detail,
)

FIXTURE = (Path(__file__).resolve().parent / "fixtures"
           / "groundtruth_migrated.json")

# Double comma: single trailing commas are legal JSONC; this is truly broken.
BROKEN_JSONC = '{\n  "[opencode]": {},,\n}\n'

LONG_NAME = "very-long-profile-name"  # 22 chars


def _record(name, document=None, *, error=None, is_valid=True,
            size=1234, mtime=5678, raw=None, path=None):
    """Hand-build a ProfileRecord (no disk) with deterministic metadata."""
    from opencode_config_switcher.profiles import ProfileRecord
    return ProfileRecord(
        name=name,
        path=path or Path(f"{name}.jsonc"),
        document=document,
        is_valid=is_valid,
        error=error,
        size_bytes=size,
        modified_ns=mtime,
        raw_text=raw if raw is not None else "{}",
    )


def _summary(record, *, is_active=False, drift="unmanaged",
             agents=(), categories=(), section_warnings=()):
    return ProfileSummary(
        record=record,
        is_active=is_active,
        drift=drift,
        agents=agents,
        categories=categories,
        section_warnings=section_warnings,
    )


class TempHomeTestCase(unittest.TestCase):
    """Every filesystem test gets its own throwaway HOME."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.paths = Paths.build(self.home)

    def _render_live(self, profile_raw: dict) -> None:
        """Write live omo.jsonc the way the engine will: merge into empty."""
        merged = replace_sections(OmoDocument(raw={}),
                                  OmoDocument(raw=profile_raw))
        self.paths.omo_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.omo_path.write_text(jsonc_dumps(merged), encoding="utf-8")


class GoldenDetailsTests(TempHomeTestCase):
    """format_details on the ground-truth fixture at width 60, byte-exact."""

    def setUp(self) -> None:
        super().setUp()
        with FIXTURE.open(encoding="utf-8") as fh:
            data = json.load(fh)
        self.record = _record("gt", OmoDocument(raw=data))
        self.summary = build_summaries(self.paths, [self.record])[0]

    def test_summary_shape_for_non_active_fixture(self):
        self.assertIs(self.summary.record, self.record)
        self.assertFalse(self.summary.is_active)
        self.assertEqual(self.summary.drift, "unmanaged")
        self.assertEqual(read_active(self.paths), None)
        self.assertEqual(len(self.summary.agents), 11)
        self.assertEqual(len(self.summary.categories), 8)
        self.assertEqual(self.summary.section_warnings, ())

    def test_agents_wrap_route_summaries(self):
        for display in (*self.summary.agents, *self.summary.categories):
            self.assertIsInstance(display, RouteDisplay)
            self.assertIsInstance(display.route, RouteSummary)

    def test_golden_lines_width_60(self):
        expected = [
            "Profile: gt",
            "State: inactive",
            "File: gt.jsonc (1234 bytes, modified 5678)",
            "Schema: https://raw.githubusercontent.com/code-yeongyu/oh-m…",
            "model_fallback: True",
            "runtime_fallback:",
            "  enabled: True",
            "  retry_on_errors: 429, 503, 529",
            "  max_fallback_attempts: 3",
            "  cooldown_seconds: 60",
            "Agents (11):",
            "  sisyphus: provider-1/model-1",
            "    primary: reasoning=max",
            "    fallbacks:",
            "      1. provider-2/model-2",
            "      2. provider-3/model-3",
            "  hephaestus: provider-1/model-1",
            "    primary: reasoning=max",
            "    fallbacks:",
            "      1. provider-4/model-4",
            "      2. provider-2/model-2",
            "  oracle: provider-1/model-1",
            "    primary: reasoning=max",
            "    fallbacks:",
            "      1. provider-4/model-4",
            "  librarian: provider-2/model-2",
            "    fallbacks:",
            "      1. provider-5/model-5",
            "      2. provider-6/model-6",
            "      3. provider-7/model-7",
            "  explore: provider-2/model-2",
            "    fallbacks:",
            "      1. provider-8/model-8",
            "      2. provider-7/model-7",
            "  multimodal-looker: provider-9/model-9",
            "    fallbacks:",
            "      1. provider-10/model-10",
            "      2. provider-11/model-11",
            "  prometheus: provider-1/model-1",
            "    primary: reasoning=max",
            "    fallbacks:",
            "      1. provider-4/model-4",
            "  metis: provider-1/model-1",
            "    primary: reasoning=max",
            "    fallbacks:",
            "      1. provider-12/model-12 (reasoning=xhigh)",
            "      2. provider-13/model-13 (reasoning=max)",
            "      3. provider-14/model-14 (reasoning=max)",
            "  momus: provider-12/model-12",
            "    primary: reasoning=xhigh",
            "    fallbacks:",
            "      1. provider-1/model-1 (reasoning=max)",
            "      2. provider-13/model-13 (reasoning=max)",
            "      3. provider-14/model-14 (reasoning=max)",
            "  atlas: provider-1/model-1",
            "    primary: reasoning=max",
            "    fallbacks:",
            "      1. provider-15/model-15 (reasoning=medium)",
            "  sisyphus-junior: provider-2/model-2",
            "    fallbacks:",
            "      1. provider-3/model-3",
            "Categories (8):",
            "  visual-engineering: provider-4/model-4",
            "    fallbacks:",
            "      1. provider-1/model-1",
            "      2. provider-16/model-16 (reasoning=high)",
            "      3. provider-17/model-17 (reasoning=high)",
            "    (4 entries in models list)",
            "  ultrabrain: provider-1/model-1",
            "    primary: reasoning=max",
            "    fallbacks:",
            "      1. provider-18/model-18 (reasoning=high)",
            "      2. provider-1/model-1",
            "    (3 entries in models list)",
            "  deep: provider-1/model-1",
            "    primary: reasoning=max",
            "    fallbacks:",
            "      1. provider-18/model-18 (reasoning=high)",
            "      2. provider-17/model-17 (reasoning=high)",
            "    (3 entries in models list)",
            "  artistry: provider-18/model-18",
            "    primary: reasoning=high",
            "    fallbacks:",
            "      1. provider-16/model-16 (reasoning=high)",
            "      2. provider-17/model-17 (reasoning=high)",
            "      3. provider-15/model-15",
            "    (4 entries in models list)",
            "  quick: provider-2/model-2",
            "    fallbacks:",
            "      1. provider-19/model-19",
            "      2. provider-4/model-4",
            "      3. provider-6/model-6",
            "    (4 entries in models list)",
            "  unspecified-low: provider-2/model-2",
            "    fallbacks:",
            "      1. provider-20/model-20",
            "      2. provider-21/model-21",
            "    (3 entries in models list)",
            "  unspecified-high: provider-2/model-2",
            "    primary: reasoning=max",
            "    fallbacks:",
            "      1. provider-1/model-1 (reasoning=max)",
            "      2. provider-13/model-13 (reasoning=max)",
            "      3. provider-14/model-14 (reasoning=max)",
            "    (4 entries in models list)",
            "  writing: provider-2/model-2",
            "    fallbacks:",
            "      1. provider-1/model-1",
            "      2. provider-4/model-4",
            "      3. provider-13/model-13 (reasoning=max)",
            "      4. provider-14/model-14 (reasoning=max)",
            "    (5 entries in models list)",
        ]
        self.assertEqual(format_details(self.summary, 60), expected)

    def test_models_list_count_line_split_by_form(self):
        # Form-A agents never carry a models list -> no count, len is None.
        for route in self.summary.agents:
            self.assertIsNone(route.models_list_len)
        self.assertNotIn(
            "entries in models list",
            "\n".join(format_details(self.summary, 60)
                      ).split("Categories (8):")[0])
        # Form-B categories carry their list lengths in document order.
        self.assertEqual(
            [r.models_list_len for r in self.summary.categories],
            [4, 3, 3, 4, 4, 3, 4, 5])
        details = format_details(self.summary, 60)
        self.assertIn("    (4 entries in models list)", details)
        self.assertIn("    (5 entries in models list)", details)


class BadgeAndDetailTests(TempHomeTestCase):
    """INVALID overrides everything; ACTIVE/CUSTOM for managed/drifted."""

    DOC = {"$schema": OMO_SCHEMA_URL,
           "[opencode]": {"model_fallback": True,
                          "agents": {"sisyphus": {"model": "m1"}}}}

    def _write_and_load(self, name, doc):
        write_profile(self.paths, name, doc)
        return [r for r in list_profiles(self.paths)
                if r.name == name][0]

    def test_active_managed_badge_and_detail(self):
        record = self._write_and_load("alpha", self.DOC)
        self._render_live(self.DOC)
        set_active(self.paths, "alpha")
        summary = build_summaries(self.paths, [record])[0]
        self.assertTrue(summary.is_active)
        self.assertEqual(summary.drift, "managed")
        self.assertEqual(state_badge(summary), "ACTIVE")
        self.assertEqual(state_detail(summary), "active")

    def test_active_drifted_after_hand_edit(self):
        record = self._write_and_load("alpha", self.DOC)
        self._render_live(self.DOC)
        set_active(self.paths, "alpha")
        # Hand-edit the live omo.jsonc so alpha drifts away.
        text = self.paths.omo_path.read_text(encoding="utf-8")
        self.paths.omo_path.write_text(text.replace('"m1"', '"hacked"'),
                                       encoding="utf-8")
        summary = build_summaries(self.paths, [record])[0]
        self.assertEqual(summary.drift, "drifted")
        self.assertEqual(state_badge(summary), "CUSTOM")
        self.assertEqual(state_detail(summary),
                         "custom (configuration drifted from 'alpha')")

    def test_non_active_plain_profile(self):
        self._write_and_load("alpha", self.DOC)
        self._render_live(self.DOC)
        set_active(self.paths, "alpha")
        record = self._write_and_load("beta", self.DOC)
        summary = build_summaries(self.paths, [record])[0]
        self.assertFalse(summary.is_active)
        self.assertEqual(summary.drift, "unmanaged")
        self.assertEqual(state_badge(summary), "")
        self.assertEqual(state_detail(summary), "")

    def test_invalid_record_renders_without_exception(self):
        self.paths.profiles_dir.mkdir(parents=True)
        (self.paths.profiles_dir / "broken.jsonc").write_text(
            BROKEN_JSONC, encoding="utf-8")
        record = list_profiles(self.paths)[0]
        self.assertFalse(record.is_valid)
        self.assertIsNotNone(record.error)
        summary = build_summaries(self.paths, [record])[0]
        self.assertEqual(state_badge(summary), "INVALID")
        self.assertEqual(state_detail(summary),
                         f"invalid: {record.error}")
        lines = format_details(summary, 80)
        self.assertTrue(lines[1].startswith("State: invalid: "))
        self.assertTrue(lines[2].startswith("File: broken.jsonc"))
        self.assertFalse(any(line.startswith("Schema:") for line in lines))
        self.assertFalse(any(line.startswith("Agents (") for line in lines))
        self.assertEqual(summary.agents, ())
        self.assertEqual(summary.categories, ())
        self.assertEqual(summary.section_warnings, ())

    def test_invalid_active_profile_still_invalid(self):
        self.paths.profiles_dir.mkdir(parents=True)
        (self.paths.profiles_dir / "broken.jsonc").write_text(
            BROKEN_JSONC, encoding="utf-8")
        set_active(self.paths, "broken")
        record = list_profiles(self.paths)[0]
        summary = build_summaries(self.paths, [record])[0]
        self.assertTrue(summary.is_active)
        self.assertEqual(summary.drift, "drifted")
        self.assertEqual(state_badge(summary), "INVALID")
        self.assertEqual(state_detail(summary),
                         f"invalid: {record.error}")


class MenuRowTests(unittest.TestCase):
    def _summary(self, name=LONG_NAME, badge_state=("managed", True)):
        drift, active = badge_state
        return _summary(_record(name), is_active=active, drift=drift)

    def test_full_row_at_width_40(self):
        self.assertEqual(menu_row(self._summary(), 40),
                         "very-long-profile-name [ACTIVE]")

    def test_badge_intact_from_len_plus_12_onward(self):
        full = "very-long-profile-name [ACTIVE]"
        for width in range(len(LONG_NAME) + 12, len(LONG_NAME) + 30):
            self.assertEqual(menu_row(self._summary(), width), full)

    def test_clipping_at_width_20_keeps_badge(self):
        row = menu_row(self._summary(), 20)
        self.assertEqual(row, "very-long-… [ACTIVE]")
        self.assertEqual(display_width(row), 20)
        self.assertTrue(row.endswith("[ACTIVE]"))

    def test_inactive_row_has_no_suffix(self):
        summary = _summary(_record("plain"), is_active=False)
        self.assertEqual(menu_row(summary, 40), "plain")
        self.assertEqual(menu_row(summary, 4), "pla…")

    def test_invalid_and_custom_badge_suffixes(self):
        custom = _summary(_record("cust"), is_active=True, drift="drifted")
        self.assertEqual(menu_row(custom, 40), "cust [CUSTOM]")
        broken = _summary(_record("broke", error="bad json", is_valid=False),
                          is_active=True, drift="drifted")
        self.assertEqual(menu_row(broken, 40), "broke [INVALID]")

    def test_cjk_name_clips_to_display_width(self):
        summary = _summary(_record("設定プロファイル"), is_active=False)
        row = menu_row(summary, 10)
        self.assertEqual(row, "設定プロ…")
        self.assertLessEqual(display_width(row), 10)

    def test_zero_and_tiny_widths(self):
        self.assertEqual(menu_row(self._summary(), 0), "")
        self.assertEqual(display_width(menu_row(self._summary(), 1)), 1)


class WarningsHarvestTests(TempHomeTestCase):
    DIRTY_DOC = {
        "$schema": OMO_SCHEMA_URL,
        "[opencode]": {
            "runtime_fallback": {"enabled": "yes"},
            "agents": {
                "sisyphus": {"model": "m1",
                             "fallback_models": {"bad": 1}},
                "bad-block": "nope",
            },
            "categories": {"also-bad": 42},
        },
    }

    def test_section_route_and_runtime_warnings_collected_in_order(self):
        record = self._write_dirty("dirty")
        summary = build_summaries(self.paths, [record])[0]
        self.assertEqual(summary.section_warnings, (
            "agents.bad-block: expected object, got str",
            "categories.also-bad: expected object, got int",
        ))
        lines = format_details(summary, 80)
        warnings_block = lines[lines.index("Warnings:"):]
        self.assertEqual(warnings_block, [
            "Warnings:",
            "  agents.bad-block: expected object, got str",
            "  categories.also-bad: expected object, got int",
            "  agents.sisyphus.fallback_models: unexpected type dict",
            "  runtime_fallback.enabled: expected boolean, got str",
        ])

    def test_two_records_with_identical_warnings_no_cross_contamination(
            self):
        first = self._write_dirty("one")
        second = self._write_dirty("two")
        one, two = build_summaries(self.paths, [first, second])
        self.assertEqual(one.section_warnings,
                         two.section_warnings)
        self.assertEqual(len(two.section_warnings), 2)

    def test_nothing_leaks_into_the_warnings_module_state(self):
        import warnings as warnings_mod
        record = self._write_dirty("dirty")
        before = list(warnings_mod.filters)
        build_summaries(self.paths, [record])
        self.assertEqual(warnings_mod.filters, before)

    def _write_dirty(self, name):
        write_profile(self.paths, name, self.DIRTY_DOC)
        return [r for r in list_profiles(self.paths)
                if r.name == name][0]


class FormatDetailsEdgeTests(TempHomeTestCase):
    def test_none_size_and_mtime_short_file_line(self):
        summary = _summary(_record("gt", size=None, mtime=None))
        lines = format_details(summary, 40)
        self.assertEqual(lines[2], "File: gt.jsonc")

    def test_partial_metadata_also_short_file_line(self):
        summary = _summary(_record("gt", size=10, mtime=None))
        self.assertEqual(format_details(summary, 40)[2], "File: gt.jsonc")

    def test_minimal_harness_renders_not_configured_and_empty_sections(
            self):
        doc = {"$schema": OMO_SCHEMA_URL, "[opencode]": {}}
        summary = build_summaries(
            self.paths, [_record("empty", OmoDocument(raw=doc))])[0]
        self.assertEqual(format_details(summary, 100), [
            "Profile: empty",
            "State: inactive",
            "File: empty.jsonc (1234 bytes, modified 5678)",
            f"Schema: {OMO_SCHEMA_URL}",
            "model_fallback: not configured",
            "runtime_fallback:",
            "  enabled: not configured",
            "  retry_on_errors: not configured",
            "  max_fallback_attempts: not configured",
            "  cooldown_seconds: not configured",
            "Agents (0):",
            "Categories (0):",
        ])

    def test_no_harness_block_omits_routes_and_fallback_sections(self):
        doc = {"$schema": OMO_SCHEMA_URL}
        summary = build_summaries(
            self.paths, [_record("bare", OmoDocument(raw=doc))])[0]
        self.assertEqual(format_details(summary, 100), [
            "Profile: bare",
            "State: inactive",
            "File: bare.jsonc (1234 bytes, modified 5678)",
            f"Schema: {OMO_SCHEMA_URL}",
        ])

    def test_additional_settings_compact_value_forms(self):
        doc = {"$schema": OMO_SCHEMA_URL, "[opencode]": {
            "theme": "dark",
            "flags": [1, 2, 3],
            "nested": {"b": 2, "a": 1},
            "autoshare": False,
            "agents": {},
        }}
        summary = build_summaries(
            self.paths, [_record("extra", OmoDocument(raw=doc))])[0]
        lines = format_details(summary, 80)
        tail = lines[lines.index("Additional settings:"):]
        self.assertEqual(tail, [
            "Additional settings:",
            "  theme: dark",
            "  flags: [3 items]",
            "  nested: {b, a}",
            "  autoshare: False",
        ])

    def test_form_a_route_with_models_list_shows_count_line(self):
        doc = {"$schema": OMO_SCHEMA_URL, "[opencode]": {"agents": {
            "both": {"model": "m", "models": ["a", "b"]},
            "strmodels": {"models": "solo"},
            "plain": {"model": "p"},
        }}}
        summary = build_summaries(
            self.paths, [_record("mix", OmoDocument(raw=doc))])[0]
        by_name = {r.route.name: r for r in summary.agents}
        self.assertEqual(by_name["both"].models_list_len, 2)
        self.assertEqual(by_name["both"].route.fallbacks[0].model, "a")
        self.assertIsNone(by_name["strmodels"].models_list_len)
        self.assertIsNone(by_name["plain"].models_list_len)
        lines = format_details(summary, 80)
        self.assertIn("    (2 entries in models list)", lines)
        self.assertEqual(
            sum("entries in models list" in line for line in lines), 1)
        self.assertIn("  strmodels: solo", lines)

    def test_canonical_agent_primary_line_fallback_list_and_count(self):
        doc = {"$schema": OMO_SCHEMA_URL, "[opencode]": {"agents": {
            "sisyphus": {"models": [{"model": "p/m", "reasoning": "max"},
                                    "a/b"]}}}}
        summary = build_summaries(
            self.paths, [_record("canon", OmoDocument(raw=doc))])[0]
        agent = summary.agents[0]
        self.assertEqual(agent.models_list_len, 2)
        self.assertEqual(agent.route.primary,
                         ModelSpec(model="p/m", reasoning="max"))
        self.assertEqual(agent.route.fallbacks,
                         (ModelSpec(model="a/b"),))
        lines = format_details(summary, 80)
        start = lines.index("Agents (1):")
        self.assertEqual(lines[start:start + 5], [
            "Agents (1):",
            "  sisyphus: p/m",
            "    primary: reasoning=max",
            "    fallbacks:",
            "      1. a/b",
        ])
        self.assertIn("    (2 entries in models list)", lines)

    def test_canonical_agent_fallback_entry_renders_extras_suffix(self):
        doc = {"$schema": OMO_SCHEMA_URL, "[opencode]": {"agents": {
            "metis": {"models": [
                {"model": "p/m", "variant": "big"},
                {"model": "a/b", "reasoning": "low",
                 "reasoningEffort": "xhigh"},
            ]}}}}
        summary = build_summaries(
            self.paths, [_record("canon2", OmoDocument(raw=doc))])[0]
        lines = format_details(summary, 80)
        start = lines.index("Agents (1):")
        self.assertEqual(lines[start:start + 5], [
            "Agents (1):",
            "  metis: p/m",
            "    primary: variant=big",
            "    fallbacks:",
            "      1. a/b (reasoning=low, effort=xhigh)",
        ])

    def test_primary_variant_and_effort_parts(self):
        doc = {"$schema": OMO_SCHEMA_URL, "[opencode]": {"agents": {
            "full": {"model": "m", "variant": "big",
                     "reasoningEffort": "high"},
            "vonly": {"model": "m2", "variant": "tiny"},
        }}}
        summary = build_summaries(
            self.paths, [_record("v", OmoDocument(raw=doc))])[0]
        lines = format_details(summary, 80)
        self.assertIn("    primary: variant=big effort=high", lines)
        self.assertIn("    primary: variant=tiny", lines)

    def test_all_lines_clipped_to_width_with_long_name(self):
        name = "x" * 64
        doc = {"$schema": OMO_SCHEMA_URL, "[opencode]": {"agents": {
            "sisyphus": {"model": "m1",
                         "fallback_models": ["m2", "m3"]}}}}
        summary = build_summaries(
            self.paths, [_record(name, OmoDocument(raw=doc))])[0]
        lines = format_details(summary, 20)
        self.assertTrue(lines)
        for line in lines:
            self.assertLessEqual(display_width(line), 20)
        self.assertTrue(lines[0].startswith("Profile: "))
        self.assertTrue(lines[0].endswith("…"))


class FormatRawTests(unittest.TestCase):
    def test_long_lines_clipped_short_pass_through(self):
        raw = "x" * 100 + "\nshort line\n\n"
        lines = format_raw(raw, 30)
        self.assertEqual(lines[0], "x" * 29 + "…")
        self.assertEqual(display_width(lines[0]), 30)
        self.assertEqual(lines[1], "short line")
        self.assertEqual(lines[2], "")

    def test_empty_and_cjk(self):
        self.assertEqual(format_raw("", 40), [])
        lines = format_raw("設定プロファイル設定プロファイル", 10)
        self.assertEqual(lines[0], "設定プロ…")
        self.assertLessEqual(display_width(lines[0]), 10)


class BuildSummariesTests(TempHomeTestCase):
    def test_empty_store_returns_empty_list(self):
        self.assertEqual(build_summaries(self.paths, []), [])
        self.paths.profiles_dir.mkdir(parents=True)
        self.assertEqual(build_summaries(self.paths, []), [])

    def test_preserves_record_order_and_active_lookup(self):
        doc = {"$schema": OMO_SCHEMA_URL, "[opencode]": {"model": "m1"}}
        write_profile(self.paths, "beta", doc)
        write_profile(self.paths, "alpha", doc)
        self._render_live(doc)
        set_active(self.paths, "alpha")
        records = {r.name: r for r in list_profiles(self.paths)}
        summaries = build_summaries(self.paths,
                                    [records["beta"], records["alpha"]])
        self.assertEqual([s.record.name for s in summaries],
                         ["beta", "alpha"])
        self.assertEqual([s.is_active for s in summaries],
                         [False, True])
        self.assertEqual([s.drift for s in summaries],
                         ["unmanaged", "managed"])

    def test_record_without_document_gets_no_routes(self):
        record = _record("ghost", None, error="Cannot read ghost: boom",
                         is_valid=False, raw=None)
        summary = build_summaries(self.paths, [record])[0]
        self.assertEqual((summary.agents, summary.categories,
                          summary.section_warnings), ((), (), ()))


if __name__ == "__main__":
    unittest.main()
