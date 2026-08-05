"""Tests for the configuration switching service."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opencode_config_switcher.switching import (
    ApplyStatus,
    ApplyStage,
    ApplyResult,
    apply_config,
    )


class ApplyResultTests(unittest.TestCase):
    def test_immutable(self):
        r = ApplyResult(ApplyStatus.NOOP, Path("s"), Path("a"),
                        Path("b"), "msg")
        with self.assertRaises(Exception):
            r.status = ApplyStatus.APPLIED  # type: ignore[misc]


class ApplyConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.active = self.tmp / "active.json"
        self.backup = self.active.with_suffix(self.active.suffix + ".BAK")
        self.source = self.tmp / "source.json"

    # ── no-op ──────────────────────────────────────────────────────

    def test_noop_when_same_path(self):
        self.active.write_text("data")
        result = apply_config(
            self.active, active=self.active, backup=self.backup)
        self.assertEqual(result.status, ApplyStatus.NOOP)
        self.assertEqual(result.failed_stage, ApplyStage.NONE)
        self.assertEqual(self.active.read_text(), "data")
        self.assertFalse(self.backup.exists())

    def test_noop_when_same_path_even_if_invalid(self):
        self.active.write_text("data")
        result = apply_config(
            self.active, active=self.active, backup=self.backup,
            is_valid=False, error_reason="broken")
        self.assertEqual(result.status, ApplyStatus.NOOP)
        self.assertEqual(self.active.read_text(), "data")

    # ── blocked ────────────────────────────────────────────────────

    def test_blocked_invalid_source(self):
        self.active.write_text("current")
        self.source.write_text("invalid")
        result = apply_config(
            self.source, active=self.active, backup=self.backup,
            is_valid=False, error_reason="trailing comma")
        self.assertEqual(result.status, ApplyStatus.BLOCKED)
        self.assertEqual(result.failed_stage, ApplyStage.VALIDATION)
        self.assertEqual(result.error, "trailing comma")
        # No writes
        self.assertEqual(self.active.read_text(), "current")
        self.assertFalse(self.backup.exists())

    # ── apply with backup ──────────────────────────────────────────

    def test_apply_creates_backup(self):
        self.active.write_text("old-active")
        self.source.write_text("new-source")
        result = apply_config(
            self.source, active=self.active, backup=self.backup)
        self.assertEqual(result.status, ApplyStatus.APPLIED)
        self.assertTrue(self.backup.exists())
        self.assertEqual(self.backup.read_text(), "old-active")
        self.assertEqual(self.active.read_text(), "new-source")

    def test_apply_no_existing_active(self):
        """If active doesn't exist, skip backup but still copy source."""
        self.source.write_text("new-source")
        result = apply_config(
            self.source, active=self.active, backup=self.backup,
            is_valid=True)
        self.assertEqual(result.status, ApplyStatus.APPLIED)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.active.read_text(), "new-source")

    def test_apply_overwrites_backup(self):
        """Second apply overwrites the single .BAK generation."""
        self.active.write_text("old-active")
        self.backup.write_text("stale-backup")
        self.source.write_text("new-source")
        result = apply_config(
            self.source, active=self.active, backup=self.backup)
        self.assertEqual(result.status, ApplyStatus.APPLIED)
        self.assertEqual(self.backup.read_text(), "old-active")
        self.assertEqual(self.active.read_text(), "new-source")

    # ── failure stages ─────────────────────────────────────────────

    def test_backup_failure(self):
        self.active.write_text("old")
        self.source.write_text("new")
        with mock.patch("shutil.copy2",
                        side_effect=PermissionError("denied")):
            result = apply_config(
                self.source, active=self.active, backup=self.backup)
        self.assertEqual(result.status, ApplyStatus.FAILED)
        self.assertEqual(result.failed_stage, ApplyStage.BACKUP)
        self.assertIn("PermissionError", result.message)

    def test_copy_failure(self):
        self.active.write_text("old")
        self.source.write_text("new")
        # Mock only second copy2 call (source → active)
        orig = __import__("shutil").copy2
        call_count = [0]
        def side_effect(src, dst):
            call_count[0] += 1
            if call_count[0] == 2:
                raise OSError("disk full")
            return orig(src, dst)
        with mock.patch("shutil.copy2", side_effect=side_effect):
            result = apply_config(
                self.source, active=self.active, backup=self.backup)
        self.assertEqual(result.status, ApplyStatus.FAILED)
        self.assertEqual(result.failed_stage, ApplyStage.COPY)
        self.assertIn("OSError", result.message)


if __name__ == "__main__":
    unittest.main()
