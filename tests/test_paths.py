"""Tests for ``paths``: home-only ``Paths`` layout plus the project-scope
``ApplyScope`` enum and ``project_omo_paths`` resolver (pure path math,
no filesystem I/O).
"""

import unittest
from pathlib import Path

from opencode_config_switcher.paths import (
    ApplyScope,
    Paths,
    project_omo_paths,
)


class ApplyScopeTests(unittest.TestCase):
    def test_members_and_values(self):
        self.assertEqual(
            [scope.value for scope in ApplyScope], ["GLOBAL", "LOCAL"])
        self.assertEqual(ApplyScope.GLOBAL.value, "GLOBAL")
        self.assertEqual(ApplyScope.LOCAL.value, "LOCAL")

    def test_scopes_are_strings(self):
        self.assertIsInstance(ApplyScope.GLOBAL, str)
        self.assertIsInstance(ApplyScope.LOCAL, str)


class ProjectOmoPathsTests(unittest.TestCase):
    def test_resolves_exact_local_targets(self):
        self.assertEqual(
            project_omo_paths(Path("/x/p")),
            (Path("/x/p/.omo/omo.jsonc"),
             Path("/x/p/.omo/omo.jsonc.BAK")),
        )

    def test_is_pure_path_math(self):
        # A nonexistent cwd resolves without touching the filesystem.
        ghost = Path("/definitely/not/here")
        target, backup = project_omo_paths(ghost)
        self.assertEqual(target, ghost / ".omo" / "omo.jsonc")
        self.assertFalse(target.exists())
        self.assertFalse(backup.exists())


class PathsBuildStillHomeOnlyTests(unittest.TestCase):
    def test_build_derives_home_paths_unchanged(self):
        paths = Paths.build(Path("/home/u"))
        self.assertEqual(paths.omo_path, Path("/home/u/.omo/omo.jsonc"))
        self.assertEqual(paths.omo_backup,
                         Path("/home/u/.omo/omo.jsonc.BAK"))
        self.assertEqual(paths.profiles_dir, Path("/home/u/.omo/profiles"))
        self.assertEqual(paths.active_marker,
                         Path("/home/u/.omo/profiles/.active"))


if __name__ == "__main__":
    unittest.main()
