"""Installation and migration tests for the packaged command."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class InstallationTests(unittest.TestCase):
    """Verify `pip install --no-build-isolation --no-deps .` works."""

    def test_offline_install_in_venv(self):
        venv = Path(tempfile.mkdtemp()) / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            check=True, capture_output=True)
        pip = str(venv / "bin" / "pip")
        py = str(venv / "bin" / "python3")

        # ensurepip
        subprocess.run([py, "-m", "ensurepip", "--upgrade"],
                       check=True, capture_output=True)

        # Verify setuptools >= 61
        result = subprocess.run(
            [pip, "show", "setuptools"],
            capture_output=True, text=True)
        if result.returncode != 0:
            self.skipTest("setuptools not available in venv")

        # Install
        env = {**os.environ, "PIP_NO_INDEX": "1"}
        result = subprocess.run(
            [pip, "install", "--no-build-isolation", "--no-deps",
             str(PROJECT_ROOT)],
            env=env, capture_output=True, text=True)
        if result.returncode != 0:
            print("pip install stderr:", result.stderr)
        self.assertEqual(result.returncode, 0,
                         f"pip install failed: {result.stderr}")

        # Test version commands
        for cmd_name in ("opencode-config-switcher",
                         "switch-omo-config",
                         "switch_oh-my-opencode_config.py"):
            cmd = str(venv / "bin" / cmd_name)
            result = subprocess.run(
                [cmd, "--version"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "3.0.0")

        # Test module entry
        result = subprocess.run(
            [py, "-m", "opencode_config_switcher", "--version"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "3.0.0")

        # Verify installed metadata via importlib.metadata
        import subprocess as _sp
        result = _sp.run(
            [str(venv / "bin" / "python3"), "-c",
             "import importlib.metadata as m; "
             "d=m.metadata('opencode-config-switcher'); "
             "print(f'Name: {d[\"Name\"]}'); "
             "print(f'Version: {d[\"Version\"]}'); "
             "print(f'Requires-Python: {d[\"Requires-Python\"]}')"],
            capture_output=True, text=True, env=env)
        self.assertIn("Name: opencode-config-switcher", result.stdout)
        self.assertIn("Version: 3.0.0", result.stdout)
        self.assertIn("Requires-Python: >=3.11", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_old_script_removed(self):
        """Root switch_oh-my-opencode_config.py must not exist."""
        old = PROJECT_ROOT / "switch_oh-my-opencode_config.py"
        self.assertFalse(
            old.exists(),
            "Obsolete root script must be removed")


if __name__ == "__main__":
    unittest.main()
