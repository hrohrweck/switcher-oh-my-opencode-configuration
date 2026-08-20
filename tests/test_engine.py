"""Tests for the render/use engine: ``use_profile`` render pipeline with
single-generation ``.BAK`` backup, ``capture_current`` live-state import,
and the pure ``render_document`` merge helper.

Contract notes (binding for Tasks 7/8/9/12):
- ``use_profile`` order: not-found/invalid-name BLOCKED, invalid record
  BLOCKED (zero writes), active+managed NOOP (zero writes — mtime pinned),
  else backup-if-exists -> render write -> ``set_active`` -> APPLIED.
- A corrupt (LoadError) live ``omo.jsonc`` is treated as ABSENT for the
  render (fresh start) but still byte-preserved into the ``.BAK``.
- Backup happens BEFORE the render write: a failed write leaves the backup
  on disk and the live file untouched; a failed backup aborts before any
  write to ``omo.jsonc``.
- ``capture_current`` never touches ``omo.jsonc`` or the ``.active`` marker.
- Exact message templates asserted; see the engine module docstring.
"""

import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from opencode_config_switcher.engine import (
    UseResult,
    UseStatus,
    capture_current,
    render_document,
    use_profile,
)
from opencode_config_switcher.jsonc import dumps as jsonc_dumps
from opencode_config_switcher.jsonc import loads as jsonc_loads
from opencode_config_switcher.omoconfig import (
    OMO_SCHEMA_URL,
    LoadError,
    OmoDocument,
    replace_sections,
)
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.profiles import (
    drift_status,
    read_active,
    read_profile,
    write_profile,
)

# Double comma: the second is a blankable trailing comma, the first is not,
# so strict json.loads sees an empty property slot -> JsoncError (Task 4
# notepad wisdom — a single trailing comma is legal JSONC here).
BROKEN_JSONC = '{\n  "[opencode]": {},,\n}\n'

PROFILE_SECTION = {"theme": "dark", "model": "m1"}
PROFILE_DOC = {"$schema": OMO_SCHEMA_URL, "[opencode]": PROFILE_SECTION}

LIVE_MULTI = {
    "$schema": OMO_SCHEMA_URL,
    "[opencode]": {"model": "old"},
    "[codex]": {"model": "codex-m", "nested": {"a": 1, "b": [True, None]}},
    "codegraph": {"enabled": True, "depth": 2},
    "_migrations": ["2026-07-opencode-config-unification",
                    "2026-08-reasoning-unification"],
    "profiles": {"upstream": {"env": "staging"}},
}
PRESERVED_KEYS = ("[codex]", "codegraph", "_migrations", "profiles")


class TempHomeTestCase(unittest.TestCase):
    """Every filesystem test gets its own throwaway HOME."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.paths = Paths.build(self.home)


def _write_profile(paths: Paths, name: str, document: dict) -> None:
    write_profile(paths, name, document)


def _write_live(paths: Paths, document: dict) -> None:
    paths.omo_path.parent.mkdir(parents=True, exist_ok=True)
    paths.omo_path.write_text(jsonc_dumps(document), encoding="utf-8")


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
    def test_use_status_values(self):
        # NO_MATCHES/PREVIEW were appended by Task 7 (spec-mandated enum
        # extension consumed by the model-replace service and Tasks 10/16).
        self.assertEqual(
            [status.value for status in UseStatus],
            ["APPLIED", "NOOP", "BLOCKED", "FAILED",
             "NO_MATCHES", "PREVIEW"],
        )
        self.assertIsInstance(UseStatus.APPLIED, str)

    def test_use_result_is_frozen(self):
        result = UseResult(
            status=UseStatus.NOOP, profile="p", omo_path=Path("/x"),
            backup=Path("/x.BAK"), message="m",
        )
        with self.assertRaises(FrozenInstanceError):
            result.status = UseStatus.APPLIED  # type: ignore[misc]


class RenderDocumentTests(unittest.TestCase):
    def test_absent_live_starts_from_empty(self):
        merged = render_document(OmoDocument(raw=PROFILE_DOC), None)
        expected = replace_sections(
            OmoDocument(raw={}), OmoDocument(raw=PROFILE_DOC))
        self.assertEqual(merged, expected)
        self.assertEqual(list(merged.keys()), ["$schema", "[opencode]"])

    def test_loaderror_live_starts_from_empty(self):
        error = LoadError(path=Path("/x/omo.jsonc"), message="garbage")
        merged = render_document(OmoDocument(raw=PROFILE_DOC), error)
        expected = replace_sections(
            OmoDocument(raw={}), OmoDocument(raw=PROFILE_DOC))
        self.assertEqual(merged, expected)

    def test_live_document_layers_like_replace_sections(self):
        live = OmoDocument(raw=dict(LIVE_MULTI))
        merged = render_document(OmoDocument(raw=PROFILE_DOC), live)
        self.assertEqual(merged, replace_sections(
            OmoDocument(raw=LIVE_MULTI), OmoDocument(raw=PROFILE_DOC)))
        self.assertEqual(merged["[opencode]"], PROFILE_SECTION)


class UseProfileFreshHomeTests(TempHomeTestCase):
    def test_fresh_home_use_creates_omo_jsonc(self):
        _write_profile(self.paths, "work", PROFILE_DOC)

        result = use_profile(self.paths, "work")

        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(result.message, "Profile applied: work")
        self.assertEqual(result.profile, "work")
        self.assertEqual(result.omo_path, self.paths.omo_path)
        self.assertEqual(result.backup, self.paths.omo_backup)
        self.assertIsNone(result.error)

        text = self.paths.omo_path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("// OMO configuration\n"))
        self.assertTrue(text.endswith("\n"))
        parsed = jsonc_loads(text)
        self.assertEqual(list(parsed.keys()), ["$schema", "[opencode]"])
        self.assertEqual(parsed["$schema"], OMO_SCHEMA_URL)
        self.assertEqual(parsed["[opencode]"], PROFILE_SECTION)

        self.assertFalse(self.paths.omo_backup.exists())  # nothing existed
        self.assertEqual(read_active(self.paths), "work")
        self.assertEqual(
            self.paths.active_marker.read_text(encoding="utf-8"), "work\n")


class UseProfileExistingFileTests(TempHomeTestCase):
    def test_multi_section_use_replaces_only_opencode(self):
        _write_profile(self.paths, "work", PROFILE_DOC)
        _write_live(self.paths, LIVE_MULTI)
        pre_write_bytes = self.paths.omo_path.read_bytes()

        result = use_profile(self.paths, "work")

        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(result.message, "Profile applied: work")

        after = jsonc_loads(self.paths.omo_path.read_text(encoding="utf-8"))
        self.assertEqual(after["[opencode]"], PROFILE_SECTION)
        for key in PRESERVED_KEYS:
            _assert_same_value_and_order(
                self, after[key], LIVE_MULTI[key])
        self.assertEqual(list(after.keys()), list(LIVE_MULTI.keys()))

        self.assertEqual(self.paths.omo_backup.read_bytes(), pre_write_bytes)
        self.assertEqual(read_active(self.paths), "work")

    def test_managed_second_use_is_noop_with_zero_writes(self):
        _write_profile(self.paths, "work", PROFILE_DOC)
        _write_live(self.paths, LIVE_MULTI)
        first = use_profile(self.paths, "work")
        self.assertEqual(first.status, UseStatus.APPLIED)

        stat_before = self.paths.omo_path.stat()
        bytes_before = self.paths.omo_path.read_bytes()
        second = use_profile(self.paths, "work")

        self.assertEqual(second.status, UseStatus.NOOP)
        self.assertEqual(
            second.message, "No change: profile 'work' is already active")
        stat_after = self.paths.omo_path.stat()
        self.assertEqual(stat_after.st_mtime_ns, stat_before.st_mtime_ns)
        self.assertEqual(
            stat_after.st_size, stat_before.st_size)
        self.assertEqual(self.paths.omo_path.read_bytes(), bytes_before)
        self.assertEqual(drift_status(
            self.paths, read_profile(self.paths, "work")), "managed")

    def test_drifted_active_use_applies_again(self):
        _write_profile(self.paths, "work", PROFILE_DOC)
        _write_live(self.paths, LIVE_MULTI)
        self.assertEqual(
            use_profile(self.paths, "work").status, UseStatus.APPLIED)

        drifted = dict(LIVE_MULTI)
        drifted["[opencode]"] = {"model": "hacked", "extra": True}
        _write_live(self.paths, drifted)
        self.assertEqual(drift_status(
            self.paths, read_profile(self.paths, "work")), "drifted")

        again = use_profile(self.paths, "work")
        self.assertEqual(again.status, UseStatus.APPLIED)
        self.assertEqual(again.message, "Profile applied: work")

        after = jsonc_loads(self.paths.omo_path.read_text(encoding="utf-8"))
        self.assertEqual(after["[opencode]"], PROFILE_SECTION)


class UseProfileBlockedTests(TempHomeTestCase):
    def test_invalid_profile_blocked_with_zero_writes(self):
        self.paths.profiles_dir.mkdir(parents=True)
        (self.paths.profiles_dir / "broken.jsonc").write_text(
            BROKEN_JSONC, encoding="utf-8")
        _write_live(self.paths, LIVE_MULTI)
        (self.paths.omo_backup).write_bytes(b"stale backup")
        live_stat_before = self.paths.omo_path.stat()
        backup_stat_before = self.paths.omo_backup.stat()

        result = use_profile(self.paths, "broken")

        self.assertEqual(result.status, UseStatus.BLOCKED)
        self.assertIsNotNone(result.error)
        self.assertEqual(
            result.message,
            f"Cannot apply invalid profile: broken: {result.error}")
        self.assertTrue(
            result.error is not None
            and result.error.startswith("Invalid JSONC at line"))
        self.assertEqual(
            self.paths.omo_path.stat().st_mtime_ns,
            live_stat_before.st_mtime_ns)
        self.assertEqual(
            self.paths.omo_backup.stat().st_mtime_ns,
            backup_stat_before.st_mtime_ns)

    def test_missing_profile_blocked_exact_message(self):
        result = use_profile(self.paths, "nope")

        self.assertEqual(result.status, UseStatus.BLOCKED)
        self.assertEqual(result.message, "Profile 'nope' not found")
        self.assertIsNone(result.error)
        self.assertFalse(self.paths.omo_path.exists())  # zero writes

    def test_invalid_name_blocked(self):
        result = use_profile(self.paths, "x/y")

        self.assertEqual(result.status, UseStatus.BLOCKED)
        self.assertEqual(result.message, "Invalid profile name: 'x/y'")
        self.assertFalse(self.paths.omo_path.exists())


class UseProfileCorruptLiveTests(TempHomeTestCase):
    def test_corrupt_live_renders_fresh_and_preserves_corrupt_bytes(self):
        _write_profile(self.paths, "work", PROFILE_DOC)
        corrupt = b"{{{ not jsonc at all"
        self.paths.omo_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.omo_path.write_bytes(corrupt)

        result = use_profile(self.paths, "work")

        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(result.message, "Profile applied: work")
        self.assertEqual(self.paths.omo_backup.read_bytes(), corrupt)

        after = jsonc_loads(self.paths.omo_path.read_text(encoding="utf-8"))
        self.assertEqual(list(after.keys()), ["$schema", "[opencode]"])
        self.assertEqual(after["[opencode]"], PROFILE_SECTION)
        self.assertEqual(read_active(self.paths), "work")


class UseProfileFailureTests(TempHomeTestCase):
    def test_backup_failure_blocks_render(self):
        _write_profile(self.paths, "work", PROFILE_DOC)
        _write_live(self.paths, LIVE_MULTI)
        live_bytes = self.paths.omo_path.read_bytes()

        with patch("shutil.copy2", side_effect=OSError("boom")):
            result = use_profile(self.paths, "work")

        self.assertEqual(result.status, UseStatus.FAILED)
        self.assertEqual(
            result.message, "Failed to create backup: OSError: boom")
        self.assertEqual(result.error, "boom")
        self.assertEqual(self.paths.omo_path.read_bytes(), live_bytes)
        self.assertFalse(self.paths.omo_backup.exists())
        self.assertIsNone(read_active(self.paths))

    def test_render_failure_leaves_backup_but_not_new_content(self):
        _write_profile(self.paths, "work", PROFILE_DOC)
        _write_live(self.paths, LIVE_MULTI)
        live_bytes = self.paths.omo_path.read_bytes()

        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            result = use_profile(self.paths, "work")

        self.assertEqual(result.status, UseStatus.FAILED)
        self.assertEqual(
            result.message, "Failed to render configuration: OSError: disk full")
        self.assertEqual(result.error, "disk full")
        self.assertEqual(self.paths.omo_path.read_bytes(), live_bytes)
        self.assertEqual(self.paths.omo_backup.read_bytes(), live_bytes)
        self.assertIsNone(read_active(self.paths))  # set_active never reached


class CaptureCurrentTests(TempHomeTestCase):
    def test_capture_round_trips_live_document(self):
        _write_live(self.paths, LIVE_MULTI)
        live_bytes = self.paths.omo_path.read_bytes()

        result = capture_current(self.paths, "snap")

        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(result.message, "Profile captured: snap")
        record = read_profile(self.paths, "snap")
        self.assertTrue(record.is_valid)
        _assert_same_value_and_order(
            self, record.document.raw, LIVE_MULTI)
        self.assertEqual(self.paths.omo_path.read_bytes(), live_bytes)
        self.assertIsNone(read_active(self.paths))
        self.assertFalse(self.paths.omo_backup.exists())

    def test_capture_existing_target_without_overwrite_blocked(self):
        _write_live(self.paths, LIVE_MULTI)
        _write_profile(self.paths, "snap", PROFILE_DOC)
        profile_bytes = (self.paths.profiles_dir / "snap.jsonc").read_bytes()

        result = capture_current(self.paths, "snap", overwrite=False)

        self.assertEqual(result.status, UseStatus.BLOCKED)
        self.assertEqual(result.message, "Profile already exists: snap")
        self.assertEqual(
            (self.paths.profiles_dir / "snap.jsonc").read_bytes(),
            profile_bytes)
        self.assertIsNone(read_active(self.paths))

    def test_capture_with_overwrite_replaces_and_backs_up(self):
        _write_live(self.paths, LIVE_MULTI)
        self.assertEqual(
            capture_current(self.paths, "snap").status, UseStatus.APPLIED)
        first_bytes = (self.paths.profiles_dir / "snap.jsonc").read_bytes()

        drifted = dict(LIVE_MULTI)
        drifted["[opencode]"] = {"model": "new"}
        _write_live(self.paths, drifted)

        result = capture_current(self.paths, "snap")  # overwrite=True default

        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(
            (self.paths.profiles_dir / "snap.jsonc.BAK").read_bytes(),
            first_bytes)
        record = read_profile(self.paths, "snap")
        self.assertEqual(record.document.raw["[opencode]"], {"model": "new"})

    def test_capture_missing_live_blocked(self):
        result = capture_current(self.paths, "snap")

        self.assertEqual(result.status, UseStatus.BLOCKED)
        self.assertEqual(
            result.message,
            f"Cannot import invalid configuration: "
            f"File not found: {self.paths.omo_path}")
        self.assertFalse(self.paths.profiles_dir.exists())

    def test_capture_invalid_name_blocked(self):
        _write_live(self.paths, LIVE_MULTI)

        result = capture_current(self.paths, "x/y")

        self.assertEqual(result.status, UseStatus.BLOCKED)
        self.assertEqual(result.message, "Invalid profile name: 'x/y'")


if __name__ == "__main__":
    unittest.main()
