"""CLI contracts: argparse subcommands, bare plain selector, stream/exit ownership.

Hermetic by construction: every subprocess runs with a temp HOME and
PYTHONPATH=src; every direct call patches HOME so Path.home() resolves into
the sandbox.  Only cli.py may print user output or choose exit codes.
"""

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opencode_config_switcher import __version__, cli
from opencode_config_switcher.engine import UseResult, UseStatus
from opencode_config_switcher.engine import render_document, use_profile
from opencode_config_switcher.jsonc import dumps as jsonc_dumps
from opencode_config_switcher.jsonc import loads as jsonc_loads
from opencode_config_switcher.omoconfig import OmoDocument
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.transform import transform_legacy
from opencode_config_switcher.tui import TuiOutcome, TuiResult

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

    def test_tty_selector_noop_exits_cleanly(self):
        """Regression for the TuiOutcome.NOOP -> TuiHandleOutcome conversion.

        Exiting the selector without changing the active profile used to
        raise ``ValueError: 'NOOP' is not a valid TuiHandleOutcome`` because
        the code constructed the enum from the string value rather than the
        member name.
        """
        with _home_with({"alpha": ALPHA_TEXT},
                        active="alpha", omo_text=LIVE_TEXT) as home:
            fake_out = _FakeTty()
            noop_result = TuiResult(
                outcome=TuiOutcome.NOOP,
                apply_result=UseResult(
                    status=UseStatus.NOOP,
                    profile="alpha",
                    omo_path=Paths.build(home).omo_path,
                    backup=Paths.build(home).omo_backup,
                    message=("No change: profile 'alpha' is already active"),
                ),
            )
            with mock.patch("sys.stdin", _FakeTty("")), \
                    mock.patch("sys.stdout", fake_out), \
                    mock.patch("opencode_config_switcher.tui.run_profile_tui",
                               return_value=noop_result), \
                    mock.patch.dict(os.environ,
                                    {"TERM": "xterm-256color",
                                     "HOME": str(home)}):
                code = cli.main([])
        self.assertEqual(code, 0)
        self.assertIn("No change: profile 'alpha' is already active",
                      fake_out.getvalue())

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


# ── Task 9: use/create/delete/import ───────────────────────────────

def _legacy_json(model: str) -> str:
    """One minimal valid legacy configuration document."""
    return json.dumps({"model_fallback": False,
                       "agents": {"build": {"model": model}}})


INVALID_LEGACY = '{"agents": {"build": {"model": "p/m"}},}'


@contextlib.contextmanager
def _home_with_legacy(files, *, profiles=None, active=None, omo_text=None):
    """Temp HOME adding a legacy tree under ~/.config/opencode."""
    with _home_with(profiles, active=active, omo_text=omo_text) as home:
        legacy = home / ".config" / "opencode"
        legacy.mkdir(parents=True)
        for name, text in files.items():
            (legacy / name).write_text(text, encoding="utf-8")
        yield home


def _tree_checksum(root: Path) -> str:
    """Stable digest over a directory tree (names + file bytes)."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _profile_names(home) -> list[str]:
    return sorted(
        path.name for path in (home / ".omo" / "profiles").iterdir()
        if not path.name.startswith("."))


class UseTests(unittest.TestCase):
    def test_applied_exact_messages_and_backup(self):
        with _home_with({"alpha": ALPHA_TEXT, "beta": BETA_TEXT},
                        omo_text=LIVE_TEXT) as home:
            proc = _run_cli(["use", "alpha"], home=home)
            backup_line = f"Backup saved to: {home / '.omo' / 'omo.jsonc.BAK'}\n"
            omo = (home / ".omo" / "omo.jsonc").read_text(encoding="utf-8")
            active = (home / ".omo" / "profiles" / ".active").read_text(
                encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout,
                         "Profile applied: alpha\n" + backup_line)
        self.assertEqual(active, "alpha\n")
        self.assertEqual(
            omo,
            jsonc_dumps(render_document(
                OmoDocument(raw=jsonc_loads(ALPHA_TEXT)),
                OmoDocument(raw=jsonc_loads(LIVE_TEXT)))))

    def test_noop(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            use_profile(Paths.build(home), "alpha")
            proc = _run_cli(["use", "alpha"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout, "No change: profile 'alpha' is already active\n")

    def test_blocked_missing_exits_2(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["use", "nope"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Profile 'nope' not found\n")
        self.assertEqual(proc.stdout, "")

    def test_blocked_invalid_exits_2(self):
        with _home_with({"broken": BROKEN_TEXT}) as home:
            proc = _run_cli(["use", "broken"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.startswith(
            "Cannot apply invalid profile: broken:"), proc.stderr)

    def test_blocked_invalid_name_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["use", "a/b"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Invalid profile name: 'a/b'\n")

    def test_without_name_reuses_plain_selector(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["use"], home=home, stdin="1\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "Available profiles:\n"
            "  1) alpha\n"
            "Select 1-1 or q: "
            "Profile applied: alpha\n"
            f"Backup saved to: {home / '.omo' / 'omo.jsonc.BAK'}\n")

    def test_without_name_tty_routes_to_selector_stub(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            fake_out = _FakeTty()
            with mock.patch("sys.stdin", _FakeTty("")), \
                    mock.patch("sys.stdout", fake_out), \
                    mock.patch.object(
                        cli, "run_tui_selector",
                        return_value=cli.TuiHandleOutcome.QUIT), \
                    mock.patch.dict(os.environ,
                                    {"TERM": "xterm-256color",
                                     "HOME": str(home)}):
                code = cli.main(["use"])
        self.assertEqual(code, 0)
        self.assertIn("Exiting without changes", fake_out.getvalue())

    def test_select_alias_matches_use(self):
        with _home_with({"alpha": ALPHA_TEXT}, omo_text=LIVE_TEXT) as home:
            proc = _run_cli(["select", "alpha"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "Profile applied: alpha\n"
            f"Backup saved to: {home / '.omo' / 'omo.jsonc.BAK'}\n")


class CreateTests(unittest.TestCase):
    def test_minimal_document(self):
        with _home_with() as home:
            proc = _run_cli(["create", "fresh"], home=home)
            stored = (home / ".omo" / "profiles" / "fresh.jsonc").read_text(
                encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "Profile created: fresh\n")
        self.assertEqual(
            stored,
            jsonc_dumps({"$schema": SCHEMA, "[opencode]": {}}))

    def test_already_exists_exits_2(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["create", "alpha"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Profile already exists: alpha\n")

    def test_invalid_name_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["create", "a/b"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Invalid profile name: 'a/b'\n")

    def test_from_copies_document(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["create", "copy", "--from", "alpha"], home=home)
            copied = (home / ".omo" / "profiles" / "copy.jsonc").read_text(
                encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "Profile created: copy\n")
        self.assertEqual(copied, jsonc_dumps(jsonc_loads(ALPHA_TEXT)))

    def test_from_missing_source_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["create", "copy", "--from", "nope"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Profile 'nope' not found\n")

    def test_from_invalid_source_exits_2(self):
        with _home_with({"broken": BROKEN_TEXT}) as home:
            proc = _run_cli(["create", "copy", "--from", "broken"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.startswith(
            "Cannot copy invalid profile: broken:"), proc.stderr)

    def test_from_invalid_source_name_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["create", "copy", "--from", "a/b"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Invalid profile name: 'a/b'\n")


class DeleteTests(unittest.TestCase):
    def test_missing_exits_2(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["delete", "nope", "--yes"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Profile 'nope' not found\n")

    def test_missing_check_precedes_non_tty_gate(self):
        with _home_with() as home:
            proc = _run_cli(["delete", "nope"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Profile 'nope' not found\n")

    def test_invalid_name_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["delete", "a/b", "--yes"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Invalid profile name: 'a/b'\n")

    def test_yes_deletes_and_reports_backup(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["delete", "alpha", "--yes"], home=home)
            bak = home / ".omo" / "profiles" / "alpha.jsonc.BAK"
            bak_exists = bak.is_file()
            remaining = _profile_names(home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout, f"Deleted profile: alpha (backup: {bak})\n")
        self.assertTrue(bak_exists)
        self.assertEqual(remaining, ["alpha.jsonc.BAK"])

    def test_yes_on_active_also_clears_marker(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            use_profile(Paths.build(home), "alpha")
            proc = _run_cli(["delete", "alpha", "--yes"], home=home)
            marker = home / ".omo" / "profiles" / ".active"
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            f"Deleted profile: alpha (backup: "
            f"{home / '.omo' / 'profiles' / 'alpha.jsonc.BAK'})\n"
            "No profile is active now.\n")
        self.assertFalse(marker.exists())

    def test_non_tty_without_yes_refuses(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["delete", "alpha"], home=home)
            survived = (home / ".omo" / "profiles" / "alpha.jsonc").is_file()
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            proc.stderr,
            "Refusing to delete without --yes in non-interactive mode\n")
        self.assertEqual(proc.stdout, "")
        self.assertTrue(survived)

    def _run_prompted(self, home, answer, *, eof=False):
        prompts, out = [], io.StringIO()

        def fake_input(prompt=""):
            prompts.append(prompt)
            if eof:
                raise EOFError
            return answer

        with mock.patch("sys.stdin", _FakeTty()), \
                mock.patch("sys.stdout", out), \
                mock.patch("builtins.input", side_effect=fake_input), \
                mock.patch.dict(os.environ, {"HOME": str(home)}):
            code = cli.main(["delete", "alpha"])
        return code, prompts, out.getvalue()

    def test_prompt_confirm_deletes(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            code, prompts, out = self._run_prompted(home, "y")
            bak = home / ".omo" / "profiles" / "alpha.jsonc.BAK"
            bak_exists = bak.is_file()
            profile_gone = not bak.parent.joinpath("alpha.jsonc").exists()
        self.assertEqual(code, 0)
        self.assertEqual(prompts, ["Delete profile 'alpha'? [y/N]: "])
        self.assertEqual(
            out, f"Deleted profile: alpha (backup: {bak})\n")
        self.assertTrue(bak_exists)
        self.assertTrue(profile_gone)

    def test_prompt_uppercase_y_confirms(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            code, prompts, _ = self._run_prompted(home, "Y")
            bak_exists = (home / ".omo" / "profiles"
                          / "alpha.jsonc.BAK").is_file()
        self.assertEqual(code, 0)
        self.assertEqual(prompts, ["Delete profile 'alpha'? [y/N]: "])
        self.assertTrue(bak_exists)

    def test_prompt_decline_keeps_profile(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            code, prompts, out = self._run_prompted(home, "n")
            survived = (home / ".omo" / "profiles" / "alpha.jsonc").is_file()
        self.assertEqual(code, 0)
        self.assertEqual(prompts, ["Delete profile 'alpha'? [y/N]: "])
        self.assertEqual(out, "Exiting without changes\n")
        self.assertTrue(survived)

    def test_prompt_eof_exits_cleanly(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            code, prompts, out = self._run_prompted(home, "", eof=True)
            survived = (home / ".omo" / "profiles" / "alpha.jsonc").is_file()
        self.assertEqual(code, 0)
        self.assertEqual(prompts, ["Delete profile 'alpha'? [y/N]: "])
        self.assertEqual(out, "Exiting without changes\n")
        self.assertTrue(survived)


class ImportCurrentTests(unittest.TestCase):
    def test_captures_without_touching_live(self):
        with _home_with(omo_text=LIVE_TEXT) as home:
            live_before = (home / ".omo" / "omo.jsonc").read_bytes()
            proc = _run_cli(["import", "--current"], home=home)
            stored = (home / ".omo" / "profiles" / "current.jsonc").read_text(
                encoding="utf-8")
            live_after = (home / ".omo" / "omo.jsonc").read_bytes()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            f"Imported profile: current "
            f"(from {home / '.omo' / 'omo.jsonc'})\n")
        self.assertEqual(live_after, live_before)
        self.assertEqual(stored, jsonc_dumps(jsonc_loads(LIVE_TEXT)))

    def test_custom_name(self):
        with _home_with(omo_text=LIVE_TEXT) as home:
            proc = _run_cli(["import", "--current", "mine"], home=home)
            stored_exists = (home / ".omo" / "profiles"
                             / "mine.jsonc").is_file()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            f"Imported profile: mine (from {home / '.omo' / 'omo.jsonc'})\n")
        self.assertTrue(stored_exists)

    def test_missing_live_file_exits_1(self):
        with _home_with() as home:
            proc = _run_cli(["import", "--current"], home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stderr,
            f"No configuration found at {home / '.omo' / 'omo.jsonc'}\n")
        self.assertEqual(proc.stdout, "")

    def test_collision_exits_2(self):
        with _home_with({"current": ALPHA_TEXT}, omo_text=LIVE_TEXT) as home:
            proc = _run_cli(["import", "--current"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Profile already exists: current\n")

    def test_force_overwrites(self):
        with _home_with({"current": ALPHA_TEXT}, omo_text=LIVE_TEXT) as home:
            proc = _run_cli(["import", "--current", "--force"], home=home)
            stored = (home / ".omo" / "profiles" / "current.jsonc").read_text(
                encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(stored, jsonc_dumps(jsonc_loads(LIVE_TEXT)))

    def test_invalid_live_file_exits_2(self):
        with _home_with(omo_text=BROKEN_TEXT) as home:
            proc = _run_cli(["import", "--current"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.startswith(
            "Cannot import invalid configuration:"), proc.stderr)

    def test_invalid_name_exits_2(self):
        with _home_with(omo_text=LIVE_TEXT) as home:
            proc = _run_cli(["import", "--current", "a/b"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Invalid profile name: 'a/b'\n")

    def test_current_composes_with_all_legacy(self):
        files = {"oh-my-openagent-a.json": _legacy_json("p/m")}
        with _home_with_legacy(files, omo_text=LIVE_TEXT) as home:
            proc = _run_cli(
                ["import", "--current", "live", "--all-legacy"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            f"Imported profile: live "
            f"(from {home / '.omo' / 'omo.jsonc'})\n"
            "Imported profile: a (from oh-my-openagent-a.json)\n")


class ImportGateTests(unittest.TestCase):
    def test_no_flags_non_interactive_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["import"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            proc.stderr,
            "Specify --all-legacy, --source PATH, or --current "
            "to import configurations.\n")
        self.assertEqual(proc.stdout, "")

    def test_no_flags_tty_still_gated(self):
        with _home_with() as home:
            err = io.StringIO()
            with mock.patch("sys.stdin", _FakeTty()), \
                    mock.patch("sys.stderr", err), \
                    mock.patch.dict(os.environ, {"HOME": str(home)}):
                code = cli.main(["import"])
        self.assertEqual(code, 2)
        self.assertIn("Specify --all-legacy", err.getvalue())

    def test_name_with_two_sources_exits_2(self):
        files = {"a.json": _legacy_json("p/m"), "b.json": _legacy_json("q/n")}
        with _home_with_legacy(files) as home:
            legacy = home / ".config" / "opencode"
            proc = _run_cli(
                ["import", "--name", "x",
                 "--source", str(legacy / "a.json"),
                 "--source", str(legacy / "b.json")], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "--name requires exactly one source\n")

    def test_name_with_zero_sources_beats_empty_dir(self):
        with _home_with() as home:
            proc = _run_cli(["import", "--all-legacy", "--name", "x"],
                            home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "--name requires exactly one source\n")

    def test_source_file_missing_exits_2(self):
        with _home_with() as home:
            missing = home / "nope.json"
            proc = _run_cli(["import", "--source", str(missing)], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, f"Source file not found: {missing}\n")

    def test_empty_legacy_dir_exits_1(self):
        with _home_with_legacy({}) as home:
            proc = _run_cli(["import", "--all-legacy"], home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stderr,
            f"No legacy configuration files found in "
            f"{home / '.config' / 'opencode'}\n")

    def test_source_takes_precedence_over_all_legacy(self):
        files = {"oh-my-openagent-g.json": _legacy_json("p/m"),
                 "oh-my-openagent-h.json": _legacy_json("q/n")}
        with _home_with_legacy(files) as home:
            chosen = home / ".config" / "opencode" / "oh-my-openagent-h.json"
            proc = _run_cli(["import", "--all-legacy", "--source", str(chosen)],
                            home=home)
            names = _profile_names(home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "Imported profile: h (from oh-my-openagent-h.json)\n")
        self.assertEqual(names, ["h.jsonc"])


class ImportBatchTests(unittest.TestCase):
    def test_single_source(self):
        files = {"oh-my-openagent-a.json": _legacy_json("p/m")}
        with _home_with_legacy(files) as home:
            src = home / ".config" / "opencode" / "oh-my-openagent-a.json"
            proc = _run_cli(["import", "--source", str(src)], home=home)
            stored = (home / ".omo" / "profiles" / "a.jsonc").read_text(
                encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "Imported profile: a (from oh-my-openagent-a.json)\n")
        self.assertEqual(
            stored,
            jsonc_dumps(transform_legacy(json.loads(_legacy_json("p/m")))[0]))

    def test_multiple_sources_import_in_cli_order(self):
        files = {"oh-my-openagent-a.json": _legacy_json("p/m"),
                 "oh-my-openagent-b.json": _legacy_json("q/n")}
        with _home_with_legacy(files) as home:
            legacy = home / ".config" / "opencode"
            proc = _run_cli(
                ["import", "--source", str(legacy / "oh-my-openagent-b.json"),
                 "--source", str(legacy / "oh-my-openagent-a.json")],
                home=home)
            names = _profile_names(home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "Imported profile: b (from oh-my-openagent-b.json)\n"
            "Imported profile: a (from oh-my-openagent-a.json)\n")
        self.assertEqual(names, ["a.jsonc", "b.jsonc"])

    def test_invalid_mid_batch_aborts_at_invalid_file(self):
        files = {"oh-my-openagent-a.json": _legacy_json("p/m"),
                 "oh-my-openagent-b.json": INVALID_LEGACY,
                 "oh-my-openagent-c.json": _legacy_json("r/o")}
        with _home_with_legacy(files) as home:
            legacy = home / ".config" / "opencode"
            before = _tree_checksum(legacy)
            proc = _run_cli(["import", "--all-legacy"], home=home)
            names = _profile_names(home)
            after = _tree_checksum(legacy)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            proc.stdout,
            "Imported profile: a (from oh-my-openagent-a.json)\n")
        self.assertTrue(proc.stderr.startswith(
            "Cannot import invalid configuration: "
            "oh-my-openagent-b.json: Invalid JSON in"), proc.stderr)
        self.assertEqual(names, ["a.jsonc"])
        self.assertEqual(after, before)

    def test_collision_aborts_batch_without_writes(self):
        files = {"oh-my-openagent-a.json": _legacy_json("p/m"),
                 "oh-my-openagent-b.json": _legacy_json("q/n")}
        with _home_with_legacy(files, profiles={"a": ALPHA_TEXT}) as home:
            proc = _run_cli(["import", "--all-legacy"], home=home)
            names = _profile_names(home)
            alpha = (home / ".omo" / "profiles" / "a.jsonc").read_text(
                encoding="utf-8")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Profile already exists: a\n")
        self.assertEqual(names, ["a.jsonc"])
        self.assertEqual(alpha, ALPHA_TEXT)

    def test_force_overwrites_with_backup(self):
        files = {"oh-my-openagent-a.json": _legacy_json("p/m")}
        with _home_with_legacy(files, profiles={"a": ALPHA_TEXT}) as home:
            proc = _run_cli(["import", "--all-legacy", "--force"], home=home)
            stored = (home / ".omo" / "profiles" / "a.jsonc").read_text(
                encoding="utf-8")
            bak = (home / ".omo" / "profiles" / "a.jsonc.BAK").read_text(
                encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            stored,
            jsonc_dumps(transform_legacy(json.loads(_legacy_json("p/m")))[0]))
        self.assertEqual(bak, ALPHA_TEXT)

    def test_warnings_print_after_success_line(self):
        legacy_doc = json.dumps({
            "agents": {"build": {"model": "p/m", "reasoning": "high",
                                  "reasoningEffort": "low"}}})
        files = {"oh-my-openagent-w.json": legacy_doc}
        with _home_with_legacy(files) as home:
            proc = _run_cli(["import", "--all-legacy"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "Imported profile: w (from oh-my-openagent-w.json)\n")
        self.assertEqual(
            proc.stderr,
            "warning: conflict: agents.build dropped "
            "reasoningEffort='low' kept reasoning='high'\n")

    def test_name_with_single_source(self):
        files = {"oh-my-openagent-a.json": _legacy_json("p/m")}
        with _home_with_legacy(files) as home:
            src = home / ".config" / "opencode" / "oh-my-openagent-a.json"
            proc = _run_cli(["import", "--source", str(src),
                             "--name", "custom"], home=home)
            names = _profile_names(home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "Imported profile: custom (from oh-my-openagent-a.json)\n")
        self.assertEqual(names, ["custom.jsonc"])


class ImportChooserTests(unittest.TestCase):
    THREE_FILES = {
        f"oh-my-openagent-{n}.json": _legacy_json(f"p/{n}")
        for n in ("a", "b", "c")
    }

    def _sources_argv(self, home):
        legacy = home / ".config" / "opencode"
        argv = ["import"]
        for name in self.THREE_FILES:
            argv += ["--source", str(legacy / name)]
        return argv

    def _run_chooser(self, home, answer, argv, *, eof=False):
        out, err, prompts = io.StringIO(), io.StringIO(), []

        def fake_input(prompt=""):
            prompts.append(prompt)
            out.write(prompt)
            if eof:
                raise EOFError
            return answer

        with mock.patch("sys.stdin", _FakeTty()), \
                mock.patch("sys.stdout", out), \
                mock.patch("sys.stderr", err), \
                mock.patch("builtins.input", side_effect=fake_input), \
                mock.patch.dict(os.environ, {"HOME": str(home)}):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue(), prompts

    MENU = ("Importable configurations:\n"
            "  1) oh-my-openagent-a.json\n"
            "  2) oh-my-openagent-b.json\n"
            "  3) oh-my-openagent-c.json\n"
            "Import 1-3, a for all, or q: ")

    def test_number_imports_exactly_one(self):
        with _home_with_legacy(self.THREE_FILES) as home:
            code, out, err, prompts = self._run_chooser(
                home, "2", self._sources_argv(home))
            names = _profile_names(home)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, self.MENU
                         + "Imported profile: b "
                         "(from oh-my-openagent-b.json)\n")
        self.assertEqual(prompts, ["Import 1-3, a for all, or q: "])
        self.assertEqual(names, ["b.jsonc"])

    def test_a_imports_all(self):
        with _home_with_legacy(self.THREE_FILES) as home:
            code, out, err, _ = self._run_chooser(
                home, "a", self._sources_argv(home))
            names = _profile_names(home)
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out, self.MENU
            + "Imported profile: a (from oh-my-openagent-a.json)\n"
            + "Imported profile: b (from oh-my-openagent-b.json)\n"
            + "Imported profile: c (from oh-my-openagent-c.json)\n")
        self.assertEqual(names, ["a.jsonc", "b.jsonc", "c.jsonc"])

    def test_uppercase_a_imports_all(self):
        with _home_with_legacy(self.THREE_FILES) as home:
            code, _, _, _ = self._run_chooser(
                home, "A", self._sources_argv(home))
            names = _profile_names(home)
        self.assertEqual(code, 0)
        self.assertEqual(names, ["a.jsonc", "b.jsonc", "c.jsonc"])

    def test_quit_imports_nothing(self):
        with _home_with_legacy(self.THREE_FILES) as home:
            code, out, err, _ = self._run_chooser(
                home, "q", self._sources_argv(home))
            names = _profile_names(home)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, self.MENU + "Exiting without changes\n")
        self.assertEqual(names, [])

    def test_uppercase_q_quits(self):
        with _home_with_legacy(self.THREE_FILES) as home:
            code, out, _, _ = self._run_chooser(
                home, "Q", self._sources_argv(home))
            names = _profile_names(home)
        self.assertEqual(code, 0)
        self.assertTrue(out.endswith("Exiting without changes\n"))
        self.assertEqual(names, [])

    def test_eof_quits(self):
        with _home_with_legacy(self.THREE_FILES) as home:
            code, out, err, _ = self._run_chooser(
                home, "", self._sources_argv(home), eof=True)
            names = _profile_names(home)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, self.MENU + "Exiting without changes\n")
        self.assertEqual(names, [])

    def test_invalid_selection_exits_2(self):
        with _home_with_legacy(self.THREE_FILES) as home:
            code, out, err, _ = self._run_chooser(
                home, "zz", self._sources_argv(home))
            names = _profile_names(home)
        self.assertEqual(code, 2)
        self.assertEqual(
            err, "Invalid selection: 'zz'; expected 1-3, a, or q\n")
        self.assertEqual(names, [])

    def test_invalid_file_marked_and_blocked_when_picked(self):
        files = {"oh-my-openagent-a.json": _legacy_json("p/a"),
                 "oh-my-openagent-b.json": INVALID_LEGACY,
                 "oh-my-openagent-c.json": _legacy_json("p/c")}
        with _home_with_legacy(files) as home:
            legacy = home / ".config" / "opencode"
            argv = ["import"]
            for name in files:
                argv += ["--source", str(legacy / name)]
            code, out, err, _ = self._run_chooser(home, "2", argv)
            names = _profile_names(home)
        self.assertEqual(code, 2)
        self.assertEqual(
            out,
            "Importable configurations:\n"
            "  1) oh-my-openagent-a.json\n"
            "  2) oh-my-openagent-b.json [invalid]\n"
            "  3) oh-my-openagent-c.json\n"
            "Import 1-3, a for all, or q: ")
        self.assertTrue(err.startswith(
            "Cannot import invalid configuration: "
            "oh-my-openagent-b.json: Invalid JSON in"), err)
        self.assertEqual(names, [])

    def test_all_legacy_skips_chooser_on_tty(self):
        with _home_with_legacy(self.THREE_FILES) as home:
            code, out, err, prompts = self._run_chooser(
                home, "should-not-be-asked", ["import", "--all-legacy"])
            names = _profile_names(home)
        self.assertEqual(code, 0, err)
        self.assertEqual(prompts, [])
        self.assertEqual(
            out,
            "Imported profile: a (from oh-my-openagent-a.json)\n"
            + "Imported profile: b (from oh-my-openagent-b.json)\n"
            + "Imported profile: c (from oh-my-openagent-c.json)\n")
        self.assertEqual(names, ["a.jsonc", "b.jsonc", "c.jsonc"])

    def test_source_with_tty_still_shows_chooser(self):
        files = {"oh-my-openagent-a.json": _legacy_json("p/a")}
        with _home_with_legacy(files) as home:
            src = home / ".config" / "opencode" / "oh-my-openagent-a.json"
            code, out, err, _ = self._run_chooser(
                home, "1", ["import", "--source", str(src)])
            names = _profile_names(home)
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out,
            "Importable configurations:\n"
            "  1) oh-my-openagent-a.json\n"
            "Import 1-1, a for all, or q: "
            "Imported profile: a (from oh-my-openagent-a.json)\n")
        self.assertEqual(names, ["a.jsonc"])


class LifecycleTests(unittest.TestCase):
    def test_full_legacy_to_use_lifecycle(self):
        names = ["balanced", "cost-efficient", "deepseek", "local",
                 "local-with-cloud-brain", "max", "smart", "smart-glm"]
        files = {f"oh-my-openagent-{n}.json": _legacy_json(f"prov/{n}")
                 for n in names}
        files["oh-my-opencode-x.json"] = _legacy_json("prov/x")
        # File order ('-' sorts before '.'): openagent-local-with-cloud-brain
        # precedes openagent-local (and smart-glm precedes smart);
        # profile-name order swaps those pairs back.
        file_order = ["balanced", "cost-efficient", "deepseek",
                      "local-with-cloud-brain", "local", "max", "smart-glm",
                      "smart"]
        with _home_with_legacy(files) as home:
            legacy = home / ".config" / "opencode"
            before = _tree_checksum(legacy)
            paths = Paths.build(home)

            proc_import = _run_cli(["import", "--all-legacy"], home=home)
            after_import = _tree_checksum(legacy)
            proc_list = _run_cli(["list"], home=home)
            proc_use = _run_cli(["use", "cost-efficient"], home=home)
            proc_raw = _run_cli(["show", "cost-efficient", "--raw"], home=home)
            live = paths.omo_path.read_text(encoding="utf-8")
            proc_del = _run_cli(["delete", "cost-efficient", "--yes"],
                                home=home)
            bak = paths.profiles_dir / "cost-efficient.jsonc.BAK"
            bak_exists = bak.is_file()
            marker_exists = paths.active_marker.exists()
            src = legacy / "oh-my-openagent-cost-efficient.json"
            proc_reimport = _run_cli(
                ["import", "--source", str(src), "--force"], home=home)
            after_all = _tree_checksum(legacy)

        expected_import = "".join(
            f"Imported profile: {n} (from oh-my-openagent-{n}.json)\n"
            for n in file_order) + \
            "Imported profile: x (from oh-my-opencode-x.json)\n"
        self.assertEqual(proc_import.returncode, 0, proc_import.stderr)
        self.assertEqual(proc_import.stdout, expected_import)
        self.assertEqual(after_import, before)
        self.assertEqual(after_all, before)

        self.assertEqual(proc_list.returncode, 0, proc_list.stderr)
        self.assertEqual(
            proc_list.stdout,
            "".join(f"  {n}\n" for n in names) + "  x\n")

        self.assertEqual(proc_use.returncode, 0, proc_use.stderr)
        self.assertEqual(
            proc_use.stdout,
            "Profile applied: cost-efficient\n"
            f"Backup saved to: {paths.omo_backup}\n")

        expected_profile = jsonc_dumps(
            transform_legacy(json.loads(_legacy_json("prov/cost-efficient")))[0])
        self.assertEqual(proc_raw.returncode, 0, proc_raw.stderr)
        self.assertEqual(proc_raw.stdout, expected_profile)
        self.assertEqual(live, expected_profile)

        self.assertEqual(proc_del.returncode, 0, proc_del.stderr)
        self.assertEqual(
            proc_del.stdout,
            f"Deleted profile: cost-efficient (backup: {bak})\n"
            "No profile is active now.\n")
        self.assertTrue(bak_exists)
        self.assertFalse(marker_exists)

        self.assertEqual(proc_reimport.returncode, 0, proc_reimport.stderr)
        self.assertEqual(
            proc_reimport.stdout,
            "Imported profile: cost-efficient "
            "(from oh-my-openagent-cost-efficient.json)\n")


# ── Task 10: replace-model + help finalization ─────────────────────

RICH_DOC = {
    "$schema": SCHEMA,
    "[opencode]": {
        "agents": {
            "build": {
                "model": "old/model",
                "fallback_models": [
                    "old/model",
                    {"model": "old/model", "reasoning": "high"},
                ],
            }
        },
        "categories": {"fast": {"models": ["old/model"]}},
        "models": {"primary": "old/model"},
    },
}
RICH_TEXT = jsonc_dumps(RICH_DOC)
RICH_REPLACED_TEXT = jsonc_dumps(json.loads(json.dumps(RICH_DOC)
                                            .replace('"old/model"',
                                                     '"new/model"')))
RICH_PREVIEW_HITS = (
    "  [opencode].build.model\n"
    "  [opencode].build.fallback_models[0]\n"
    "  [opencode].build.fallback_models[1]\n"
    "  [opencode].fast.models[0]\n"
    "  [opencode].catalog:primary\n"
)


class ReplaceModelProfileTests(unittest.TestCase):
    def test_dry_run_prints_exact_hits_and_writes_nothing(self):
        with _home_with({"rich": RICH_TEXT}) as home:
            profile = home / ".omo" / "profiles" / "rich.jsonc"
            before_bytes = profile.read_bytes()
            before_stat = profile.stat()
            proc = _run_cli(["replace-model", "old/model", "new/model",
                             "--profile", "rich", "--dry-run"], home=home)
            after_bytes = profile.read_bytes()
            after_stat = profile.stat()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(
            proc.stdout,
            "Would replace in profile 'rich':\n" + RICH_PREVIEW_HITS)
        self.assertEqual(after_bytes, before_bytes)
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
        self.assertEqual(after_stat.st_size, before_stat.st_size)

    def test_dry_run_zero_hits_prints_no_matches(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["replace-model", "zz/zz", "n/n",
                             "--profile", "alpha", "--dry-run"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(
            proc.stdout,
            "Would replace in profile 'alpha':\n  no matches\n")

    def test_dry_run_missing_profile_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["replace-model", "old/model", "new/model",
                             "--profile", "nope", "--dry-run"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Profile 'nope' not found\n")
        self.assertEqual(proc.stdout, "")

    def test_apply_on_active_profile_re_renders_omo_jsonc(self):
        with _home_with({"rich": RICH_TEXT}) as home:
            paths = Paths.build(home)
            use_profile(paths, "rich")
            proc = _run_cli(["replace-model", "old/model", "new/model",
                             "--profile", "rich"], home=home)
            profile_text = (paths.profiles_dir / "rich.jsonc").read_text(
                encoding="utf-8")
            backup_text = (paths.profiles_dir / "rich.jsonc.BAK").read_text(
                encoding="utf-8")
            omo_text = paths.omo_path.read_text(encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "Replaced 5 model reference(s) in profile 'rich'"
            "; re-rendered active configuration\n")
        self.assertEqual(profile_text, RICH_REPLACED_TEXT)
        self.assertEqual(backup_text, RICH_TEXT)
        self.assertIn("new/model", omo_text)
        self.assertNotIn("old/model", omo_text)
        self.assertIn('"reasoning": "high"', omo_text)

    def test_apply_on_inactive_profile_leaves_omo_jsonc_untouched(self):
        with _home_with({"rich": RICH_TEXT, "beta": BETA_TEXT},
                        omo_text=LIVE_TEXT) as home:
            live_before = (home / ".omo" / "omo.jsonc").read_bytes()
            proc = _run_cli(["replace-model", "old/model", "new/model",
                             "--profile", "rich"], home=home)
            live_after = (home / ".omo" / "omo.jsonc").read_bytes()
            profile_text = (home / ".omo" / "profiles" /
                            "rich.jsonc").read_text(encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout, "Replaced 5 model reference(s) in profile 'rich'\n")
        self.assertEqual(live_after, live_before)
        self.assertEqual(profile_text, RICH_REPLACED_TEXT)

    def test_no_matches_apply_exits_1(self):
        with _home_with({"alpha": ALPHA_TEXT}) as home:
            proc = _run_cli(["replace-model", "zz/zz", "n/n",
                             "--profile", "alpha"], home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stderr, "No matches for model 'zz/zz' in profile 'alpha'\n")
        self.assertEqual(proc.stdout, "")

    def test_missing_profile_apply_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["replace-model", "old/model", "new/model",
                             "--profile", "nope"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Profile 'nope' not found\n")
        self.assertEqual(proc.stdout, "")

    def test_invalid_profile_apply_exits_2(self):
        with _home_with({"broken": BROKEN_TEXT}) as home:
            proc = _run_cli(["replace-model", "old/model", "new/model",
                             "--profile", "broken"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.startswith(
            "Cannot apply invalid profile: broken:"), proc.stderr)

    def test_invalid_profile_name_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["replace-model", "a/b", "c/d",
                             "--profile", "x/y"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "Invalid profile name: 'x/y'\n")


class ReplaceModelAllTests(unittest.TestCase):
    STORE = {"beta": BETA_TEXT, "broken": BROKEN_TEXT, "rich": RICH_TEXT}

    def test_apply_across_hit_miss_invalid(self):
        with _home_with(dict(self.STORE)) as home:
            proc = _run_cli(["replace-model", "old/model", "new/model",
                             "--all"], home=home)
            rich_text = (home / ".omo" / "profiles" / "rich.jsonc").read_text(
                encoding="utf-8")
            beta_text = (home / ".omo" / "profiles" / "beta.jsonc").read_text(
                encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "beta: no matches\n"
            "Replaced 5 model reference(s) in profile 'rich'\n"
            "Replaced in 1/3 profile(s)\n")
        self.assertEqual(proc.stderr.count("\n"), 1)
        self.assertTrue(proc.stderr.startswith(
            "broken: Cannot apply invalid profile: broken:"), proc.stderr)
        self.assertEqual(rich_text, RICH_REPLACED_TEXT)
        self.assertEqual(beta_text, BETA_TEXT)

    def test_dry_run_across_all(self):
        with _home_with(dict(self.STORE)) as home:
            rich = home / ".omo" / "profiles" / "rich.jsonc"
            before = rich.read_bytes()
            proc = _run_cli(["replace-model", "old/model", "new/model",
                             "--all", "--dry-run"], home=home)
            after = rich.read_bytes()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "beta: no matches\n"
            "Would replace in profile 'rich':\n" + RICH_PREVIEW_HITS +
            "Previewed in 1/3 profile(s)\n")
        self.assertEqual(proc.stderr.count("\n"), 1)
        self.assertTrue(proc.stderr.startswith(
            "broken: Cannot apply invalid profile: broken:"), proc.stderr)
        self.assertEqual(after, before)

    def test_all_no_hits_anywhere_exits_1(self):
        with _home_with({"alpha": ALPHA_TEXT, "beta": BETA_TEXT}) as home:
            proc = _run_cli(["replace-model", "zz/zz", "n/n", "--all"],
                            home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stdout,
            "alpha: no matches\n"
            "beta: no matches\n"
            "Replaced in 0/2 profile(s)\n")
        self.assertEqual(proc.stderr, "")

    def test_all_empty_store_exits_1(self):
        with _home_with() as home:
            proc = _run_cli(["replace-model", "a/b", "c/d", "--all"],
                            home=home)
            profiles_dir = home / ".omo" / "profiles"
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(
            proc.stderr,
            f"No profiles found in {profiles_dir}\n{HINT}\n")


class ReplaceModelUsageTests(unittest.TestCase):
    def test_both_target_flags_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["replace-model", "old/model", "new/model",
                             "--profile", "rich", "--all"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stderr)
        self.assertIn("not allowed with argument", proc.stderr)

    def test_neither_target_flag_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["replace-model", "old/model", "new/model"],
                            home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stderr)
        self.assertIn(
            "one of the arguments --profile --all is required", proc.stderr)

    def test_missing_positionals_exits_2(self):
        with _home_with() as home:
            proc = _run_cli(["replace-model", "--all"], home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stderr)


class HelpFinalizationTests(unittest.TestCase):
    COMMANDS = ["list", "show", "active", "use", "select",
                "create", "delete", "import", "replace-model"]

    def test_every_subcommand_help_exits_0(self):
        with _home_with() as home:
            for command in self.COMMANDS:
                with self.subTest(command=command):
                    proc = _run_cli([command, "--help"], home=home)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertIn("usage", proc.stdout)

    def test_replace_model_help_lists_positionals_and_flags(self):
        with _home_with() as home:
            proc = _run_cli(["replace-model", "--help"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OLD", proc.stdout)
        self.assertIn("NEW", proc.stdout)
        self.assertIn("--profile", proc.stdout)
        self.assertIn("--all", proc.stdout)
        self.assertIn("--dry-run", proc.stdout)

    def test_top_level_help_mentions_every_subcommand(self):
        with _home_with() as home:
            proc = _run_cli(["--help"], home=home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for name in self.COMMANDS:
            self.assertIn(name, proc.stdout, name)


if __name__ == "__main__":
    unittest.main()
