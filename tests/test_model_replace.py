"""Tests for the bulk model-replacement service (Task 7): pure
``replace_model`` document surgery across every matching surface,
``replace_model_in_profile`` write/preview semantics, and
``replace_model_all`` batch iteration.

Contract notes (binding for Tasks 10/16):
- Hit field grammar: ``model`` / ``fallback_models[{i}]`` /
  ``models[{i}]`` / ``catalog:{name}``; ``section`` is the harness block
  key (``"[opencode]"``) or ``"<root>"`` for the harness-neutral root;
  ``route`` is the agent/category name, ``""`` for catalog hits.  Walk
  order: ``<root>`` first, then HARNESS_BLOCKS order; inside a section
  agents (primary, then fallback chain) -> categories -> catalog.
- Exact string equality only — no substring, prefix, or case folding;
  malformed containers are skipped silently (no crash).
- ``UseStatus`` gains ``NO_MATCHES`` (zero hits, zero writes — checked
  BEFORE the dry-run branch) and ``PREVIEW`` (dry-run success, zero
  writes; bytes AND mtime_ns pinned).
- Active profiles always re-render via ``use_profile``: APPLIED appends
  ``; re-rendered active configuration``; NOOP/BLOCKED append nothing;
  FAILED appends ``; re-render failed: {error}`` while the overall
  status stays APPLIED (the profile write itself succeeded).
- Write errors are FAILED with message = error = ``str(exc)``.
"""

import copy
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from opencode_config_switcher.engine import (
    ReplacementHit,
    ReplaceResult,
    UseStatus,
    UseResult,
    replace_model,
    replace_model_all,
    replace_model_in_profile,
    use_profile,
)
from opencode_config_switcher.jsonc import dumps as jsonc_dumps
from opencode_config_switcher.jsonc import loads as jsonc_loads
from opencode_config_switcher.omoconfig import OMO_SCHEMA_URL
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.profiles import (
    drift_status,
    read_active,
    read_profile,
    write_profile,
)

# Double comma: a single trailing comma is legal JSONC here (Task 4
# notepad wisdom) — the double comma is what makes json fail.
BROKEN_JSONC = '{\n  "[opencode]": {},,\n}\n'

OLD = "acme/old-1"
NEW = "acme/new-1"

FIVE_HIT_DOC = {
    "$schema": OMO_SCHEMA_URL,
    "[opencode]": {
        "agents": {
            "build": {
                "model": OLD,
                "fallback_models": [
                    OLD,
                    {"model": OLD, "reasoning": "high", "temperature": 0.2},
                ],
            },
        },
        "categories": {
            "fast": {"models": ["acme/keep-1", OLD]},
        },
        "models": {
            "primary": {"model": OLD, "reasoning": "low"},
        },
    },
}

FIVE_HITS = (
    ReplacementHit("[opencode]", "build", "model", OLD, NEW),
    ReplacementHit("[opencode]", "build", "fallback_models[0]", OLD, NEW),
    ReplacementHit("[opencode]", "build", "fallback_models[1]", OLD, NEW),
    ReplacementHit("[opencode]", "fast", "models[1]", OLD, NEW),
    ReplacementHit("[opencode]", "", "catalog:primary", OLD, NEW),
)

FIVE_HIT_EXPECTED = {
    "$schema": OMO_SCHEMA_URL,
    "[opencode]": {
        "agents": {
            "build": {
                "model": NEW,
                "fallback_models": [
                    NEW,
                    {"model": NEW, "reasoning": "high", "temperature": 0.2},
                ],
            },
        },
        "categories": {
            "fast": {"models": ["acme/keep-1", NEW]},
        },
        "models": {
            "primary": {"model": NEW, "reasoning": "low"},
        },
    },
}

TWO_HIT_DOC = {
    "$schema": OMO_SCHEMA_URL,
    "[opencode]": {
        "agents": {"a": {"model": OLD}},
        "models": {"m": OLD},
    },
}
TWO_HIT_EXPECTED = {
    "$schema": OMO_SCHEMA_URL,
    "[opencode]": {
        "agents": {"a": {"model": NEW}},
        "models": {"m": NEW},
    },
}

NO_MATCH_DOC = {
    "$schema": OMO_SCHEMA_URL,
    "[opencode]": {"agents": {"a": {"model": "acme/other-1"}}},
}


class TempHomeTestCase(unittest.TestCase):
    """Every filesystem test gets its own throwaway HOME."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.paths = Paths.build(self.home)


def _profile_path(paths: Paths, name: str) -> Path:
    return paths.profiles_dir / (name + ".jsonc")


def _write_broken(paths: Paths) -> Path:
    paths.profiles_dir.mkdir(parents=True, exist_ok=True)
    target = paths.profiles_dir / "broken.jsonc"
    target.write_text(BROKEN_JSONC, encoding="utf-8")
    return target


def _assert_same_value_and_order(test: unittest.TestCase,
                                 actual: object, expected: object) -> None:
    """Deep equality INCLUDING dict key order at every level."""

    def check(a: object, e: object, where: str) -> None:
        if isinstance(a, dict) and isinstance(e, dict):
            test.assertEqual(list(a.keys()), list(e.keys()), where)
            for key in e:
                check(a[key], e[key], f"{where}.{key}")
        elif isinstance(a, list) and isinstance(e, list):
            test.assertEqual(len(a), len(e), where)
            for idx, (item_a, item_e) in enumerate(zip(a, e)):
                check(item_a, item_e, f"{where}[{idx}]")
        else:
            test.assertEqual(a, e, where)

    check(actual, expected, "<root>")


class ContractTests(unittest.TestCase):
    def test_replacement_hit_is_frozen(self):
        hit = ReplacementHit("[opencode]", "a", "model", "x", "y")
        with self.assertRaises(FrozenInstanceError):
            hit.field = "other"  # type: ignore[misc]

    def test_replace_result_is_frozen_with_error_default(self):
        result = ReplaceResult(
            status=UseStatus.PREVIEW, profile="p", hits=(), message="m")
        self.assertIsNone(result.error)
        with self.assertRaises(FrozenInstanceError):
            result.status = UseStatus.APPLIED  # type: ignore[misc]

    def test_use_status_extended_members(self):
        self.assertEqual(
            [status.value for status in UseStatus],
            ["APPLIED", "NOOP", "BLOCKED", "FAILED",
             "NO_MATCHES", "PREVIEW"],
        )
        self.assertIsInstance(UseStatus.PREVIEW, str)
        self.assertIsInstance(UseStatus.NO_MATCHES, str)


class ReplaceModelPureTests(unittest.TestCase):
    def test_five_surfaces_exact_hits_and_document(self):
        changed, hits = replace_model(FIVE_HIT_DOC, OLD, NEW)

        self.assertIsNot(changed, FIVE_HIT_DOC)
        self.assertEqual(hits, FIVE_HITS)
        _assert_same_value_and_order(self, changed, FIVE_HIT_EXPECTED)

    def test_input_document_never_mutated(self):
        before = copy.deepcopy(FIVE_HIT_DOC)

        replace_model(FIVE_HIT_DOC, OLD, NEW)

        _assert_same_value_and_order(self, FIVE_HIT_DOC, before)

    def test_second_run_on_changed_document_reports_zero_hits(self):
        changed, _ = replace_model(FIVE_HIT_DOC, OLD, NEW)

        again, hits = replace_model(changed, OLD, NEW)

        self.assertEqual(hits, ())
        _assert_same_value_and_order(self, again, changed)

    def test_exact_equality_only_no_prefix_suffix_or_case_fold(self):
        doc = {
            "[opencode]": {
                "agents": {"a": {"model": OLD}},
                "models": {"x": f"{OLD}-v2"},
            },
        }
        # case variant / prefix / suffix variant of the stored values
        for probe in (OLD.upper(), OLD[:-2], f"{OLD}-v3"):
            with self.subTest(probe=probe):
                changed, hits = replace_model(doc, probe, NEW)
                self.assertEqual(hits, ())
                _assert_same_value_and_order(self, changed, doc)

    def test_root_and_harness_section_labels(self):
        doc = {
            "agents": {"root-agent": {"model": OLD}},
            "models": {"root-cat": {"model": OLD}},
            "[opencode]": {"agents": {"plan": {"model": OLD}}},
            "[codex]": {"models": {"mini": OLD}},
        }

        changed, hits = replace_model(doc, OLD, NEW)

        self.assertEqual(hits, (
            ReplacementHit("<root>", "root-agent", "model", OLD, NEW),
            ReplacementHit("<root>", "", "catalog:root-cat", OLD, NEW),
            ReplacementHit("[opencode]", "plan", "model", OLD, NEW),
            ReplacementHit("[codex]", "", "catalog:mini", OLD, NEW),
        ))
        self.assertEqual(changed["agents"]["root-agent"]["model"], NEW)
        self.assertEqual(changed["models"]["root-cat"]["model"], NEW)
        self.assertEqual(changed["[opencode]"]["agents"]["plan"]["model"],
                         NEW)
        self.assertEqual(changed["[codex]"]["models"]["mini"], NEW)

    def test_malformed_containers_skipped_without_crash(self):
        doc = {
            "agents": ["not", "a", "dict"],
            "categories": "not-a-dict",
            "models": ["also", "a", "list"],
            "[opencode]": {
                "agents": {
                    "ok": {"model": OLD,
                           "fallback_models": {"not": "a list"}},
                    "junk": "string-route",
                },
                "categories": {"bad": 42, "c": {"models": 7}},
            },
            "[codex]": "not even a dict",
            "[senpi]": ["nope"],
        }

        changed, hits = replace_model(doc, OLD, NEW)

        self.assertEqual(hits, (
            ReplacementHit("[opencode]", "ok", "model", OLD, NEW),
        ))
        self.assertEqual(changed["agents"], ["not", "a", "dict"])
        self.assertEqual(changed["categories"], "not-a-dict")
        self.assertEqual(changed["models"], ["also", "a", "list"])
        self.assertEqual(changed["[codex]"], "not even a dict")
        self.assertEqual(changed["[senpi]"], ["nope"])
        self.assertEqual(changed["[opencode]"]["agents"]["junk"],
                         "string-route")
        self.assertEqual(changed["[opencode]"]["categories"]["bad"], 42)
        self.assertEqual(changed["[opencode]"]["categories"]["c"]["models"],
                         7)
        self.assertEqual(
            changed["[opencode]"]["agents"]["ok"]["fallback_models"],
            {"not": "a list"})

    def test_bare_string_chains_replaced_as_index_zero(self):
        doc = {"[opencode]": {
            "agents": {"a": {"model": "keep", "fallback_models": OLD}},
            "categories": {"c": {"models": OLD}},
        }}

        changed, hits = replace_model(doc, OLD, NEW)

        self.assertEqual(hits, (
            ReplacementHit("[opencode]", "a", "fallback_models[0]",
                           OLD, NEW),
            ReplacementHit("[opencode]", "c", "models[0]", OLD, NEW),
        ))
        self.assertEqual(
            changed["[opencode]"]["agents"]["a"]["fallback_models"], NEW)
        self.assertEqual(changed["[opencode]"]["categories"]["c"]["models"],
                         NEW)

    def test_chain_list_junk_entries_skipped(self):
        doc = {"[opencode]": {"agents": {"a": {
            "fallback_models": [1, None, {"no": "model"}, OLD]}}}}

        changed, hits = replace_model(doc, OLD, NEW)

        self.assertEqual(hits, (
            ReplacementHit("[opencode]", "a", "fallback_models[3]",
                           OLD, NEW),
        ))
        self.assertEqual(
            changed["[opencode]"]["agents"]["a"]["fallback_models"],
            [1, None, {"no": "model"}, NEW])

    def test_catalog_entries_without_model_key_skipped(self):
        doc = {"[opencode]": {"models": {"weird": {"nope": OLD}, "num": 5}}}

        changed, hits = replace_model(doc, OLD, NEW)

        self.assertEqual(hits, ())
        _assert_same_value_and_order(self, changed, doc)


class ReplaceModelInProfileTests(TempHomeTestCase):
    def test_apply_writes_profile_bak_and_leaves_omo_alone(self):
        write_profile(self.paths, "work", FIVE_HIT_DOC)
        target = _profile_path(self.paths, "work")
        first_bytes = target.read_bytes()

        result = replace_model_in_profile(self.paths, "work", OLD, NEW)

        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(result.profile, "work")
        self.assertIsNone(result.error)
        self.assertEqual(result.hits, FIVE_HITS)
        self.assertEqual(
            result.message, "Replaced 5 model reference(s) in profile 'work'")
        record = read_profile(self.paths, "work")
        _assert_same_value_and_order(
            self, record.document.raw, FIVE_HIT_EXPECTED)
        self.assertEqual(
            target.with_name(target.name + ".BAK").read_bytes(), first_bytes)
        self.assertFalse(self.paths.omo_path.exists())  # inactive: no render
        self.assertIsNone(read_active(self.paths))      # marker untouched

    def test_second_call_after_apply_reports_no_matches(self):
        write_profile(self.paths, "work", FIVE_HIT_DOC)
        self.assertEqual(
            replace_model_in_profile(
                self.paths, "work", OLD, NEW).status,
            UseStatus.APPLIED)
        target = _profile_path(self.paths, "work")
        stat_before = target.stat()

        second = replace_model_in_profile(self.paths, "work", OLD, NEW)

        self.assertEqual(second.status, UseStatus.NO_MATCHES)
        self.assertEqual(
            second.message,
            "No matches for model 'acme/old-1' in profile 'work'")
        self.assertEqual(
            target.stat().st_mtime_ns, stat_before.st_mtime_ns)

    def test_dry_run_previews_with_zero_writes(self):
        write_profile(self.paths, "work", FIVE_HIT_DOC)
        target = _profile_path(self.paths, "work")
        stat_before = target.stat()
        bytes_before = target.read_bytes()

        result = replace_model_in_profile(
            self.paths, "work", OLD, NEW, dry_run=True)

        self.assertEqual(result.status, UseStatus.PREVIEW)
        self.assertEqual(result.hits, FIVE_HITS)
        self.assertEqual(
            result.message,
            "Would replace 5 model reference(s) in profile 'work'")
        self.assertIsNone(result.error)
        self.assertEqual(target.read_bytes(), bytes_before)
        self.assertEqual(target.stat().st_mtime_ns, stat_before.st_mtime_ns)
        self.assertFalse(
            target.with_name(target.name + ".BAK").exists())
        self.assertFalse(self.paths.omo_path.exists())

    def test_no_matches_message_and_zero_writes(self):
        write_profile(self.paths, "work", NO_MATCH_DOC)
        target = _profile_path(self.paths, "work")
        stat_before = target.stat()

        result = replace_model_in_profile(self.paths, "work", OLD, NEW)

        self.assertEqual(result.status, UseStatus.NO_MATCHES)
        self.assertEqual(result.hits, ())
        self.assertIsNone(result.error)
        self.assertEqual(
            result.message,
            "No matches for model 'acme/old-1' in profile 'work'")
        self.assertEqual(
            target.stat().st_mtime_ns, stat_before.st_mtime_ns)
        self.assertFalse(self.paths.omo_path.exists())

    def test_dry_run_with_zero_hits_is_no_matches(self):
        write_profile(self.paths, "work", NO_MATCH_DOC)

        result = replace_model_in_profile(
            self.paths, "work", OLD, NEW, dry_run=True)

        self.assertEqual(result.status, UseStatus.NO_MATCHES)
        self.assertEqual(
            result.message,
            "No matches for model 'acme/old-1' in profile 'work'")

    def test_missing_profile_blocked(self):
        result = replace_model_in_profile(self.paths, "nope", OLD, NEW)

        self.assertEqual(result.status, UseStatus.BLOCKED)
        self.assertEqual(result.message, "Profile 'nope' not found")
        self.assertIsNone(result.error)
        self.assertEqual(result.hits, ())
        self.assertFalse(self.paths.profiles_dir.exists())

    def test_invalid_name_blocked(self):
        result = replace_model_in_profile(self.paths, "x/y", OLD, NEW)

        self.assertEqual(result.status, UseStatus.BLOCKED)
        self.assertEqual(result.message, "Invalid profile name: 'x/y'")

    def test_invalid_profile_blocked_zero_writes(self):
        target = _write_broken(self.paths)
        stat_before = target.stat()

        result = replace_model_in_profile(self.paths, "broken", OLD, NEW)

        self.assertEqual(result.status, UseStatus.BLOCKED)
        self.assertIsNotNone(result.error)
        self.assertEqual(
            result.message,
            f"Cannot apply invalid profile: broken: {result.error}")
        self.assertTrue(
            result.error is not None
            and result.error.startswith("Invalid JSONC at line"))
        self.assertEqual(target.stat().st_mtime_ns, stat_before.st_mtime_ns)

    def test_active_profile_re_rendered_with_suffix(self):
        write_profile(self.paths, "work", FIVE_HIT_DOC)
        self.assertEqual(
            use_profile(self.paths, "work").status, UseStatus.APPLIED)
        self.assertIn(
            OLD, self.paths.omo_path.read_text(encoding="utf-8"))

        result = replace_model_in_profile(self.paths, "work", OLD, NEW)

        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(
            result.message,
            "Replaced 5 model reference(s) in profile 'work'"
            "; re-rendered active configuration")
        record = read_profile(self.paths, "work")
        _assert_same_value_and_order(
            self, record.document.raw, FIVE_HIT_EXPECTED)
        live = jsonc_loads(
            self.paths.omo_path.read_text(encoding="utf-8"))
        _assert_same_value_and_order(
            self, live["[opencode]"], FIVE_HIT_EXPECTED["[opencode]"])
        self.assertNotIn(
            OLD, self.paths.omo_path.read_text(encoding="utf-8"))
        self.assertEqual(drift_status(self.paths, record), "managed")

    def test_active_noop_rerender_appends_no_suffix(self):
        # Live already contains the POST-replace document, so the store
        # write makes drift "managed" again and use_profile returns NOOP.
        write_profile(self.paths, "work", TWO_HIT_EXPECTED)
        self.assertEqual(
            use_profile(self.paths, "work").status, UseStatus.APPLIED)
        _profile_path(self.paths, "work").write_text(
            jsonc_dumps(TWO_HIT_DOC), encoding="utf-8")  # pre-replace doc
        self.assertEqual(drift_status(
            self.paths, read_profile(self.paths, "work")), "drifted")

        result = replace_model_in_profile(self.paths, "work", OLD, NEW)

        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(
            result.message, "Replaced 2 model reference(s) in profile 'work'")
        self.assertEqual(drift_status(
            self.paths, read_profile(self.paths, "work")), "managed")
        live = jsonc_loads(
            self.paths.omo_path.read_text(encoding="utf-8"))
        _assert_same_value_and_order(
            self, live["[opencode]"], TWO_HIT_EXPECTED["[opencode]"])

    def test_active_rerender_failure_keeps_applied_with_error_suffix(self):
        write_profile(self.paths, "work", TWO_HIT_DOC)
        use_profile(self.paths, "work")
        failure = UseResult(
            status=UseStatus.FAILED, profile="work",
            omo_path=self.paths.omo_path, backup=self.paths.omo_backup,
            message="Failed to render configuration: OSError: boom",
            error="boom",
        )

        with patch("opencode_config_switcher.engine.use_profile",
                   return_value=failure) as mock_use:
            result = replace_model_in_profile(
                self.paths, "work", OLD, NEW)

        mock_use.assert_called_once_with(self.paths, "work")
        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(
            result.message,
            "Replaced 2 model reference(s) in profile 'work'"
            "; re-render failed: boom")
        # The profile write itself succeeded despite the render failure.
        record = read_profile(self.paths, "work")
        _assert_same_value_and_order(
            self, record.document.raw, TWO_HIT_EXPECTED)

    def test_write_failure_failed_with_str_exc(self):
        write_profile(self.paths, "work", FIVE_HIT_DOC)
        before = _profile_path(self.paths, "work").read_bytes()

        with patch("pathlib.Path.write_text",
                   side_effect=OSError("disk full")):
            result = replace_model_in_profile(
                self.paths, "work", OLD, NEW)

        self.assertEqual(result.status, UseStatus.FAILED)
        self.assertEqual(result.message, "disk full")
        self.assertEqual(result.error, "disk full")
        self.assertEqual(result.hits, FIVE_HITS)
        self.assertEqual(_profile_path(self.paths, "work").read_bytes(),
                         before)
        self.assertFalse(self.paths.omo_path.exists())


class ReplaceModelAllTests(TempHomeTestCase):
    def test_all_ordered_pairs_mixed_validity(self):
        write_profile(self.paths, "aaa", TWO_HIT_DOC)
        _write_broken(self.paths)
        write_profile(self.paths, "zzz", NO_MATCH_DOC)

        pairs = replace_model_all(self.paths, OLD, NEW)

        self.assertEqual([name for name, _ in pairs],
                         ["aaa", "broken", "zzz"])
        aaa_name, aaa = pairs[0]
        self.assertEqual(aaa_name, "aaa")
        self.assertEqual(aaa.status, UseStatus.APPLIED)
        self.assertEqual(len(aaa.hits), 2)
        self.assertEqual(
            aaa.message, "Replaced 2 model reference(s) in profile 'aaa'")
        _, broken = pairs[1]
        self.assertEqual(broken.status, UseStatus.BLOCKED)
        self.assertTrue(broken.message.startswith(
            "Cannot apply invalid profile: broken:"))
        _, zzz = pairs[2]
        self.assertEqual(zzz.status, UseStatus.NO_MATCHES)
        record = read_profile(self.paths, "aaa")
        _assert_same_value_and_order(
            self, record.document.raw, TWO_HIT_EXPECTED)

    def test_all_dry_run_reports_without_writes(self):
        write_profile(self.paths, "aaa", TWO_HIT_DOC)
        write_profile(self.paths, "zzz", NO_MATCH_DOC)
        target = _profile_path(self.paths, "aaa")
        stat_before = target.stat()
        bytes_before = target.read_bytes()

        pairs = replace_model_all(self.paths, OLD, NEW, dry_run=True)

        self.assertEqual([name for name, _ in pairs], ["aaa", "zzz"])
        self.assertEqual(pairs[0][1].status, UseStatus.PREVIEW)
        self.assertEqual(len(pairs[0][1].hits), 2)
        self.assertEqual(pairs[1][1].status, UseStatus.NO_MATCHES)
        self.assertEqual(target.read_bytes(), bytes_before)
        self.assertEqual(target.stat().st_mtime_ns, stat_before.st_mtime_ns)

    def test_all_empty_store_returns_empty_list(self):
        self.assertEqual(replace_model_all(self.paths, OLD, NEW), [])


if __name__ == "__main__":
    unittest.main()
