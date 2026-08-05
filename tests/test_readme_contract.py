"""Contract tests for README documentation accuracy."""

import re
import unittest
from pathlib import Path


README = Path(__file__).resolve().parent.parent / "README.md"
RAW = README.read_text()


class ReadmeContractTests(unittest.TestCase):

    # ── required content ───────────────────────────────────────────

    def test_version_2_0_0(self):
        self.assertIn("2.0.0", RAW)

    def test_python_311_plus(self):
        self.assertIn("Python 3.11+", RAW)
        self.assertNotIn("Python 3.6", RAW)

    def test_canonical_command(self):
        self.assertIn("opencode-config-switcher", RAW)

    def test_legacy_alias(self):
        self.assertIn("switch_oh-my-opencode_config.py", RAW)

    def test_pipx_install(self):
        self.assertIn("pipx", RAW.lower())

    def test_pip_user_install(self):
        self.assertIn("pip install --user", RAW)

    def test_uninstall(self):
        self.assertIn("uninstall", RAW.lower())

    def test_wide_keys(self):
        self.assertIn("PageUp", RAW)
        self.assertIn("PageDown", RAW)

    def test_narrow_tab(self):
        self.assertIn("Tab", RAW)

    def test_overlay(self):
        self.assertIn("Overlay", RAW)

    def test_plain_exit_codes(self):
        self.assertIn("exit 0", RAW)
        self.assertIn("exit 2", RAW)

    def test_migration_notes(self):
        self.assertIn("Migration", RAW)

    def test_no_break_system_packages(self):
        self.assertNotIn("break-system-packages", RAW)

    def test_no_windows_claim(self):
        req_section = RAW.split("## Requirements")[1] if "## Requirements" in RAW else RAW
        req_section = req_section.split("##")[0]
        self.assertNotIn("Windows", req_section.replace(
            "Windows is unsupported", ""))

    # ── prohibited content ─────────────────────────────────────────

    def test_no_python_36(self):
        self.assertNotIn("Python 3.6", RAW)

    def test_no_copy_install(self):
        self.assertNotIn("cp switch_oh-my-opencode_config.py", RAW)
        self.assertNotIn("copy the script to", RAW.lower())

    def test_no_space_to_apply(self):
        tui_section = RAW.split("### TUI Mode")[1] if "### TUI Mode" in RAW else ""
        self.assertNotIn("Space", tui_section.split("Space does nothing")[0])

    def test_no_schema_validation_claim(self):
        self.assertNotIn("JSON Schema", RAW)

    def test_no_1_2_0_as_current(self):
        self.assertNotIn("v1.2.0 (Current)", RAW)

    # ── fenced command allowlist ────────────────────────────────────

    def test_fenced_commands_match_allowlist(self):
        """Every fenced bash block must be in the approved set."""
        allowlist = {
            "./setup.sh",
            "pipx install .",
            "python3.11 -m pip install --user .",
            "opencode-config-switcher --version",
            "switch_oh-my-opencode_config.py --version",
            "python3.11 -m opencode_config_switcher --version",
            "python3.11 -m pip uninstall opencode-config-switcher",
            "python3.11 -m unittest discover -s tests -v",
            "PYTHONPATH=src python3.11 -m unittest tests.test_tui_pty -v",
            "bash -n setup.sh",
            "pipx uninstall opencode-config-switcher",
        }

        fenced = re.findall(r'```(?:bash|sh)?\n(.*?)```', RAW, re.DOTALL)
        violations = []
        for block in fenced:
            for line in block.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line not in allowlist and not any(
                        line.startswith(a) for a in allowlist):
                    violations.append(line)

        self.assertEqual(
            [], violations,
            f"Fenced commands outside allowlist: {violations}")

    def test_keys_and_contract_strings(self):
        self.assertIn("Exiting without changes", RAW)
        self.assertIn("Backup saved to:", RAW)
        self.assertIn("Configuration applied:", RAW)
        self.assertIn("INVALID", RAW)
        self.assertIn("CURRENT", RAW)

    def test_migration_and_failure_contract(self):
        self.assertIn("externally-managed-environment", RAW)
        self.assertIn("pipx", RAW.lower())


if __name__ == "__main__":
    unittest.main()
