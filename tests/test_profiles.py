"""Tests for the profile store: name validation, list/read, write/create/
delete lifecycle with single-generation ``.BAK`` backups, the ``.active``
marker, and ``drift_status`` managed/drifted/unmanaged classification.

Contract notes (binding for Tasks 6/9/11/12):
- The store WRITES ``{name}.jsonc`` only; it READS ``{name}.json`` too.
- ``overwrite=True`` backs up the previous bytes to ``{name}.jsonc.BAK``
  (single generation, replaced on every overwrite); first-time writes and
  creates produce NO ``.BAK``.
- ``delete_profile`` renames the profile to ``<same>.BAK`` and never clears
  or follows the ``.active`` marker (caller's job).
- ``drift_status`` compares ONLY the profile-defined, non-``$schema``,
  non-control keys against the live ``omo.jsonc`` (dicts order-insensitive,
  lists order-sensitive); a LoadError live document is ``"drifted"`` and a
  marker mismatch is ``"unmanaged"`` — never a crash.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from opencode_config_switcher.jsonc import dumps as jsonc_dumps
from opencode_config_switcher.omoconfig import (
    OMO_SCHEMA_URL,
    OmoDocument,
    replace_sections,
)
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.profiles import (
    InvalidProfileName,
    ProfileExistsError,
    ProfileNotFoundError,
    clear_active,
    create_profile,
    delete_profile,
    drift_status,
    list_profiles,
    read_active,
    read_profile,
    set_active,
    validate_name,
    write_profile,
)

DOC_V1 = {"$schema": OMO_SCHEMA_URL, "[opencode]": {"model": "one"}}
DOC_V2 = {"$schema": OMO_SCHEMA_URL, "[opencode]": {"model": "two"}}
DOC_V3 = {"$schema": OMO_SCHEMA_URL, "[opencode]": {"model": "three"}}
# Double comma: the second is a blankable trailing comma, the first is not,
# so strict json.loads sees an empty property slot -> JsoncError.
BROKEN_JSONC = '{\n  "[opencode]": {},,\n}\n'

PROFILE_A = {
    "$schema": OMO_SCHEMA_URL,
    "[opencode]": {"theme": "dark", "model": "m1"},
}


class TempHomeTestCase(unittest.TestCase):
    """Every filesystem test gets its own throwaway HOME."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.paths = Paths.build(self.home)


def _render_live(paths: Paths, profile_raw: dict) -> None:
    """Write live omo.jsonc the way the Task 6 engine will: merge into empty."""
    merged = replace_sections(OmoDocument(raw={}), OmoDocument(raw=profile_raw))
    paths.omo_path.parent.mkdir(parents=True, exist_ok=True)
    paths.omo_path.write_text(jsonc_dumps(merged), encoding="utf-8")


class NameValidationTests(unittest.TestCase):
    def test_accepts_simple_and_rich_names(self):
        for name in ("a", "My-Profile.2_x", "9starts-digit",
                     "A.b-c_d", "a" * 64):
            with self.subTest(name=name):
                self.assertIsNone(validate_name(name))

    def test_rejects_reserved_and_malformed_names(self):
        for name in ("", ".active", ".lead", "-lead", "dot/file",
                     "back\\slash", "has space", "slash/inside", "../evil",
                     "/absolute/path", "a" * 65, "trailing\n", "tab\there"):
            with self.subTest(name=name):
                with self.assertRaises(InvalidProfileName) as ctx:
                    validate_name(name)
                self.assertEqual(ctx.exception.name, name)

    def test_boundary_length_64_ok_65_rejected(self):
        self.assertIsNone(validate_name("a" * 64))
        self.assertRaises(InvalidProfileName, validate_name, "a" * 65)


class ListProfilesTests(TempHomeTestCase):
    def test_missing_dir_returns_empty_list(self):
        self.assertEqual(list_profiles(self.paths), [])

    def test_empty_dir_returns_empty_list(self):
        self.paths.profiles_dir.mkdir(parents=True)
        self.assertEqual(list_profiles(self.paths), [])

    def test_lists_jsonc_and_json_skips_hidden_bak_and_marker(self):
        self.paths.profiles_dir.mkdir(parents=True)
        alpha_text = jsonc_dumps(
            {"$schema": OMO_SCHEMA_URL, "[opencode]": {"model": "a"}})
        (self.paths.profiles_dir / "alpha.jsonc").write_text(
            alpha_text, encoding="utf-8")
        beta_text = jsonc_dumps(
            {"$schema": OMO_SCHEMA_URL, "[opencode]": {"model": "b"}})
        (self.paths.profiles_dir / "beta.json").write_text(
            beta_text, encoding="utf-8")
        (self.paths.profiles_dir / ".hidden.jsonc").write_text("{}", encoding="utf-8")
        (self.paths.profiles_dir / "gamma.jsonc.BAK").write_text("{}", encoding="utf-8")
        (self.paths.active_marker).write_text("alpha\n", encoding="utf-8")

        records = list_profiles(self.paths)
        self.assertEqual([r.name for r in records], ["alpha", "beta"])
        alpha, beta = records
        self.assertEqual(alpha.path, self.paths.profiles_dir / "alpha.jsonc")
        self.assertEqual(beta.path, self.paths.profiles_dir / "beta.json")
        self.assertTrue(alpha.is_valid)
        self.assertIsNone(alpha.error)
        self.assertIsNotNone(alpha.document)
        self.assertEqual(alpha.document.raw["[opencode]"]["model"], "a")
        self.assertEqual(alpha.raw_text, alpha_text)          # cached, never reread
        self.assertEqual(alpha.size_bytes, len(alpha_text.encode("utf-8")))
        self.assertIsInstance(alpha.modified_ns, int)
        self.assertGreater(alpha.modified_ns, 0)
        self.assertEqual(beta.document.raw["[opencode]"]["model"], "b")

    def test_broken_jsonc_recorded_invalid_with_jsonc_error_and_raw_text(self):
        self.paths.profiles_dir.mkdir(parents=True)
        (self.paths.profiles_dir / "broken.jsonc").write_text(
            BROKEN_JSONC, encoding="utf-8")
        records = list_profiles(self.paths)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.name, "broken")
        self.assertFalse(record.is_valid)
        self.assertIsInstance(record.error, str)
        self.assertTrue(record.error.startswith("Invalid JSONC at line"),
                        msg=record.error)
        self.assertIsNone(record.document)
        self.assertEqual(record.raw_text, BROKEN_JSONC)      # still cached

    def test_non_dict_top_level_recorded_invalid(self):
        self.paths.profiles_dir.mkdir(parents=True)
        (self.paths.profiles_dir / "only.json").write_text("[1, 2]", encoding="utf-8")
        record = list_profiles(self.paths)[0]
        self.assertFalse(record.is_valid)
        self.assertIn("expected object at top level", record.error)

    def test_records_are_sorted_by_name(self):
        self.paths.profiles_dir.mkdir(parents=True)
        for name in ("zeta", "beta", "mid"):
            (self.paths.profiles_dir / f"{name}.jsonc").write_text(
                jsonc_dumps(DOC_V1), encoding="utf-8")
        self.assertEqual([r.name for r in list_profiles(self.paths)],
                         ["beta", "mid", "zeta"])


class ReadProfileTests(TempHomeTestCase):
    def test_reads_jsonc_profile(self):
        write_profile(self.paths, "alpha", DOC_V1)
        record = read_profile(self.paths, "alpha")
        self.assertEqual(record.name, "alpha")
        self.assertTrue(record.is_valid)
        self.assertEqual(record.document.raw, DOC_V1)

    def test_reads_json_profile_variant(self):
        self.paths.profiles_dir.mkdir(parents=True)
        (self.paths.profiles_dir / "hand.json").write_text(
            jsonc_dumps(DOC_V2), encoding="utf-8")
        record = read_profile(self.paths, "hand")
        self.assertTrue(record.is_valid)
        self.assertEqual(record.document.raw, DOC_V2)

    def test_missing_profile_raises_not_found(self):
        with self.assertRaises(ProfileNotFoundError) as ctx:
            read_profile(self.paths, "nope")
        self.assertEqual(ctx.exception.name, "nope")

    def test_invalid_name_raises_before_any_io(self):
        for name in ("../evil", "/abs", "a/b"):
            with self.subTest(name=name):
                self.assertRaises(InvalidProfileName,
                                  read_profile, self.paths, name)

    def test_read_error_becomes_invalid_record_with_message(self):
        write_profile(self.paths, "alpha", DOC_V1)
        with patch("pathlib.Path.read_text",
                   side_effect=PermissionError("denied")):
            record = read_profile(self.paths, "alpha")
        self.assertFalse(record.is_valid)
        self.assertEqual(record.error,
                         "Cannot read alpha: PermissionError: denied")
        self.assertIsNone(record.document)
        self.assertIsNone(record.raw_text)
        self.assertIsNotNone(record.size_bytes)   # stat succeeded


class WriteCreateTests(TempHomeTestCase):
    def test_create_minimal_document(self):
        returned = create_profile(self.paths, "alpha")
        target = self.paths.profiles_dir / "alpha.jsonc"
        self.assertEqual(returned, target)
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            jsonc_dumps({"$schema": OMO_SCHEMA_URL, "[opencode]": {}}))
        record = read_profile(self.paths, "alpha")
        self.assertEqual(record.document.raw,
                         {"$schema": OMO_SCHEMA_URL, "[opencode]": {}})

    def test_create_deep_copies_source_document(self):
        source = {"$schema": OMO_SCHEMA_URL,
                  "[opencode]": {"agents": {"build": {"model": "a"}}}}
        create_profile(self.paths, "copied", from_document=source)
        source["[opencode]"]["agents"]["build"]["model"] = "MUTATED"
        record = read_profile(self.paths, "copied")
        self.assertEqual(
            record.document.raw["[opencode]"]["agents"]["build"]["model"], "a")

    def test_first_time_write_creates_no_bak(self):
        write_profile(self.paths, "alpha", DOC_V1)
        self.assertEqual(os.listdir(self.paths.profiles_dir), ["alpha.jsonc"])

    def test_create_parents_on_demand(self):
        self.assertFalse(self.paths.profiles_dir.exists())
        write_profile(self.paths, "alpha", DOC_V1)
        self.assertTrue((self.paths.profiles_dir / "alpha.jsonc").exists())

    def test_write_is_serialized_through_jsonc_dumps(self):
        write_profile(self.paths, "alpha", DOC_V1)
        self.assertEqual(
            (self.paths.profiles_dir / "alpha.jsonc").read_text(encoding="utf-8"),
            jsonc_dumps(DOC_V1))

    def test_existing_without_overwrite_raises(self):
        create_profile(self.paths, "alpha", from_document=DOC_V1)
        with self.assertRaises(ProfileExistsError) as ctx:
            write_profile(self.paths, "alpha", DOC_V2)
        self.assertEqual(ctx.exception.name, "alpha")
        # the rejection must have left the original bytes untouched
        self.assertEqual(
            (self.paths.profiles_dir / "alpha.jsonc").read_text(encoding="utf-8"),
            jsonc_dumps(DOC_V1))

    def test_create_existing_without_overwrite_raises(self):
        create_profile(self.paths, "alpha", from_document=DOC_V1)
        with self.assertRaises(ProfileExistsError):
            create_profile(self.paths, "alpha", from_document=DOC_V2)

    def test_overwrite_baks_previous_bytes_single_generation(self):
        create_profile(self.paths, "alpha", from_document=DOC_V1)
        v1_bytes = (self.paths.profiles_dir / "alpha.jsonc").read_bytes()
        write_profile(self.paths, "alpha", DOC_V2, overwrite=True)
        bak = self.paths.profiles_dir / "alpha.jsonc.BAK"
        self.assertEqual(bak.read_bytes(), v1_bytes)
        self.assertEqual(
            (self.paths.profiles_dir / "alpha.jsonc").read_text(encoding="utf-8"),
            jsonc_dumps(DOC_V2))

        v2_bytes = (self.paths.profiles_dir / "alpha.jsonc").read_bytes()
        write_profile(self.paths, "alpha", DOC_V3, overwrite=True)
        self.assertEqual(sorted(os.listdir(self.paths.profiles_dir)),
                         ["alpha.jsonc", "alpha.jsonc.BAK"])   # ONE generation
        self.assertEqual(bak.read_bytes(), v2_bytes)           # latest previous

    def test_invalid_name_never_reaches_filesystem(self):
        for name in ("../evil", "/tmp/evil", "a/b", ".active"):
            with self.subTest(name=name):
                self.assertRaises(InvalidProfileName,
                                  write_profile, self.paths, name, DOC_V1)
                self.assertRaises(InvalidProfileName,
                                  create_profile, self.paths, name)
        self.assertFalse(self.paths.profiles_dir.exists())


class DeleteTests(TempHomeTestCase):
    def test_delete_renames_to_bak_and_hides_from_list(self):
        create_profile(self.paths, "alpha", from_document=DOC_V1)
        returned = delete_profile(self.paths, "alpha")
        bak = self.paths.profiles_dir / "alpha.jsonc.BAK"
        self.assertEqual(returned, bak)
        self.assertTrue(bak.exists())
        self.assertFalse((self.paths.profiles_dir / "alpha.jsonc").exists())
        self.assertEqual(list_profiles(self.paths), [])

    def test_delete_replaces_previous_bak(self):
        create_profile(self.paths, "alpha", from_document=DOC_V1)
        delete_profile(self.paths, "alpha")
        create_profile(self.paths, "alpha", from_document=DOC_V2)
        delete_profile(self.paths, "alpha")
        self.assertEqual(sorted(os.listdir(self.paths.profiles_dir)),
                         ["alpha.jsonc.BAK"])
        bak_text = (self.paths.profiles_dir / "alpha.jsonc.BAK").read_text(
            encoding="utf-8")
        self.assertEqual(bak_text, jsonc_dumps(DOC_V2))

    def test_delete_missing_raises_not_found(self):
        self.assertRaises(ProfileNotFoundError,
                          delete_profile, self.paths, "nope")

    def test_delete_twice_raises_not_found(self):
        create_profile(self.paths, "alpha", from_document=DOC_V1)
        delete_profile(self.paths, "alpha")
        self.assertRaises(ProfileNotFoundError,
                          delete_profile, self.paths, "alpha")

    def test_delete_json_variant_renames_json_to_bak(self):
        self.paths.profiles_dir.mkdir(parents=True)
        (self.paths.profiles_dir / "hand.json").write_text(
            jsonc_dumps(DOC_V1), encoding="utf-8")
        returned = delete_profile(self.paths, "hand")
        self.assertEqual(returned, self.paths.profiles_dir / "hand.json.BAK")

    def test_restore_by_rename_round_trips_identical_content(self):
        create_profile(self.paths, "alpha", from_document=DOC_V1)
        before = read_profile(self.paths, "alpha")
        delete_profile(self.paths, "alpha")
        (self.paths.profiles_dir / "alpha.jsonc.BAK").rename(
            self.paths.profiles_dir / "alpha.jsonc")
        after = read_profile(self.paths, "alpha")
        self.assertEqual(after.raw_text, before.raw_text)
        self.assertEqual(after.document.raw, before.document.raw)
        self.assertTrue(after.is_valid)

    def test_delete_never_touches_active_marker(self):
        create_profile(self.paths, "alpha", from_document=DOC_V1)
        set_active(self.paths, "alpha")
        delete_profile(self.paths, "alpha")
        self.assertEqual(read_active(self.paths), "alpha")
        self.assertTrue(self.paths.active_marker.exists())

    def test_delete_invalid_name_raises(self):
        self.assertRaises(InvalidProfileName,
                          delete_profile, self.paths, ".active")
        self.assertRaises(InvalidProfileName,
                          delete_profile, self.paths, "../evil")


class ActiveMarkerTests(TempHomeTestCase):
    def test_read_active_missing_returns_none(self):
        self.assertIsNone(read_active(self.paths))

    def test_set_read_clear_round_trip(self):
        set_active(self.paths, "alpha")
        self.assertEqual(read_active(self.paths), "alpha")
        self.assertEqual(self.paths.active_marker.read_text(encoding="utf-8"),
                         "alpha\n")            # exactly name + newline
        clear_active(self.paths)
        self.assertIsNone(read_active(self.paths))
        self.assertFalse(self.paths.active_marker.exists())

    def test_set_active_creates_dir_on_demand(self):
        self.assertFalse(self.paths.profiles_dir.exists())
        set_active(self.paths, "alpha")
        self.assertTrue(self.paths.active_marker.exists())

    def test_set_active_does_not_validate_name(self):
        set_active(self.paths, "not a valid name!")
        self.assertEqual(read_active(self.paths), "not a valid name!")

    def test_empty_or_whitespace_marker_reads_none(self):
        self.paths.profiles_dir.mkdir(parents=True)
        self.paths.active_marker.write_text("", encoding="utf-8")
        self.assertIsNone(read_active(self.paths))
        self.paths.active_marker.write_text("   \n", encoding="utf-8")
        self.assertIsNone(read_active(self.paths))

    def test_clear_active_missing_is_idempotent(self):
        clear_active(self.paths)   # must not raise


class DriftStatusTests(TempHomeTestCase):
    def _active_record(self, raw: dict):
        write_profile(self.paths, "A", raw)
        _render_live(self.paths, raw)
        set_active(self.paths, "A")
        return read_profile(self.paths, "A")

    def test_rendered_live_is_managed(self):
        record = self._active_record(PROFILE_A)
        self.assertEqual(drift_status(self.paths, record), "managed")

    def test_extra_live_keys_and_schema_differs_still_managed(self):
        record = self._active_record(PROFILE_A)
        self.paths.omo_path.write_text(jsonc_dumps({
            "$schema": "https://elsewhere/schema.json",
            "[opencode]": {"model": "m1", "theme": "dark"},  # order swapped
            "[codex]": {"extra": True},                       # live-only key
        }), encoding="utf-8")
        self.assertEqual(drift_status(self.paths, record), "managed")

    def test_control_keys_and_schema_are_ignored(self):
        raw = {
            "$schema": OMO_SCHEMA_URL,
            "[opencode]": {"model": "m1"},
            "profiles": {"evil": "injection"},   # control key: skipped
            "_migrations": ["x"],                # control key: skipped
        }
        record = self._active_record(raw)
        self.assertEqual(drift_status(self.paths, record), "managed")

    def test_hand_edited_inner_key_is_drifted(self):
        record = self._active_record(PROFILE_A)
        self.paths.omo_path.write_text(jsonc_dumps({
            "$schema": OMO_SCHEMA_URL,
            "[opencode]": {"theme": "dark", "model": "HAND-EDITED"},
        }), encoding="utf-8")
        self.assertEqual(drift_status(self.paths, record), "drifted")

    def test_profile_key_missing_from_live_is_drifted(self):
        record = self._active_record({
            "$schema": OMO_SCHEMA_URL,
            "[opencode]": {"model": "m1"},
            "[codex]": {"a": 1},                # live will lack [codex]
        })
        # re-render live WITHOUT [codex] (i.e. from PROFILE_A)
        _render_live(self.paths, PROFILE_A)
        self.assertEqual(drift_status(self.paths, record), "drifted")

    def test_missing_live_document_is_drifted(self):
        record = self._active_record(PROFILE_A)
        self.paths.omo_path.unlink()
        self.assertEqual(drift_status(self.paths, record), "drifted")

    def test_malformed_live_document_is_drifted(self):
        record = self._active_record(PROFILE_A)
        self.paths.omo_path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(drift_status(self.paths, record), "drifted")

    def test_no_marker_is_unmanaged(self):
        record = self._active_record(PROFILE_A)
        clear_active(self.paths)
        self.assertEqual(drift_status(self.paths, record), "unmanaged")

    def test_other_active_profile_is_unmanaged(self):
        record = self._active_record(PROFILE_A)
        set_active(self.paths, "someone-else")
        self.assertEqual(drift_status(self.paths, record), "unmanaged")

    def test_lists_are_order_sensitive(self):
        record = self._active_record({
            "$schema": OMO_SCHEMA_URL,
            "[opencode]": {"chain": ["x", "y"]},
        })
        self.paths.omo_path.write_text(jsonc_dumps({
            "$schema": OMO_SCHEMA_URL,
            "[opencode]": {"chain": ["y", "x"]},
        }), encoding="utf-8")
        self.assertEqual(drift_status(self.paths, record), "drifted")

    def test_nested_dict_key_sets_matter(self):
        record = self._active_record({
            "$schema": OMO_SCHEMA_URL,
            "[opencode]": {"agents": {"build": {"model": "m1"}}},
        })
        self.paths.omo_path.write_text(jsonc_dumps({
            "$schema": OMO_SCHEMA_URL,
            "[opencode]": {"agents": {"build": {"model": "m1", "x": 1}}},
        }), encoding="utf-8")
        self.assertEqual(drift_status(self.paths, record), "drifted")

    def test_invalid_profile_record_is_drifted(self):
        write_profile(self.paths, "A", PROFILE_A)
        _render_live(self.paths, PROFILE_A)
        set_active(self.paths, "A")
        # corrupt the stored profile after activation
        (self.paths.profiles_dir / "A.jsonc").write_text(
            BROKEN_JSONC, encoding="utf-8")
        record = read_profile(self.paths, "A")
        self.assertFalse(record.is_valid)
        self.assertEqual(drift_status(self.paths, record), "drifted")


class AdversarialTests(TempHomeTestCase):
    def test_name_injection_cannot_escape_profiles_dir(self):
        for name in ("../evil", "/tmp/evil", "sub/dir/evil"):
            with self.subTest(name=name):
                self.assertRaises(InvalidProfileName,
                                  write_profile, self.paths, name, DOC_V1)
                self.assertRaises(InvalidProfileName,
                                  read_profile, self.paths, name)
                self.assertRaises(InvalidProfileName,
                                  delete_profile, self.paths, name)
        self.assertFalse((self.home / ".omo" / "evil.jsonc").exists())
        self.assertFalse(Path("/tmp/evil.jsonc").exists())

    def test_delete_active_then_clear_marker_reports_unmanaged(self):
        write_profile(self.paths, "A", PROFILE_A)
        _render_live(self.paths, PROFILE_A)
        set_active(self.paths, "A")
        record = read_profile(self.paths, "A")
        self.assertEqual(drift_status(self.paths, record), "managed")
        delete_profile(self.paths, "A")
        # marker survives the delete (documented caller's job)
        self.assertEqual(read_active(self.paths), "A")
        self.assertEqual(drift_status(self.paths, record), "managed")
        clear_active(self.paths)
        self.assertEqual(drift_status(self.paths, record), "unmanaged")

    def test_backup_bytes_equal_previous_version_exactly(self):
        create_profile(self.paths, "alpha", from_document=DOC_V1)
        v1 = (self.paths.profiles_dir / "alpha.jsonc").read_bytes()
        write_profile(self.paths, "alpha", DOC_V2, overwrite=True)
        self.assertEqual(
            (self.paths.profiles_dir / "alpha.jsonc.BAK").read_bytes(), v1)
        self.assertNotEqual(
            (self.paths.profiles_dir / "alpha.jsonc").read_bytes(), v1)


if __name__ == "__main__":
    unittest.main()
