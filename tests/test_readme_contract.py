"""Contract tests for README documentation accuracy (v3.0.0)."""

import re
import unittest
from pathlib import Path


README = Path(__file__).resolve().parent.parent / "README.md"
RAW = README.read_text()


class ReadmeContractTests(unittest.TestCase):

    # -- required content -------------------------------------------

    def test_version_3_0_0(self):
        self.assertIn("3.0.0", RAW)

    def test_python_311_plus(self):
        self.assertIn("Python 3.11+", RAW)
        self.assertNotIn("Python 3.6", RAW)

    def test_canonical_command(self):
        self.assertIn("opencode-config-switcher", RAW)

    def test_switch_omo_config_alias(self):
        self.assertIn("switch-omo-config", RAW)

    def test_legacy_alias(self):
        self.assertIn("switch_oh-my-opencode_config.py", RAW)

    def test_pipx_install(self):
        self.assertIn("pipx", RAW.lower())

    def test_pip_user_install(self):
        self.assertIn("pip install --user", RAW)

    def test_uninstall(self):
        self.assertIn("uninstall", RAW.lower())

    # -- v3 subcommand surface --------------------------------------

    def test_all_subcommands_documented(self):
        for command in ("list", "show", "active", "use", "select",
                        "create", "edit", "delete", "import",
                        "replace-model"):
            self.assertIn(f"opencode-config-switcher {command}", RAW)

    def test_v3_contract_strings(self):
        self.assertIn("Profile applied:", RAW)
        self.assertIn("Backup saved to:", RAW)
        self.assertIn("ACTIVE", RAW)
        self.assertIn("CUSTOM", RAW)
        self.assertIn("~/.omo/profiles", RAW)
        self.assertIn("omo.jsonc", RAW)
        self.assertIn("Editor requires a TTY", RAW)
        self.assertIn("Exiting without changes", RAW)
        self.assertIn("INVALID", RAW)

    def test_onboarding_documented(self):
        self.assertIn("Onboarding", RAW)

    def test_import_all_legacy_documented(self):
        self.assertIn("import --all-legacy", RAW)

    def test_plain_exit_codes(self):
        self.assertIn("exit 0", RAW)
        self.assertIn("exit 2", RAW)

    def test_migration_notes(self):
        self.assertIn("Migration", RAW)

    def test_version_history_preserves_v2(self):
        self.assertIn("v2.0.0", RAW)

    def test_no_break_system_packages(self):
        self.assertNotIn("break-system-packages", RAW)

    def test_no_windows_claim(self):
        req_section = RAW.split("## Requirements")[1] if "## Requirements" in RAW else RAW
        req_section = req_section.split("##")[0]
        self.assertNotIn("Windows", req_section.replace(
            "Windows is unsupported", ""))

    # -- TUI key tables ----------------------------------------------

    def test_selector_keys(self):
        for key_doc in ("Up / Down", "Tab", "PageUp", "PageDown"):
            self.assertIn(key_doc, RAW)

    def test_selector_action_keys(self):
        for key_doc in ("`n`", "`D`", "`e`", "`i`", "`r`"):
            self.assertIn(key_doc, RAW)

    # -- prohibited content ------------------------------------------

    def test_no_python_36(self):
        self.assertNotIn("Python 3.6", RAW)

    def test_no_copy_install(self):
        self.assertNotIn("cp switch_oh-my-opencode_config.py", RAW)
        self.assertNotIn("copy the script to", RAW.lower())

    def test_no_v2_current_badge(self):
        self.assertNotIn("CURRENT", RAW)

    def test_no_v2_apply_message(self):
        self.assertNotIn("Configuration applied:", RAW)

    def test_no_switching_py_outside_migration(self):
        """switching.py may only be mentioned inside the Migration section."""
        pre_migration = RAW.split("## Migration")[0]
        self.assertNotIn("switching.py", pre_migration)

    def test_no_2_0_0_as_current(self):
        self.assertNotIn("v2.0.0 (Current)", RAW)

    def test_no_schema_validation_claim(self):
        self.assertNotIn("JSON Schema", RAW)

    # -- fenced command allowlist ------------------------------------

    def test_fenced_commands_match_allowlist(self):
        """Every fenced bash block must be in the approved set."""
        allowlist = {
            "./setup.sh",
            "pipx install .",
            "python3.11 -m pip install --user .",
            "opencode-config-switcher --version",
            "python3.11 -m pip uninstall opencode-config-switcher",
            "opencode-config-switcher list",
            "opencode-config-switcher show default",
            "opencode-config-switcher active",
            "opencode-config-switcher use work",
            "opencode-config-switcher create scratch --from work",
            "opencode-config-switcher edit work",
            "opencode-config-switcher delete scratch --yes",
            "opencode-config-switcher import --all-legacy",
            "opencode-config-switcher replace-model "
            "acme/old-model acme/new-model --all --dry-run",
            "PYTHONPATH=src python3.11 -m unittest discover -s tests -v",
            "bash -n setup.sh",
        }

        fenced = re.findall(r'```(?:bash|sh)?\n(.*?)```', RAW, re.DOTALL)
        violations = []
        for block in fenced:
            for line in block.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line not in allowlist:
                    violations.append(line)

        self.assertEqual(
            [], violations,
            f"Fenced commands outside allowlist: {violations}")

    def test_migration_and_failure_contract(self):
        self.assertIn("externally-managed-environment", RAW)
        self.assertIn("pipx", RAW.lower())


if __name__ == "__main__":
    unittest.main()
