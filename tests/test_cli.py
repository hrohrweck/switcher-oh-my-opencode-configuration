"""CLI contracts: argparse subcommands, bare plain selector, stream/exit ownership.

Hermetic by construction: every subprocess runs with a temp HOME and
PYTHONPATH=src; every direct call patches HOME so Path.home() resolves into
the sandbox.  Only cli.py may print user output or choose exit codes.
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opencode_config_switcher import __version__, cli
from opencode_config_switcher.engine import render_document, use_profile
from opencode_config_switcher.jsonc import dumps as jsonc_dumps
from opencode_config_switcher.jsonc import loads as jsonc_loads
from opencode_config_switcher.omoconfig import OmoDocument
from opencode_config_switcher.paths import Paths

SRC = Path(__file__).resolve().parents[1] / "src"
PY = sys.executable
SCHEMA = ("https://raw.githubusercontent.com/code-yeongyu/"
          "oh-my-openagent/dev/assets/omo.schema.json")

ALPHA_TEXT = (
    "// alpha\n"
    "{\n"
    f'  "$schema": "{SCHEMA}",\n'
    '  "[opencode]": {\n'
    '    "agents": {"build": {"model": "provider-1/model-a"}}\n'
    "  }\n"
    "}\n"
)
BETA_TEXT = (
    "{\n"
    f'  "$schema": "{SCHEMA}",\n'
    '  "[opencode]": {\n'
    '    "agents": {"build": {"model": "provider-2/model-b"}}\n'
    "  }\n"
    "}\n"
)
BROKEN_TEXT = "{\n  \"$schema\": x,\n  \"[opencode]\": {},,\n}\n"
LIVE_TEXT = jsonc_dumps({
    "$schema": SCHEMA,
    "[opencode]": {"agents": {"build": {"model": "old/model"}}},
    "[codex]": {"k": 1},
    "_migrations": ["2026-07-opencode-config-unification"],
})

HINT = ("Run 'opencode-config-switcher import --all-legacy' or "
        "'import --current' to get started.")


@contextlib.contextmanager
def _home_with(profiles=None, *, active=None, omo_text=None):
    """Temp HOME with optional profiles/active marker/live omo.jsonc."""
    with tempfile.TemporaryDirectory(prefix="ocs-task8-") as name:
        home = Path(name)
        profiles_dir = home / ".omo" / "profiles"
        profiles_dir.mkdir(parents=True)
        for profile_name, text in (profiles or {}).items():
            (profiles_dir / f"{profile_name}.jsonc").write_text(
                text, encoding="utf-8")
        if active is not None:
            (profiles_dir / ".active").write_text(active + "\n",
                                                  encoding="utf-8")
        if omo_text is not None:
            (home / ".omo" / "omo.jsonc").write_text(omo_text,
                                                     encoding="utf-8")
        yield home


def _run_cli(args, *, home, stdin=""):
    """Real-surface proof: run the module entry point under a temp HOME."""
    env = {**os.environ, "PYTHONPATH": str(SRC), "HOME": str(home)}
    env.pop("OC_SWITCHER_HOME", None)
    return subprocess.run(
        [PY, "-m", "opencode_config_switcher", *args],
        input=stdin, capture_output=True, text=True,
        env=env, timeout=60,
    )


class _FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


class VersionTests(unittest.TestCase):
    def test_version_exact_subprocess(self):
        with _home_with() as home:
            proc = _run_cli(["--version"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"{__version__}\n")
        self.assertEqual(proc.stderr, "")

    def test_version_after_subcommand(self):
        with _home_with() as home:
            proc = _run_cli(["list", "--version"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"{__version__}\n")

    def test_version_direct_call(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            with self.assertRaises(SystemExit) as caught:
                cli.main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(out.getvalue(), f"{__version__}\n")


class UsageErrorTests(unittest.TestCase):
    def test_unknown_subcommand_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["bogus"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("invalid choice", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_unknown_flag_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["--frobnicate"], home=home)
        self.assertEqual(proc.returncode, 2)

    def test_unknown_subcommand_direct_raises_system_exit_2(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            with self.assertRaises(SystemExit) as caught:
                cli.main(["bogus"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("invalid choice", err.getvalue())

    def test_show_requires_name_exits_2(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["show"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stderr)


class ListTests(unittest.TestCase):
    def test_empty_store_hints_and_exits_1(self):
        with _home_with() as home:
            proc = _run_cli(["list"], home=home)
            profiles_dir = home / ".omo" / "profiles"
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(
            proc.stderr,
            f"No profiles found in {profiles_dir}\n{HINT}\n")

    def test_missing_profiles_dir_is_the_empty_case(self):
        with tempfile.TemporaryDirectory(prefix="ocs-task8-") as home:
            proc = _run_cli(["list"], home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("No profiles found in", proc.stderr)

    def test_active_managed_marker(self):
        with _home_with({"alpha": ALPHA_TEXT, "beta": BETA_TEXT}) as home:
            use_profile(Paths.build(home), "alpha")
            proc = _run_cli(["list"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "  alpha [active]\n  beta\n")

    def test_active_drifted_marker(self):
        with _home_with({"alpha": ALPHA_TEXT, "beta": BETA_TEXT},
                        active="alpha") as home:
            proc = _run_cli(["list"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "  alpha [custom]\n  beta\n")

    def test_invalid_marker(self):
        with _home_with({"broken": BROKEN_TEXT}) as home:
            proc = _run_cli(["list"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "  broken [invalid]\n")

    def test_active_invalid_profile_is_custom_and_invalid(self):
        with _home_with({"broken": BROKEN_TEXT}, active="broken") as home:
            proc = _run_cli(["list"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "  broken [custom] [invalid]\n")


class ShowTests(unittest.TestCase):
    def test_structured_valid(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["show", "alpha"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertIn("Profile: alpha", lines)
        self.assertIn("State: inactive", lines)
        self.assertIn("Agents (1):", lines)
        self.assertIn("  build: provider-1/model-a", lines)

    def test_raw_prints_cached_bytes_verbatim(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["show", "alpha", "--raw"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, ALPHA_TEXT)

    def test_missing_exits_2(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["show", "nope"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Profile 'nope' not found\n")
        self.assertEqual(proc.stdout, "")

    def test_invalid_profile_still_shows(self):
        with _home_with({"broken": BROKEN_TEXT}) as home:
            proc = _run_cli(["show", "broken"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertTrue(any(line.startswith("INVALID: ") for line in lines),
                        proc.stdout)
        self.assertIn("Profile: broken", lines)

    def test_invalid_name_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["show", "a/b"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Invalid profile name", proc.stderr)


class ActiveTests(unittest.TestCase):
    def test_managed(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            use_profile(Paths.build(home), "alpha")
            proc = _run_cli(["active"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "Active profile: alpha\n")

    def test_drifted(self):
        with _home_with({"alpha": ALPHA_TEXT}, active="alpha") as home:
            proc = _run_cli(["active"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout, "custom (configuration drifted from 'alpha')\n")

    def test_no_marker(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["active"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "custom (no profile active)\n")

    def test_stale_marker_names_missing_profile(self):
        with _home_with({"alpha": ALPHA_TEXT}, active="ghost") as home:
            proc = _run_cli(["active"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "custom (no profile active)\n")


class BarePlainSelectorTests(unittest.TestCase):
    def test_apply_end_to_end(self):
        with _home_with({"alpha": ALPHA_TEXT, "beta": BETA_TEXT},
                        omo_text=LIVE_TEXT) as home:
            paths = Paths.build(home)
            live_before = paths.omo_path.read_bytes()
            proc = _run_cli([], home=home, stdin="1\n")
            backup_line = f"Backup saved to: {paths.omo_backup}\n"
            expected_stdout = (
                "Available profiles:\n"
                "  1) alpha\n"
                "  2) beta\n"
                "Select 1-2 or q: "
                "Profile applied: alpha\n"
                + backup_line
            )
            rendered = render_document(
                OmoDocument(raw=jsonc_loads(ALPHA_TEXT)),
                OmoDocument(raw=jsonc_loads(LIVE_TEXT)))
            expected_omo = jsonc_dumps(rendered)
            actual_omo = paths.omo_path.read_text(encoding="utf-8")
            active = (paths.profiles_dir / ".active").read_text(
                encoding="utf-8")
            backup = paths.omo_backup.read_bytes()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, expected_stdout)
        self.assertEqual(actual_omo, expected_omo)
        self.assertEqual(active, "alpha\n")
        self.assertEqual(backup, live_before)

    def test_noop_on_managed_active(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            use_profile(Paths.build(home), "alpha")
            live = (home / ".omo" / "omo.jsonc").read_bytes()
            proc = _run_cli([], home=home, stdin="1\n")
            live_after = (home / ".omo" / "omo.jsonc").read_bytes()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "Available profiles:\n"
            "  1) alpha [active]\n"
            "Select 1-1 or q: "
            "No change: profile 'alpha' is already active\n")
        self.assertEqual(live_after, live)

    def test_quit(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli([], home=home, stdin="q\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "Available profiles:\n"
            "  1) alpha\n"
            "Select 1-1 or q: "
            "Exiting without changes\n")

    def test_eof(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli([], home=home, stdin="")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Exiting without changes", proc.stdout)

    def test_out_of_range_exits_2(self):
        with _home_with({"alpha": ALPHA_TEXT, "beta": BETA_TEXT}) as home:
            proc = _run_cli([], home=home, stdin="9\n")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            proc.stderr, "Invalid selection: '9'; expected 1-2 or q\n")

    def test_non_integer_exits_2(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli([], home=home, stdin="abc\n")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            proc.stderr, "Invalid selection: 'abc'; expected 1-1 or q\n")

    def test_blocked_invalid_profile_exits_2(self):
        with _home_with({"alpha": ALPHA_TEXT, "broken": BROKEN_TEXT}) as home:
            proc = _run_cli([], home=home, stdin="2\n")
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(
            proc.stderr.startswith("Cannot apply invalid profile: broken:"),
            proc.stderr)

    def test_empty_store_exits_1(self):
        with _home_with() as home:
            proc = _run_cli([], home=home, stdin="1\n")
            profiles_dir = home / ".omo" / "profiles"
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stderr,
            f"No profiles found in {profiles_dir}\n{HINT}\n")


class TtyDispatchTests(unittest.TestCase):
    def test_tty_routes_to_selector_stub(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            recorded = {}

            def fake_selector(summaries, paths):
                recorded["summaries"] = summaries
                recorded["paths"] = paths
                return cli.TuiHandleOutcome.QUIT

            fake_out = _FakeTty()
            with mock.patch("sys.stdin", _FakeTty("")), \
                    mock.patch("sys.stdout", fake_out), \
                    mock.patch.object(cli, "run_tui_selector", fake_selector), \
                    mock.patch.dict(os.environ,
                                    {"TERM": "xterm-256color",
                                     "HOME": str(home)}):
                code = cli.main([])
        self.assertEqual(code, 0)
        self.assertIn("Exiting without changes", fake_out.getvalue())
        self.assertEqual(len(recorded["summaries"]), 1)
        self.assertEqual(recorded["summaries"][0].record.name, "alpha")
        self.assertEqual(recorded["paths"], Paths.build(home))

    def test_term_dumb_forces_plain_even_with_tty_streams(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            fake_out = _FakeTty()
            with mock.patch("sys.stdin", _FakeTty("")), \
                    mock.patch("sys.stdout", fake_out), \
                    mock.patch.dict(os.environ,
                                    {"TERM": "dumb", "HOME": str(home)}), \
                    mock.patch.object(
                        cli, "run_tui_selector",
                        side_effect=AssertionError("TUI must not start")), \
                    mock.patch("builtins.input", return_value="q"):
                code = cli.main([])
        self.assertEqual(code, 0)
        self.assertIn("Available profiles:", fake_out.getvalue())
        self.assertIn("Exiting without changes", fake_out.getvalue())

    def test_pipe_stdout_forces_plain_even_with_tty_stdin(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            fake_out = io.StringIO()
            with mock.patch("sys.stdin", _FakeTty("")), \
                    mock.patch("sys.stdout", fake_out), \
                    mock.patch.dict(os.environ,
                                    {"TERM": "xterm-256color",
                                     "HOME": str(home)}), \
                    mock.patch.object(
                        cli, "run_tui_selector",
                        side_effect=AssertionError("TUI must not start")), \
                    mock.patch("builtins.input", return_value="q"):
                code = cli.main([])
        self.assertEqual(code, 0)
        self.assertIn("Available profiles:", fake_out.getvalue())


class StreamOwnershipTests(unittest.TestCase):
    def test_core_modules_never_print(self):
        for name in ("engine.py", "profiles.py", "tui_data.py"):
            text = (SRC / "opencode_config_switcher" / name).read_text(
                encoding="utf-8")
            self.assertNotIn("print(", text, name)

    def test_plain_paths_never_import_curses_at_module_top(self):
        source = (SRC / "opencode_config_switcher" / "cli.py").read_text(
            encoding="utf-8")
        self.assertNotIn("import curses", source.split("def ")[0])


if __name__ == "__main__":
    unittest.main()
