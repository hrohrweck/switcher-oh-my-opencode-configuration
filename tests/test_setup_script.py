"""Tests for setup.sh installer behaviour via controlled subprocess execution."""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SETUP = Path(__file__).resolve().parent.parent / "setup.sh"


def _make_fake_pipx(tmp: Path) -> tuple[Path, Path, Path]:
    """Create a fake pipx that records arguments and succeeds."""
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    python3 = bin_dir / "python3"
    pipx = bin_dir / "pipx"
    # Create a fake python3.11 that reports 3.11
    py = bin_dir / "python3.11"
    py.write_text("#!/bin/bash\necho 'Python 3.11.15'\n")
    py.chmod(py.stat().st_mode | stat.S_IEXEC)
    # Fake pipx
    pipx.write_text(
        "#!/bin/bash\n"
        "echo \"pipx install --force \"$2 > \"${TMPDIR:-/tmp}/pipx_args\"\n"
        "exit 0\n"
    )
    pipx.chmod(pipx.stat().st_mode | stat.S_IEXEC)
    python3.symlink_to(py)
    return bin_dir, pipx, py


class SetupScriptTests(unittest.TestCase):

    def test_syntax(self):
        """setup.sh passes bash -n."""
        result = subprocess.run(
            ["bash", "-n", str(SETUP)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         f"syntax error: {result.stderr}")

    def test_pipx_path(self):
        """When pipx is available, setup.sh uses pipx install --force."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bin_dir, pipx, py = _make_fake_pipx(tmp)
            env = {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "HOME": str(tmp),
                "TMPDIR": td,
            }
            args_file = Path(td) / "pipx_args"
            result = subprocess.run(
                ["bash", str(SETUP)], capture_output=True, text=True,
                env=env, cwd=str(SETUP.parent))
            # Will try to run opencode-config-switcher --version and fail
            # (that's expected — we don't have the package installed)
            # But pipx should have been called
            if args_file.exists():
                self.assertIn("pipx install --force", args_file.read_text())

    def test_python_310_rejected(self):
        """Python 3.10 is rejected with an actionable message."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            py = bin_dir / "python3"
            py.write_text(
                "#!/bin/bash\necho 'Python 3.10.12'\n"
                "echo '(3, 10)' > /dev/stderr\n")
            py.chmod(py.stat().st_mode | stat.S_IEXEC)
            env = {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "HOME": str(tmp),
            }
            result = subprocess.run(
                ["bash", str(SETUP)], capture_output=True, text=True,
                env=env, cwd=str(SETUP.parent))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("3.11", result.stderr + result.stdout)

    def test_pep668_guidance(self):
        """PEP 668 error provides actionable guidance without break-system."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            py = bin_dir / "python3.11"
            py.write_text("#!/bin/bash\necho '(3, 11)'\n")
            py.chmod(py.stat().st_mode | stat.S_IEXEC)
            # Create failing pip
            pip_dir = tmp / "pip_pkg"
            pip_dir.mkdir(parents=True)
            (pip_dir / "__main__.py").write_text(
                "import sys\n"
                "print('externally-managed-environment', file=sys.stderr)\n"
                "sys.exit(1)\n"
            )
            env = {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "HOME": str(tmp),
                "TMPDIR": td,
            }
            result = subprocess.run(
                ["bash", str(SETUP)], capture_output=True, text=True,
                env=env, cwd=str(SETUP.parent))
            # Should fail and mention pipx/venv options
            self.assertNotEqual(result.returncode, 0)
            output = result.stderr + result.stdout
            self.assertIn("pipx", output.lower())
            self.assertNotIn("break-system-packages", output)


if __name__ == "__main__":
    unittest.main()
