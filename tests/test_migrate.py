"""Tests for the legacy → canonical profile migration service:
``migrate_profile`` write/preview semantics and ``migrate_all`` batch
iteration over the Task 1 transform helpers.

Contract notes (mirrors ``replace_model_in_profile`` where the
operations rhyme):
- ``routes`` counts legacy ``fallback_models`` routes converted by
  ``transform.migrate_document``; a zero-route (canonical) profile is
  NEVER rewritten — NO_MATCHES with zero writes.
- Dry-run is PREVIEW with zero writes; apply goes through
  ``profiles.write_profile`` and inherits the ``.BAK`` backup and
  leading-comment preservation automatically.
- When the migrated profile is ACTIVE the engine re-renders the live
  ``omo.jsonc`` via ``use_profile``: removing that call leaves the
  live file on the legacy shape, ``drift_status`` reports "drifted"
  (CUSTOM), and this module's active test fails.
- Invalid profiles are BLOCKED with a structured error and zero
  writes; the CLI turns them into an ``INVALID:`` line and exit 1
  while other profiles still migrate.
"""

import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from opencode_config_switcher.engine import (
    MigrateResult,
    UseStatus,
    UseResult,
    migrate_all,
    migrate_profile,
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

BROKEN_JSONC = '{\n  "[opencode]": {},,\n}\n'

PRIMARY = "acme/primary"
FALLBACK_1 = "acme/fallback-1"
FALLBACK_2 = {"model": "acme/fallback-2", "reasoning": "high"}
CAT_PRIMARY = "acme/cat-primary"
CAT_FALLBACK = "acme/cat-fallback"

LEGACY_DOC = {
    "$schema": OMO_SCHEMA_URL,
    "[opencode]": {
        "agents": {
            "build": {
                "model": PRIMARY,
                "fallback_models": [FALLBACK_1, FALLBACK_2],
                "description": "kept",
            },
        },
        "categories": {
            "fast": {"model": CAT_PRIMARY,
                     "fallback_models": [CAT_FALLBACK]},
        },
    },
}

LEGACY_EXPECTED = {
    "$schema": OMO_SCHEMA_URL,
    "[opencode]": {
        "agents": {
            "build": {
                "description": "kept",
                "models": [PRIMARY, FALLBACK_1, FALLBACK_2],
            },
        },
        "categories": {
            "fast": {"models": [CAT_PRIMARY, CAT_FALLBACK]},
        },
    },
}

CANONICAL_DOC = LEGACY_EXPECTED

COMMENT_LINES = ["// hand-written", "// notes"]
COMMENTED_LEGACY_TEXT = "\n".join(COMMENT_LINES) + "\n" \
    + jsonc_dumps(LEGACY_DOC)
# write_profile treats a mixed header+user block as user comments and
# passes the WHOLE block through, so the header survives exactly once.
PRESERVED_COMMENT_LINES = [*COMMENT_LINES, "// OMO configuration"]


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
    def test_migrate_result_is_frozen_with_defaults(self):
        result = MigrateResult(status=UseStatus.NO_MATCHES, profile="p",
                               message="m")
        self.assertEqual(result.routes, 0)
        self.assertIsNone(result.error)
        self.assertFalse(result.rerendered)
        with self.assertRaises(FrozenInstanceError):
            result.routes = 3  # type: ignore[misc]


class MigrateProfileTests(TempHomeTestCase):
    def test_dry_run_previews_with_zero_writes(self):
        write_profile(self.paths, "work", LEGACY_DOC)
        target = _profile_path(self.paths, "work")
        stat_before = target.stat()
        bytes_before = target.read_bytes()

        result = migrate_profile(self.paths, "work", dry_run=True)

        self.assertEqual(result.status, UseStatus.PREVIEW)
        self.assertEqual(result.profile, "work")
        self.assertEqual(result.routes, 2)
        self.assertEqual(
            result.message, "Would migrate profile 'work' (2 route(s))")
        self.assertIsNone(result.error)
        self.assertFalse(result.rerendered)
        self.assertEqual(target.read_bytes(), bytes_before)
        self.assertEqual(target.stat().st_mtime_ns,
                         stat_before.st_mtime_ns)
        self.assertFalse(
            target.with_name(target.name + ".BAK").exists())
        self.assertFalse(self.paths.omo_path.exists())

    def test_dry_run_on_active_profile_leaves_live_byte_identical(self):
        write_profile(self.paths, "work", LEGACY_DOC)
        self.assertEqual(
            use_profile(self.paths, "work").status, UseStatus.APPLIED)
        live_before = self.paths.omo_path.read_bytes()
        store_before = _profile_path(self.paths, "work").read_bytes()

        result = migrate_profile(self.paths, "work", dry_run=True)

        self.assertEqual(result.status, UseStatus.PREVIEW)
        self.assertEqual(self.paths.omo_path.read_bytes(), live_before)
        self.assertEqual(_profile_path(self.paths, "work").read_bytes(),
                         store_before)

    def test_apply_writes_canonical_bak_and_leaves_live_alone(self):
        write_profile(self.paths, "work", LEGACY_DOC)
        target = _profile_path(self.paths, "work")
        first_bytes = target.read_bytes()

        result = migrate_profile(self.paths, "work")

        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(result.routes, 2)
        self.assertEqual(
            result.message, "Migrated profile 'work' (2 route(s))")
        self.assertIsNone(result.error)
        self.assertFalse(result.rerendered)
        record = read_profile(self.paths, "work")
        _assert_same_value_and_order(
            self, record.document.raw, LEGACY_EXPECTED)
        self.assertEqual(
            target.with_name(target.name + ".BAK").read_bytes(),
            first_bytes)
        self.assertFalse(self.paths.omo_path.exists())  # inactive: no render
        self.assertIsNone(read_active(self.paths))      # marker untouched

    def test_apply_preserves_leading_comments(self):
        self.paths.profiles_dir.mkdir(parents=True, exist_ok=True)
        target = _profile_path(self.paths, "work")
        target.write_text(COMMENTED_LEGACY_TEXT, encoding="utf-8")

        result = migrate_profile(self.paths, "work")

        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            jsonc_dumps(LEGACY_EXPECTED,
                        comments=PRESERVED_COMMENT_LINES))
        self.assertEqual(
            target.with_name(target.name + ".BAK").read_bytes(),
            COMMENTED_LEGACY_TEXT.encode("utf-8"))

    def test_active_profile_re_renders_live_and_drift_managed(self):
        write_profile(self.paths, "work", LEGACY_DOC)
        self.assertEqual(
            use_profile(self.paths, "work").status, UseStatus.APPLIED)
        self.assertIn(
            "fallback_models",
            self.paths.omo_path.read_text(encoding="utf-8"))

        result = migrate_profile(self.paths, "work")

        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(
            result.message,
            "Migrated profile 'work' (2 route(s))"
            "; re-rendered active configuration")
        self.assertTrue(result.rerendered)
        record = read_profile(self.paths, "work")
        _assert_same_value_and_order(
            self, record.document.raw, LEGACY_EXPECTED)
        live = jsonc_loads(
            self.paths.omo_path.read_text(encoding="utf-8"))
        _assert_same_value_and_order(
            self, live["[opencode]"], LEGACY_EXPECTED["[opencode]"])
        self.assertEqual(drift_status(self.paths, record), "managed")
        self.assertEqual(read_active(self.paths), "work")
        self.assertTrue(self.paths.omo_backup.exists())

    def test_inactive_profile_does_not_write_live_file(self):
        write_profile(self.paths, "work", LEGACY_DOC)
        self.paths.omo_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.omo_path.write_text(
            jsonc_dumps({"[codex]": {"untouched": True}}),
            encoding="utf-8")
        live_before = self.paths.omo_path.read_bytes()

        result = migrate_profile(self.paths, "work")

        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertFalse(result.rerendered)
        self.assertEqual(self.paths.omo_path.read_bytes(), live_before)
        self.assertFalse(self.paths.omo_backup.exists())
        self.assertIsNone(read_active(self.paths))

    def test_canonical_profile_no_migration_needed_zero_writes(self):
        write_profile(self.paths, "work", CANONICAL_DOC)
        target = _profile_path(self.paths, "work")
        stat_before = target.stat()
        bytes_before = target.read_bytes()

        result = migrate_profile(self.paths, "work")

        self.assertEqual(result.status, UseStatus.NO_MATCHES)
        self.assertEqual(result.routes, 0)
        self.assertIsNone(result.error)
        self.assertEqual(
            result.message, "No migration needed for profile 'work'")
        self.assertEqual(target.read_bytes(), bytes_before)
        self.assertEqual(target.stat().st_mtime_ns,
                         stat_before.st_mtime_ns)
        self.assertFalse(
            target.with_name(target.name + ".BAK").exists())
        self.assertFalse(self.paths.omo_path.exists())

    def test_canonical_active_profile_neither_writes_nor_re_renders(self):
        write_profile(self.paths, "work", CANONICAL_DOC)
        use_profile(self.paths, "work")
        live_before = self.paths.omo_path.read_bytes()

        result = migrate_profile(self.paths, "work")

        self.assertEqual(result.status, UseStatus.NO_MATCHES)
        self.assertEqual(self.paths.omo_path.read_bytes(), live_before)

    def test_missing_profile_blocked(self):
        result = migrate_profile(self.paths, "nope")

        self.assertEqual(result.status, UseStatus.BLOCKED)
        self.assertEqual(result.message, "Profile 'nope' not found")
        self.assertIsNone(result.error)
        self.assertEqual(result.routes, 0)
        self.assertFalse(self.paths.profiles_dir.exists())

    def test_invalid_name_blocked(self):
        result = migrate_profile(self.paths, "x/y")

        self.assertEqual(result.status, UseStatus.BLOCKED)
        self.assertEqual(result.message, "Invalid profile name: 'x/y'")

    def test_invalid_profile_blocked_zero_writes(self):
        target = _write_broken(self.paths)
        stat_before = target.stat()

        result = migrate_profile(self.paths, "broken")

        self.assertEqual(result.status, UseStatus.BLOCKED)
        self.assertIsNotNone(result.error)
        self.assertEqual(
            result.message,
            f"Cannot migrate invalid profile: broken: {result.error}")
        self.assertTrue(
            result.error is not None
            and result.error.startswith("Invalid JSONC at line"))
        self.assertEqual(target.stat().st_mtime_ns, stat_before.st_mtime_ns)

    def test_rerender_failure_keeps_applied_with_error_suffix(self):
        write_profile(self.paths, "work", LEGACY_DOC)
        use_profile(self.paths, "work")
        failure = UseResult(
            status=UseStatus.FAILED, profile="work",
            omo_path=self.paths.omo_path, backup=self.paths.omo_backup,
            message="Failed to render configuration: OSError: boom",
            error="boom",
        )

        with patch("opencode_config_switcher.engine.use_profile",
                   return_value=failure) as mock_use:
            result = migrate_profile(self.paths, "work")

        mock_use.assert_called_once_with(self.paths, "work")
        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(
            result.message,
            "Migrated profile 'work' (2 route(s))"
            "; re-render failed: boom")
        self.assertFalse(result.rerendered)
        record = read_profile(self.paths, "work")
        _assert_same_value_and_order(
            self, record.document.raw, LEGACY_EXPECTED)

    def test_write_failure_failed_with_str_exc(self):
        write_profile(self.paths, "work", LEGACY_DOC)
        before = _profile_path(self.paths, "work").read_bytes()

        with patch("pathlib.Path.write_text",
                   side_effect=OSError("disk full")):
            result = migrate_profile(self.paths, "work")

        self.assertEqual(result.status, UseStatus.FAILED)
        self.assertEqual(result.message, "disk full")
        self.assertEqual(result.error, "disk full")
        self.assertEqual(result.routes, 2)
        self.assertEqual(_profile_path(self.paths, "work").read_bytes(),
                         before)
        self.assertFalse(self.paths.omo_path.exists())


class MigrateAllTests(TempHomeTestCase):
    def test_all_ordered_pairs_mixed_validity(self):
        write_profile(self.paths, "aaa", LEGACY_DOC)
        _write_broken(self.paths)
        write_profile(self.paths, "zzz", CANONICAL_DOC)

        pairs = migrate_all(self.paths)

        self.assertEqual([name for name, _ in pairs],
                         ["aaa", "broken", "zzz"])
        _, aaa = pairs[0]
        self.assertEqual(aaa.status, UseStatus.APPLIED)
        self.assertEqual(aaa.routes, 2)
        self.assertEqual(
            aaa.message, "Migrated profile 'aaa' (2 route(s))")
        _, broken = pairs[1]
        self.assertEqual(broken.status, UseStatus.BLOCKED)
        self.assertTrue(broken.message.startswith(
            "Cannot migrate invalid profile: broken:"))
        _, zzz = pairs[2]
        self.assertEqual(zzz.status, UseStatus.NO_MATCHES)
        self.assertEqual(zzz.routes, 0)
        record = read_profile(self.paths, "aaa")
        _assert_same_value_and_order(
            self, record.document.raw, LEGACY_EXPECTED)

    def test_all_dry_run_reports_without_writes(self):
        write_profile(self.paths, "aaa", LEGACY_DOC)
        write_profile(self.paths, "zzz", CANONICAL_DOC)
        target = _profile_path(self.paths, "aaa")
        stat_before = target.stat()
        bytes_before = target.read_bytes()

        pairs = migrate_all(self.paths, dry_run=True)

        self.assertEqual([name for name, _ in pairs], ["aaa", "zzz"])
        self.assertEqual(pairs[0][1].status, UseStatus.PREVIEW)
        self.assertEqual(pairs[0][1].routes, 2)
        self.assertEqual(pairs[1][1].status, UseStatus.NO_MATCHES)
        self.assertEqual(target.read_bytes(), bytes_before)
        self.assertEqual(target.stat().st_mtime_ns, stat_before.st_mtime_ns)

    def test_all_active_profile_flagged_rerendered(self):
        write_profile(self.paths, "work", LEGACY_DOC)
        use_profile(self.paths, "work")
        write_profile(self.paths, "zzz", CANONICAL_DOC)

        pairs = migrate_all(self.paths)

        outcomes = dict(pairs)
        self.assertEqual(outcomes["work"].status, UseStatus.APPLIED)
        self.assertTrue(outcomes["work"].rerendered)
        self.assertFalse(outcomes["zzz"].rerendered)
        record = read_profile(self.paths, "work")
        self.assertEqual(drift_status(self.paths, record), "managed")

    def test_all_empty_store_returns_empty_list(self):
        self.assertEqual(migrate_all(self.paths), [])


if __name__ == "__main__":
    unittest.main()
