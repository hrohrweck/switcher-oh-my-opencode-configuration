"""Verify package metadata, version, and entry-point contracts."""

import tomllib
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


class PackageMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta = tomllib.loads(PYPROJECT.read_text())

    # ── version ────────────────────────────────────────────────────────

    def test_version_single_source(self):
        """Version 3.0.0 lives only in opencode_config_switcher.__init__."""
        import opencode_config_switcher as pkg

        self.assertEqual(pkg.__version__, "3.0.0")

    def test_version_not_duplicated(self):
        """pyproject.toml has no static version field."""
        self.assertNotIn("version", self.meta["project"],
                         "pyproject.toml must not carry a static version")

    # ── project metadata ───────────────────────────────────────────────

    def test_name(self):
        self.assertEqual(self.meta["project"]["name"],
                         "opencode-config-switcher")

    def test_description(self):
        self.assertIn("Full-screen TUI", self.meta["project"]["description"])

    def test_readme(self):
        self.assertEqual(self.meta["project"]["readme"], "README.md")

    def test_license_file(self):
        self.assertEqual(self.meta["project"]["license"], {"file": "LICENSE"})

    def test_python_floor(self):
        self.assertEqual(self.meta["project"]["requires-python"], ">=3.11")

    def test_python_floor_rejects_310(self):
        """Validation helper: >=3.11 rejects 3.10."""
        import re

        req = self.meta["project"]["requires-python"]
        m = re.match(r">=\s*(\d+\.\d+)", req)
        self.assertIsNotNone(m, f"cannot parse requires-python '{req}'")
        min_version = tuple(int(x) for x in m.group(1).split("."))
        self.assertGreater(min_version, (3, 10),
                           f"python 3.10 is below {req}")
        self.assertLessEqual(min_version, (3, 11),
                             f"python 3.11 is above {req}")

    def test_no_runtime_dependencies(self):
        self.assertNotIn(
            "dependencies", self.meta["project"],
            "pyproject.toml must not declare runtime dependencies")

    # ── build system ────────────────────────────────────────────────────

    def test_build_backend(self):
        self.assertEqual(
            self.meta["build-system"]["build-backend"],
            "setuptools.build_meta")

    def test_build_requires_min_setuptools(self):
        requires = self.meta["build-system"]["requires"]
        self.assertIn("setuptools>=61", requires)

    # ── console scripts ─────────────────────────────────────────────────

    def test_console_scripts(self):
        scripts = self.meta["project"]["scripts"]
        self.assertEqual(
            scripts["opencode-config-switcher"],
            "opencode_config_switcher.cli:main")
        self.assertEqual(
            scripts["switch-omo-config"],
            "opencode_config_switcher.cli:main")
        self.assertEqual(
            scripts["switch_oh-my-opencode_config.py"],
            "opencode_config_switcher.cli:main")

    def test_exactly_three_scripts(self):
        self.assertEqual(len(self.meta["project"]["scripts"]), 3)

    # ── package discovery ───────────────────────────────────────────────

    def test_packages_find_src(self):
        find = self.meta["tool"]["setuptools"]["packages"]["find"]
        self.assertEqual(find["where"], ["src"])


if __name__ == "__main__":
    unittest.main()
