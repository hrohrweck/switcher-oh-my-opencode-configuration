"""Task 17 — first-run onboarding contracts (plain + TUI surfaces).

Locked behavior (RULING from the task brief):
- Non-TTY bare + zero profiles keeps Task 8's `_report_empty_store`
  (stderr hint, exit 1) — onboarding is interactive-only.
- stdin-TTY bare + zero profiles: stdout ``No profiles found.`` plus a
  numbered one-shot chooser offering ONLY available options (current
  capture when ~/.omo/omo.jsonc exists, legacy import when discovery
  finds files, Skip always), then the normal bare selector dispatch;
  still-empty store afterwards → `_report_empty_store` exit 1.
- Neither source: stdout header + stderr guidance naming BOTH paths,
  exit 1.
- TUI: `run_profile_tui` entered with empty summaries AND services
  carrying ``import_fn`` + ``capture_fn`` renders the same chooser as a
  modal BEFORE the main loop; action → refresh → populated: selector
  loop (status in footer); still empty: status + any-key → QUIT.

Hermetic: temp HOME everywhere; subprocess runs pin the non-TTY path.
"""

import contextlib
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
from opencode_config_switcher.engine import UseStatus
from opencode_config_switcher.jsonc import dumps as jsonc_dumps
from opencode_config_switcher.jsonc import loads as jsonc_loads
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.profiles import list_profiles
from opencode_config_switcher.transform import discover_legacy
from opencode_config_switcher.tui import (
    OnboardingState, SelectorServices, TuiOutcome, onboarding_key,
    run_profile_tui)
import opencode_config_switcher.tui as tui_mod

SRC = Path(__file__).resolve().parents[1] / "src"
PY = sys.executable
SCHEMA = ("https://raw.githubusercontent.com/code-yeongyu/"
          "oh-my-openagent/dev/assets/omo.schema.json")

LIVE_TEXT = jsonc_dumps({
    "$schema": SCHEMA,
    "[opencode]": {"agents": {"build": {"model": "old/model"}}},
    "[codex]": {"k": 1},
})
BROKEN_LIVE = "{\n  \"[opencode]\": {},,\n}\n"
HINT = ("Run 'opencode-config-switcher import --all-legacy' or "
        "'import --current' to get started.")

CURRENT_LABEL = "Import current ~/.omo/omo.jsonc as profile 'current'"
LEGACY_LABEL = "Import legacy configuration files"
SKIP_LABEL = "Skip"


def _legacy_json(model: str) -> str:
    return json.dumps({"model_fallback": False,
                       "agents": {"build": {"model": model}}})


@contextlib.contextmanager
def _home_with(profiles=None, *, omo_text=None):
    with tempfile.TemporaryDirectory(prefix="ocs-task17-") as name:
        home = Path(name)
        profiles_dir = home / ".omo" / "profiles"
        profiles_dir.mkdir(parents=True)
        for profile_name, text in (profiles or {}).items():
            (profiles_dir / f"{profile_name}.jsonc").write_text(
                text, encoding="utf-8")
        if omo_text is not None:
            (home / ".omo" / "omo.jsonc").write_text(omo_text,
                                                     encoding="utf-8")
        yield home


@contextlib.contextmanager
def _home_with_legacy(files, *, omo_text=None):
    with _home_with(omo_text=omo_text) as home:
        legacy = home / ".config" / "opencode"
        legacy.mkdir(parents=True)
        for name, text in files.items():
            (legacy / name).write_text(text, encoding="utf-8")
        yield home


class _FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _run_cli(args, *, home, stdin=""):
    env = {**os.environ, "PYTHONPATH": str(SRC), "HOME": str(home)}
    env.pop("OC_SWITCHER_HOME", None)
    return subprocess.run(
        [PY, "-m", "opencode_config_switcher", *args],
        input=stdin, capture_output=True, text=True, env=env, timeout=60)


# ── plain (CLI) onboarding ─────────────────────────────────────────

class PlainOnboardingTestCase(unittest.TestCase):
    """Direct-call harness: TTY stdin (+TTY stdout for TUI dispatch),
    mocked input(), sentinel run_tui_selector."""

    def _run_bare(self, home, answers, *, term="xterm-256color",
                  selector=None):
        """Run ``cli.main([])`` under a fake TTY.

        ``answers`` feeds mocked input() calls in order.  ``selector``
        replaces run_tui_selector (default: record + QUIT).
        """
        inputs = iter(answers)
        prompts = []
        recorded = {}

        def fake_input(prompt=""):
            prompts.append(prompt)
            return next(inputs)

        if selector is None:
            def selector(summaries, paths):
                recorded["summaries"] = summaries
                recorded["paths"] = paths
                return cli.TuiHandleOutcome.QUIT

        out, err = _FakeTty(), io.StringIO()
        with mock.patch("sys.stdin", _FakeTty()), \
                mock.patch("sys.stdout", out), \
                mock.patch("sys.stderr", err), \
                mock.patch.dict(os.environ,
                                {"TERM": term, "HOME": str(home)}), \
                mock.patch("builtins.input", side_effect=fake_input), \
                mock.patch.object(cli, "run_tui_selector",
                                  side_effect=selector):
            code = cli.main([])
        return code, out.getvalue(), err.getvalue(), prompts, recorded


class PlainOnboardingTests(PlainOnboardingTestCase):
    def test_only_legacy_option_2_imports_and_opens_tty_selector(self):
        files = {
            "oh-my-openagent-alpha.json": _legacy_json("p/a"),
            "oh-my-openagent-beta.json": _legacy_json("p/b"),
        }
        with _home_with_legacy(files) as home:
            paths = Paths.build(home)
            code, out, err, prompts, recorded = self._run_bare(
                home, ["2"])
            stored = sorted(r.name for r in list_profiles(paths))
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out,
            "No profiles found.\n"
            f"2) {LEGACY_LABEL}\n"
            f"3) {SKIP_LABEL}\n"
            "Imported profile: alpha "
            "(from oh-my-openagent-alpha.json)\n"
            "Imported profile: beta "
            "(from oh-my-openagent-beta.json)\n"
            "Exiting without changes\n")
        self.assertEqual(prompts, ["Choose 2-3 or q: "])
        self.assertEqual(err, "")
        self.assertEqual(stored, ["alpha", "beta"])
        self.assertEqual(len(recorded["summaries"]), 2)
        self.assertEqual(recorded["paths"], paths)

    def test_only_omo_option_1_captures_current(self):
        with _home_with(omo_text=LIVE_TEXT) as home:
            paths = Paths.build(home)
            live_before = paths.omo_path.read_bytes()
            code, out, err, prompts, recorded = self._run_bare(
                home, ["1"])
            stored = (paths.profiles_dir / "current.jsonc").read_text(
                encoding="utf-8")
            live_after = paths.omo_path.read_bytes()
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out,
            "No profiles found.\n"
            f"1) {CURRENT_LABEL}\n"
            f"3) {SKIP_LABEL}\n"
            f"Imported profile: current (from {paths.omo_path})\n"
            "Exiting without changes\n")
        self.assertEqual(prompts, ["Choose 1 or 3 or q: "])
        self.assertEqual(stored, jsonc_dumps(jsonc_loads(LIVE_TEXT)))
        self.assertEqual(live_after, live_before)
        self.assertEqual(len(recorded["summaries"]), 1)

    def test_both_sources_show_three_options(self):
        files = {"oh-my-openagent-alpha.json": _legacy_json("p/a")}
        with _home_with_legacy(files, omo_text=LIVE_TEXT) as home:
            paths = Paths.build(home)
            code, out, err, prompts, _ = self._run_bare(home, ["3"])
            stored = list_profiles(paths)
        self.assertEqual(
            out,
            "No profiles found.\n"
            f"1) {CURRENT_LABEL}\n"
            f"2) {LEGACY_LABEL}\n"
            f"3) {SKIP_LABEL}\n")
        self.assertEqual(err,
                         f"No profiles found in {paths.profiles_dir}\n"
                         f"{HINT}\n")
        self.assertEqual(code, 1)
        self.assertEqual(stored, [])
        self.assertEqual(prompts, ["Choose 1-3 or q: "])

    def test_neither_source_exits_1_naming_both_paths(self):
        with _home_with() as home:
            paths = Paths.build(home)
            code, out, err, prompts, recorded = self._run_bare(
                home, answers=[])
        self.assertEqual(code, 1)
        self.assertEqual(out, "No profiles found.\n")
        self.assertEqual(
            err,
            f"No configuration found at {paths.omo_path} and no legacy "
            f"files in {paths.legacy_dir}.\n")
        self.assertEqual(prompts, [])
        self.assertEqual(recorded, {})

    def test_q_quits_then_empty_store_hint_exits_1(self):
        files = {"oh-my-openagent-alpha.json": _legacy_json("p/a")}
        with _home_with_legacy(files) as home:
            paths = Paths.build(home)
            code, out, err, _, recorded = self._run_bare(home, ["q"])
        self.assertEqual(code, 1)
        self.assertIn("Exiting without changes\n", out)
        self.assertEqual(
            err, f"No profiles found in {paths.profiles_dir}\n{HINT}\n")
        self.assertEqual(recorded, {})

    def test_eof_quits_then_empty_store_hint_exits_1(self):
        def eof_input(prompt=""):
            raise EOFError
        with _home_with_legacy(
                {"oh-my-openagent-alpha.json": _legacy_json("p/a")}) as home:
            paths = Paths.build(home)
            out, err = _FakeTty(), io.StringIO()
            with mock.patch("sys.stdin", _FakeTty()), \
                    mock.patch("sys.stdout", out), \
                    mock.patch("sys.stderr", err), \
                    mock.patch.dict(os.environ, {
                        "TERM": "xterm-256color", "HOME": str(home)}), \
                    mock.patch("builtins.input", side_effect=eof_input), \
                    mock.patch.object(
                        cli, "run_tui_selector",
                        side_effect=AssertionError("must not start")):
                code = cli.main([])
        self.assertEqual(code, 1)
        self.assertIn("Exiting without changes\n", out.getvalue())
        self.assertIn(f"No profiles found in {paths.profiles_dir}\n",
                      err.getvalue())

    def test_invalid_selection_exits_2(self):
        files = {"oh-my-openagent-alpha.json": _legacy_json("p/a")}
        with _home_with_legacy(files, omo_text=LIVE_TEXT) as home:
            code, out, err, _, _ = self._run_bare(home, ["9"])
        self.assertEqual(code, 2)
        self.assertEqual(
            err, "Invalid selection: '9'; expected 1-3 or q\n")
        self.assertTrue(out.startswith("No profiles found.\n"))

    def test_stdin_tty_only_falls_through_to_plain_selector(self):
        """TERM=dumb: onboarding runs, then the PLAIN selector applies."""
        files = {"oh-my-openagent-alpha.json": _legacy_json("p/a"),
                 "oh-my-openagent-beta.json": _legacy_json("p/b")}
        with _home_with_legacy(files) as home:
            paths = Paths.build(home)
            code, out, err, prompts, _ = self._run_bare(
                home, ["2", "1"], term="dumb",
                selector=AssertionError("TUI must not start"))
            omo = paths.omo_path.read_text(encoding="utf-8")
            active = (paths.profiles_dir / ".active").read_text(
                encoding="utf-8")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out,
            "No profiles found.\n"
            f"2) {LEGACY_LABEL}\n"
            f"3) {SKIP_LABEL}\n"
            "Imported profile: alpha "
            "(from oh-my-openagent-alpha.json)\n"
            "Imported profile: beta "
            "(from oh-my-openagent-beta.json)\n"
            "Available profiles:\n"
            "  1) alpha\n"
            "  2) beta\n"
            "Profile applied: alpha\n"
            f"Backup saved to: {paths.omo_backup}\n")
        self.assertEqual(prompts,
                         ["Choose 2-3 or q: ", "Select 1-2 or q: "])
        self.assertIn('"[opencode]"', omo)
        self.assertEqual(active, "alpha\n")

    def test_capture_failure_returns_engine_code(self):
        """Option 1 on an invalid live file → BLOCKED → exit 2."""
        with _home_with_legacy({}, omo_text=BROKEN_LIVE) as home:
            paths = Paths.build(home)
            code, out, err, _, recorded = self._run_bare(home, ["1"])
            stored = list_profiles(paths)
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith(
            "Cannot import invalid configuration:"), err)
        self.assertEqual(stored, [])
        self.assertEqual(recorded, {})


class NonTtyKeepsTask8BehaviorTests(unittest.TestCase):
    def test_piped_std_in_bare_still_reports_empty_store(self):
        with _home_with_legacy(
                {"oh-my-openagent-alpha.json": _legacy_json("p/a")},
                omo_text=LIVE_TEXT) as home:
            paths = Paths.build(home)
            proc = _run_cli([], home=home, stdin="1\n")
            stored = list_profiles(paths)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stderr,
            f"No profiles found in {paths.profiles_dir}\n{HINT}\n")
        self.assertEqual(proc.stdout, "")
        self.assertEqual(stored, [])

    def test_list_subcommand_hint_unchanged(self):
        with _home_with_legacy(
                {"oh-my-openagent-alpha.json": _legacy_json("p/a")}) as home:
            paths = Paths.build(home)
            proc = _run_cli(["list"], home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stderr,
            f"No profiles found in {paths.profiles_dir}\n{HINT}\n")


# ── TUI onboarding modal ───────────────────────────────────────────

class TuiOnboardingTestCase(unittest.TestCase):
    """Controller-pattern curses mocks (mirrors test_tui_controller)."""

    def setUp(self):
        self._patches = []
        for name in ("curs_set", "has_colors", "start_color",
                     "use_default_colors", "init_pair", "color_pair",
                     "update_lines_cols"):
            p = mock.patch.object(tui_mod.curses, name)
            m = p.start()
            self._patches.append(p)
            if name == "has_colors":
                m.return_value = False
            elif name == "color_pair":
                m.return_value = 0
        self.stdscr = mock.MagicMock()
        self.stdscr.getmaxyx.return_value = (40, 120)
        p = mock.patch.object(
            tui_mod.curses, "wrapper",
            side_effect=lambda fn: fn(self.stdscr))
        p.start()
        self._patches.append(p)
        self._patches.append(mock.patch("locale.setlocale").start())

    def tearDown(self):
        for p in getattr(self, "_patches", []):
            p.stop()

    def _texts(self):
        return [c.args[2] for c in self.stdscr.addstr.call_args_list]

    def _frame_contains(self, needle):
        return any(needle in text for text in self._texts())


class TuiOnboardingModalTests(TuiOnboardingTestCase):
    def _real_services(self, home):
        return cli.build_selector_services(Paths.build(home))

    def test_legacy_import_opens_selector_loop(self):
        files = {"oh-my-openagent-alpha.json": _legacy_json("p/a"),
                 "oh-my-openagent-beta.json": _legacy_json("p/b")}
        with _home_with_legacy(files) as home:
            paths = Paths.build(home)
            self.stdscr.getch.side_effect = [10, ord("q")]
            outcome = run_profile_tui(
                [], paths, self._real_services(home))
            stored = sorted(r.name for r in list_profiles(paths))
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.assertEqual(stored, ["alpha", "beta"])
        self.assertTrue(self._frame_contains(LEGACY_LABEL))
        self.assertTrue(self._frame_contains("Imported 2/2 profile(s)"))
        # The selector loop ran afterwards: menu rows were rendered.
        self.assertTrue(self._frame_contains("alpha"))

    def test_capture_current_opens_selector_loop(self):
        with _home_with(omo_text=LIVE_TEXT) as home:
            paths = Paths.build(home)
            self.stdscr.getch.side_effect = [10, ord("q")]
            outcome = run_profile_tui(
                [], paths, self._real_services(home))
            stored = (paths.profiles_dir / "current.jsonc").read_text(
                encoding="utf-8")
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.assertEqual(stored, jsonc_dumps(jsonc_loads(LIVE_TEXT)))
        self.assertTrue(self._frame_contains(CURRENT_LABEL))
        self.assertTrue(self._frame_contains("Profile captured: current"))

    def test_capture_blocked_shows_status_then_quit(self):
        with _home_with_legacy({}, omo_text=BROKEN_LIVE) as home:
            paths = Paths.build(home)
            self.stdscr.getch.side_effect = [10, 10]
            outcome = run_profile_tui(
                [], paths, self._real_services(home))
            stored = list_profiles(paths)
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.assertEqual(stored, [])
        self.assertTrue(self._frame_contains(
            "Cannot import invalid configuration:"))
        self.assertTrue(self._frame_contains("(press any key)"))

    def test_skip_returns_quit_when_still_empty(self):
        with _home_with() as home:
            paths = Paths.build(home)
            self.stdscr.getch.side_effect = [ord("q"), 10]
            outcome = run_profile_tui(
                [], paths, self._real_services(home))
            stored = list_profiles(paths)
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.assertEqual(stored, [])
        self.assertTrue(self._frame_contains(SKIP_LABEL))
        self.assertTrue(self._frame_contains("Onboarding skipped"))

    def test_no_services_means_no_onboarding_modal(self):
        home = Path("/tmp/fake-onboarding-home")
        services = SelectorServices(
            use_fn=mock.MagicMock(), create_fn=mock.MagicMock(),
            delete_fn=mock.MagicMock(), refresh_fn=mock.MagicMock(
                return_value=[]))
        self.stdscr.getch.side_effect = [10, ord("q")]
        outcome = run_profile_tui([], Paths.build(home), services)
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.assertFalse(self._frame_contains(LEGACY_LABEL))
        self.assertFalse(self._frame_contains("get started"))
        self.assertTrue(self._frame_contains("No profiles"))


class OnboardingKeyTests(unittest.TestCase):
    def _state(self, numbers=(1, 2, 3)):
        return OnboardingState(numbers=list(numbers),
                               actions=list("cls"), labels=list("abc"))

    def test_enter_selects_cursor(self):
        state = self._state()
        self.assertEqual(onboarding_key(state, "enter"), "select")

    def test_up_down_clamp(self):
        state = self._state()
        self.assertIsNone(onboarding_key(state, "up"))
        self.assertEqual(state.cursor, 0)
        onboarding_key(state, "down")
        onboarding_key(state, "down")
        onboarding_key(state, "down")
        self.assertEqual(state.cursor, 2)
        onboarding_key(state, "up")
        self.assertEqual(state.cursor, 1)

    def test_digit_jumps_and_selects(self):
        state = self._state()
        self.assertIsNone(onboarding_key(state, "5"))
        self.assertEqual(onboarding_key(state, "2"), "select")
        self.assertEqual(state.cursor, 1)
        self.assertEqual(onboarding_key(state, "1"), "select")
        self.assertEqual(state.cursor, 0)

    def test_digit_selects_canonical_number_not_position(self):
        state = self._state(numbers=(2, 3))
        self.assertEqual(onboarding_key(state, "2"), "select")
        self.assertEqual(state.cursor, 0)
        self.assertIsNone(onboarding_key(state, "1"))
        self.assertEqual(state.cursor, 0)

    def test_q_and_esc_select_skip(self):
        state = self._state()
        self.assertEqual(onboarding_key(state, "q"), "select")
        self.assertEqual(state.actions[state.cursor], "s")
        state = self._state()
        self.assertEqual(onboarding_key(state, "esc"), "select")
        self.assertEqual(state.actions[state.cursor], "s")

    def test_done_any_key_closes(self):
        state = self._state()
        state.done = True
        self.assertEqual(onboarding_key(state, "x"), "close")
        self.assertEqual(onboarding_key(state, "enter"), "close")


class ServicesWiringTests(unittest.TestCase):
    def test_build_selector_services_carries_capture_fn(self):
        with _home_with(omo_text=LIVE_TEXT) as home:
            paths = Paths.build(home)
            services = cli.build_selector_services(paths)
            self.assertIsNotNone(services.capture_fn)
            result = services.capture_fn("current")
            stored = (paths.profiles_dir / "current.jsonc").is_file()
        self.assertEqual(result.status, UseStatus.APPLIED)
        self.assertEqual(result.message, "Profile captured: current")
        self.assertTrue(stored)

    def test_import_fn_discovers_legacy_candidates(self):
        files = {"oh-my-openagent-alpha.json": _legacy_json("p/a")}
        with _home_with_legacy(files) as home:
            paths = Paths.build(home)
            services = cli.build_selector_services(paths)
            candidates = services.import_fn(paths)
            expected = [p.name for p in discover_legacy(paths)]
        self.assertEqual([c.path.name for c in candidates], expected)
        self.assertTrue(all(c.invalid is None for c in candidates))


if __name__ == "__main__":
    unittest.main()
