"""Task 16 unit contracts: selector quick-actions e/r/i + the edit CLI.

Locked contracts (fake-driven; the CLI builds the real seams):

- ``e`` routes through ``SelectorServices.edit_fn`` (None seam → inert):
  SAVED → footer ``Saved profile: {name}`` + refresh; CANCELLED → ``No
  changes``; TERMINATED → ``Editor error: {error}`` (selector survives);
  invalid profile → ``Cannot edit invalid profile: {name}: {error}`` and
  the editor is never launched.
- The r-form pure machine: tab cycling OLD→NEW→checkbox→Apply, typing,
  backspace, space toggles the checkbox, Enter advances / applies; empty
  OLD guards with ``Old model must not be empty`` (no service call);
  preview lines come from a ReplaceResult via the Task-10 hit grammar.
- Apply semantics: footer shows the engine message; APPLIED refreshes
  the list; NO_MATCHES/BLOCKED write nothing; Esc cancels with no call.
- The i-screen pure machine: up/down over files+action row, space/enter
  toggles, ``a`` selects all, Enter on the action row imports, q/Esc
  close; per-file import reuses ``cli._import_legacy_file`` (captured
  output becomes the status lines; an invalid file reports the exact
  CLI error and remaining selections still import).
- ``edit NAME``: non-TTY → stderr ``Editor requires a TTY`` exit 1;
  missing/bad/invalid profile → existing messages exit 2; TTY path runs
  the editor through an injected (monkeypatched) curses wrapper stub —
  q → stdout ``No changes`` exit 0, S → save + ``Saved profile: {name}``.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opencode_config_switcher.engine import (
    ReplacementHit, ReplaceResult, UseStatus)
from opencode_config_switcher.editor import EditorOutcome, EditorResult
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.tui import (
    ImportScreenState, LegacyCandidate, ReplaceFormState,
    build_replace_preview, import_screen_key, replace_form_key,
    replace_hit_lines)
import opencode_config_switcher.tui as tui_mod

from tests.test_tui_controller import ControllerTestCase
from tests.test_tui_rendering import _summary

HOME = Path("/tmp/fake-editor-flows-home")


def _invalid_summary(name):
    """Invalid summary with a CONTRACT-shaped record (document=None —
    real invalid records never carry a parsed document)."""
    return _summary(name, invalid=True, document=None)


def _replace(status, hits=(), profile="alpha", message="engine message"):
    return ReplaceResult(status=status, profile=profile, hits=hits,
                         message=message)


def _hits(*triples):
    return tuple(
        ReplacementHit(section=s, route=r, field=f, old="old", new="new")
        for s, r, f in triples)


# ── selector `e` routing ───────────────────────────────────────────

class EditKeyRoutingTests(ControllerTestCase):

    def _edit_fn(self, outcome, error=None):
        return mock.MagicMock(return_value=EditorResult(
            outcome, {}, error))

    def test_e_saved_footers_and_refreshes(self):
        edit_fn = self._edit_fn(EditorOutcome.SAVED)
        self.services = self.services._replace(edit_fn=edit_fn)
        self.refresh_fn.return_value = [_summary("alpha")]
        outcome = self._run([_summary("alpha")], [ord("e"), ord("q")])
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        edit_fn.assert_called_once_with("alpha")
        self.refresh_fn.assert_called_once_with()
        self.assertTrue(self._frame_contains("Saved profile: alpha"))

    def test_e_cancelled_footers_no_changes_without_refresh(self):
        edit_fn = self._edit_fn(EditorOutcome.CANCELLED)
        self.services = self.services._replace(edit_fn=edit_fn)
        outcome = self._run([_summary("alpha")], [ord("e"), ord("q")])
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        edit_fn.assert_called_once_with("alpha")
        self.refresh_fn.assert_not_called()
        self.assertTrue(self._frame_contains("No changes"))

    def test_e_terminated_footers_error_and_selector_survives(self):
        edit_fn = self._edit_fn(
            EditorOutcome.TERMINATED, "RuntimeError: injected save failure")
        self.services = self.services._replace(edit_fn=edit_fn)
        outcome = self._run([_summary("alpha")],
                            [ord("e"), ord("D"), ord("n"), 10, ord("q")])
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        self.assertTrue(self._frame_contains(
            "Editor error: RuntimeError: injected save failure"))
        # Selector is interactive again: the delete prompt really opened.
        self.assertTrue(self._frame_contains("Delete profile 'alpha'?"))

    def test_e_on_invalid_profile_never_launches_editor(self):
        edit_fn = self._edit_fn(EditorOutcome.SAVED)
        self.services = self.services._replace(edit_fn=edit_fn)
        outcome = self._run([_invalid_summary("alpha")],
                            [ord("e"), ord("q")])
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        edit_fn.assert_not_called()
        self.assertTrue(self._frame_contains(
            "Cannot edit invalid profile: alpha: "
            "Invalid JSONC at line 3: boom"))

    def test_e_on_empty_store_footers_no_profiles(self):
        edit_fn = self._edit_fn(EditorOutcome.SAVED)
        self.services = self.services._replace(edit_fn=edit_fn)
        outcome = self._run([], [ord("e"), ord("q")])
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        edit_fn.assert_not_called()
        self.assertTrue(self._frame_contains("No profiles"))

    def test_e_inert_when_seam_missing(self):
        outcome = self._run([_summary("alpha")], [ord("e"), ord("q")])
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        self.use_fn.assert_not_called()


# ── r-form pure state machine ──────────────────────────────────────

class ReplaceFormPureTests(unittest.TestCase):

    def test_tab_cycles_four_fields(self):
        form = ReplaceFormState(profile="alpha")
        for expected in (1, 2, 3, 0):
            replace_form_key(form, "tab")
            self.assertEqual(form.cursor, expected)

    def test_typing_and_backspace_edit_old_then_new(self):
        form = ReplaceFormState(profile="alpha")
        for ch in "old/model":
            replace_form_key(form, ch)
        self.assertEqual(form.old, "old/model")
        replace_form_key(form, "backspace")
        self.assertEqual(form.old, "old/mode")
        replace_form_key(form, "tab")
        for ch in "new/model":
            replace_form_key(form, ch)
        self.assertEqual(form.new, "new/model")
        # backspace inside OLD does not touch NEW and vice versa
        replace_form_key(form, "tab")   # cursor on checkbox
        replace_form_key(form, "backspace")
        self.assertEqual(form.new, "new/model")

    def test_space_toggles_checkbox_only_on_checkbox(self):
        form = ReplaceFormState(profile="alpha")
        replace_form_key(form, " ")     # cursor on OLD: no toggle
        self.assertFalse(form.all_profiles)
        self.assertEqual(form.old, " ")  # space is a typed character
        form = ReplaceFormState(profile="alpha", cursor=2)
        replace_form_key(form, " ")
        self.assertTrue(form.all_profiles)

    def test_enter_advances_until_apply_row(self):
        form = ReplaceFormState(profile="alpha")
        for _ in range(3):
            self.assertIsNone(replace_form_key(form, "enter"))
        self.assertEqual(form.cursor, 3)

    def test_enter_on_apply_with_empty_old_guards_without_intent(self):
        form = ReplaceFormState(profile="alpha", cursor=3)
        self.assertIsNone(replace_form_key(form, "enter"))
        self.assertEqual(form.error, "Old model must not be empty")

    def test_enter_on_apply_with_whitespace_old_guards(self):
        form = ReplaceFormState(profile="alpha", old="   ", cursor=3)
        self.assertIsNone(replace_form_key(form, "enter"))
        self.assertEqual(form.error, "Old model must not be empty")

    def test_enter_on_apply_with_old_emits_apply_intent(self):
        form = ReplaceFormState(profile="alpha", old="old", new="new",
                                cursor=3)
        self.assertEqual(replace_form_key(form, "enter"), "apply")
        self.assertEqual(form.error, "")

    def test_esc_closes(self):
        form = ReplaceFormState(profile="alpha", old="old")
        self.assertEqual(replace_form_key(form, "esc"), "close")

    def test_error_clears_when_old_becomes_non_empty(self):
        form = ReplaceFormState(profile="alpha", cursor=3)
        replace_form_key(form, "enter")
        self.assertEqual(form.error, "Old model must not be empty")
        form.cursor = 0
        replace_form_key(form, "x")
        form.cursor = 3
        self.assertEqual(replace_form_key(form, "enter"), "apply")


class ReplacePreviewTests(unittest.TestCase):

    def test_hit_lines_follow_task10_grammar(self):
        result = _replace(
            UseStatus.PREVIEW,
            hits=_hits(("[opencode]", "build", "model"),
                       ("[opencode]", "build", "fallback_models[1]"),
                       ("[opencode]", "", "catalog:primary")),
            message="Would replace 3 model reference(s) in profile 'alpha'")
        self.assertEqual(
            replace_hit_lines(result),
            ["  [opencode].build.model",
             "  [opencode].build.fallback_models[1]",
             "  [opencode].catalog:primary"])

    def test_preview_is_message_plus_hit_lines(self):
        result = _replace(
            UseStatus.PREVIEW, hits=_hits(("[opencode]", "a", "model")),
            message="Would replace 1 model reference(s) in profile 'alpha'")
        self.assertEqual(
            build_replace_preview(result),
            ["Would replace 1 model reference(s) in profile 'alpha'",
             "  [opencode].a.model"])

    def test_preview_no_matches_is_message_only(self):
        result = _replace(
            UseStatus.NO_MATCHES,
            message="No matches for model 'old' in profile 'alpha'")
        self.assertEqual(
            build_replace_preview(result),
            ["No matches for model 'old' in profile 'alpha'"])


# ── r-form controller semantics ────────────────────────────────────

class ReplaceFormControllerTests(ControllerTestCase):

    def _open_form(self, keys):
        # r opens the modal on the selected profile.
        return [ord("r"), *keys]

    def test_r_opens_modal_with_fields_and_hint(self):
        self.services = self.services._replace(
            replace_fn=mock.MagicMock(return_value=_replace(
                UseStatus.NO_MATCHES, message="no matches")))
        outcome = self._run([_summary("alpha")],
                            self._open_form([27, ord("q")]))
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        self.assertTrue(self._frame_contains("Old model:"))
        self.assertTrue(self._frame_contains("New model:"))
        self.assertTrue(self._frame_contains("apply to all profiles"))
        self.assertTrue(self._frame_contains("Esc: cancel"))

    def test_r_inert_without_seam(self):
        outcome = self._run([_summary("alpha")], [ord("r"), ord("q")])
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        self.use_fn.assert_not_called()

    def test_live_preview_calls_dry_run_and_renders_lines(self):
        replace_fn = mock.MagicMock(return_value=_replace(
            UseStatus.PREVIEW, hits=_hits(("[opencode]", "build", "model")),
            message="Would replace 1 model reference(s) in profile 'alpha'"))
        self.services = self.services._replace(replace_fn=replace_fn)
        keys = self._open_form([*map(ord, "old"), *([27]), ord("q")])
        outcome = self._run([_summary("alpha")], keys)
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        replace_fn.assert_called_with("alpha", "old", "", True)
        self.assertTrue(self._frame_contains(
            "Would replace 1 model reference(s) in profile 'alpha'"))
        self.assertTrue(self._frame_contains("  [opencode].build.model"))

    def test_apply_runs_real_replace_and_refreshes(self):
        calls = []

        def replace_fn(name, old, new, dry_run):
            calls.append((name, old, new, dry_run))
            if dry_run:
                return _replace(
                    UseStatus.PREVIEW,
                    message="Would replace 1 model reference(s) "
                            "in profile 'alpha'")
            return _replace(
                UseStatus.APPLIED,
                message="Replaced 1 model reference(s) in profile 'alpha'")

        self.services = self.services._replace(replace_fn=replace_fn)
        self.refresh_fn.return_value = [_summary("alpha")]
        # r, type "old", Enter, Enter, Enter (checkbox), Enter (apply)
        keys = self._open_form([*map(ord, "old"), 10, 10, 10, 10,
                                ord("q")])
        outcome = self._run([_summary("alpha")], keys)
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        self.assertEqual(calls[-1], ("alpha", "old", "", False))
        self.assertTrue(all(call[3] for call in calls[:-1]))
        self.refresh_fn.assert_called_once_with()
        self.assertTrue(self._frame_contains(
            "Replaced 1 model reference(s) in profile 'alpha'"))

    def test_zero_hit_apply_footers_no_matches_without_refresh(self):
        message = "No matches for model 'old' in profile 'alpha'"

        def replace_fn(name, old, new, dry_run):
            return _replace(UseStatus.NO_MATCHES, message=message)

        self.services = self.services._replace(replace_fn=replace_fn)
        keys = self._open_form([*map(ord, "old"), 10, 10, 10, 10,
                                ord("q")])
        outcome = self._run([_summary("alpha")], keys)
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        self.refresh_fn.assert_not_called()
        self.assertTrue(self._frame_contains(message))

    def test_empty_old_apply_shows_guard_and_never_calls(self):
        replace_fn = mock.MagicMock()
        self.services = self.services._replace(replace_fn=replace_fn)
        # r, Enter×4 straight to Apply with empty OLD, then quit.
        keys = self._open_form([10, 10, 10, 10, 27, ord("q")])
        outcome = self._run([_summary("alpha")], keys)
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        replace_fn.assert_not_called()
        self.assertTrue(self._frame_contains("Old model must not be empty"))

    def test_esc_cancels_without_apply_call(self):
        replace_fn = mock.MagicMock()
        self.services = self.services._replace(replace_fn=replace_fn)
        keys = self._open_form([*map(ord, "old"), 27, ord("q")])
        outcome = self._run([_summary("alpha")], keys)
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        # dry-run preview calls may fire, but never the apply call
        for call in replace_fn.call_args_list:
            self.assertTrue(call.args[3])
        self.assertFalse(self._frame_contains("Replaced"))

    def test_checkbox_all_passes_none_target(self):
        replace_fn = mock.MagicMock(return_value=_replace(
            UseStatus.NO_MATCHES, message="none"))
        self.services = self.services._replace(replace_fn=replace_fn)
        # r, type "old", Enter (to NEW), Enter (checkbox), space (toggle),
        # Enter (apply row), Enter (apply)
        keys = self._open_form([*map(ord, "old"), 10, 10, ord(" "), 10,
                                10, ord("q")])
        outcome = self._run([_summary("alpha")], keys)
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        self.assertEqual(
            replace_fn.call_args_list[-1], mock.call(None, "old", "", False))


# ── i-screen pure state ────────────────────────────────────────────

def _candidates(*names):
    return [LegacyCandidate(path=Path(f"/tmp/legacy/{n}.json"),
                            invalid=("Invalid JSONC" if n == "broken"
                                     else None))
            for n in names]


class ImportScreenPureTests(unittest.TestCase):

    def test_space_and_enter_toggle_selection(self):
        state = ImportScreenState(entries=_candidates("a", "b"))
        import_screen_key(state, " ")
        self.assertEqual(state.chosen, {0})
        import_screen_key(state, "enter")
        self.assertEqual(state.chosen, set())

    def test_a_selects_all(self):
        state = ImportScreenState(entries=_candidates("a", "b"))
        import_screen_key(state, "a")
        self.assertEqual(state.chosen, {0, 1})

    def test_cursor_moves_over_files_and_action_row(self):
        state = ImportScreenState(entries=_candidates("a", "b"))
        for _ in range(4):
            import_screen_key(state, "down")
        self.assertEqual(state.cursor, 2)   # clamped on the action row
        import_screen_key(state, "up")
        self.assertEqual(state.cursor, 1)

    def test_enter_on_action_row_emits_import(self):
        state = ImportScreenState(entries=_candidates("a", "b"),
                                  chosen={1})
        state.cursor = 2
        self.assertEqual(import_screen_key(state, "enter"), "import")

    def test_enter_on_file_row_toggles_not_imports(self):
        state = ImportScreenState(entries=_candidates("a"))
        self.assertIsNone(import_screen_key(state, "enter"))
        self.assertEqual(state.chosen, {0})

    def test_q_and_esc_close(self):
        state = ImportScreenState(entries=_candidates("a"))
        self.assertEqual(import_screen_key(state, "q"), "close")
        self.assertEqual(import_screen_key(state, "esc"), "close")

    def test_empty_entries_any_key_closes(self):
        state = ImportScreenState(entries=[])
        self.assertEqual(import_screen_key(state, "x"), "close")
        self.assertEqual(import_screen_key(state, "esc"), "close")


# ── i-screen import execution (reuses cli._import_legacy_file) ─────

class ImportExecutionTests(unittest.TestCase):
    """The per-file import runner against a real temp HOME."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="test-import-exec-")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.paths = Paths.build(self.home)
        self.paths.profiles_dir.mkdir(parents=True)
        legacy = self.paths.legacy_dir
        legacy.mkdir(parents=True)
        (legacy / "oh-my-openagent-one.json").write_text(json.dumps(
            {"agents": {"build": {"model": "p/one"}}}), encoding="utf-8")
        (legacy / "oh-my-openagent-two.json").write_text(json.dumps(
            {"agents": {"build": {"model": "p/two"}}}), encoding="utf-8")
        (legacy / "oh-my-openagent-broken.json").write_text(
            '{"agents": {"build": {"model": "p/x"}},}', encoding="utf-8")

    def _state(self, chosen):
        entries = [LegacyCandidate(
            path=self.paths.legacy_dir / "oh-my-openagent-broken.json"),
            LegacyCandidate(
            path=self.paths.legacy_dir / "oh-my-openagent-one.json"),
            LegacyCandidate(
            path=self.paths.legacy_dir / "oh-my-openagent-two.json")]
        return ImportScreenState(entries=entries, chosen=set(chosen))

    def test_import_reports_each_file_and_continues_past_invalid(self):
        state = self._state({0, 1, 2})   # broken first, then the good ones
        tui_mod._run_legacy_imports(self.paths, state)
        joined = "\n".join(state.status_lines)
        self.assertIn("Cannot import invalid configuration: "
                      "oh-my-openagent-broken.json", joined)
        self.assertIn("Imported profile: one", joined)
        self.assertIn("Imported profile: two", joined)
        self.assertTrue((self.paths.profiles_dir / "one.jsonc").exists())
        self.assertTrue((self.paths.profiles_dir / "two.jsonc").exists())
        self.assertFalse(
            (self.paths.profiles_dir / "broken.jsonc").exists())

    def test_import_existing_profile_reports_cli_error(self):
        (self.paths.profiles_dir / "one.jsonc").write_text(
            "{}", encoding="utf-8")
        state = self._state({1})
        tui_mod._run_legacy_imports(self.paths, state)
        joined = "\n".join(state.status_lines)
        self.assertIn("Profile already exists: one", joined)


class ImportScreenControllerTests(ControllerTestCase):

    def test_i_opens_modal_listing_files(self):
        candidates = _candidates("broken", "two")
        import_fn = mock.MagicMock(return_value=candidates)
        self.services = self.services._replace(import_fn=import_fn)
        outcome = self._run([_summary("alpha")],
                            [ord("i"), 27, ord("q")])
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        import_fn.assert_called_once()
        self.assertTrue(self._frame_contains("broken.json"))
        self.assertTrue(self._frame_contains("two.json"))
        self.assertTrue(self._frame_contains(" [invalid]"))

    def test_i_inert_without_seam(self):
        outcome = self._run([_summary("alpha")], [ord("i"), ord("q")])
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        self.use_fn.assert_not_called()

    def test_i_empty_dir_shows_message_and_any_key_dismisses(self):
        import_fn = mock.MagicMock(return_value=[])
        self.services = self.services._replace(import_fn=import_fn)
        outcome = self._run(
            [_summary("alpha")],
            [ord("i"), ord("x"), 27, ord("q")])
        self.assertEqual(outcome.outcome, tui_mod.TuiOutcome.QUIT)
        self.assertTrue(self._frame_contains(
            "No legacy configuration files found in:"))
        self.assertTrue(self._frame_contains(
            f"{Paths.build(HOME).legacy_dir}"))


# ── edit NAME CLI ──────────────────────────────────────────────────

class _StubSurface:
    """Curses-like surface feeding scripted keys to run_editor."""

    def __init__(self, keys):
        self._keys = [ord(k) if isinstance(k, str) else k for k in keys]
        self._i = 0
        self.texts = []

    def keypad(self, value):
        pass

    def erase(self):
        pass

    def getmaxyx(self):
        return (30, 100)

    def addstr(self, y, x, text, attr=0):
        self.texts.append(text)

    def refresh(self):
        pass

    def getch(self):
        if self._i >= len(self._keys):
            return ord("q")   # safety: never spin forever
        key = self._keys[self._i]
        self._i += 1
        return key


class EditCliTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="test-edit-cli-")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.profiles = self.home / ".omo" / "profiles"
        self.profiles.mkdir(parents=True)
        from opencode_config_switcher.omoconfig import OMO_SCHEMA_URL
        doc = {"$schema": OMO_SCHEMA_URL,
               "[opencode]": {"agents": {
                   "build": {"model": "provider/alpha",
                             "fallback_models": ["provider/fb"]}}}}
        from opencode_config_switcher.jsonc import dumps
        (self.profiles / "alpha.jsonc").write_text(dumps(doc),
                                                   encoding="utf-8")

    def _env(self):
        return mock.patch.dict(os.environ, {"HOME": str(self.home)})

    def test_non_tty_stderr_message_exit_1(self):
        from opencode_config_switcher import cli
        with self._env():
            code = cli.main(["edit", "alpha"])
        self.assertEqual(code, 1)

    def test_non_tty_exact_message_via_stream_capture(self):
        from opencode_config_switcher import cli
        err = io.StringIO()
        with self._env(), \
                mock.patch("sys.stderr", err):
            code = cli.main(["edit", "alpha"])
        self.assertEqual(code, 1)
        self.assertIn("Editor requires a TTY", err.getvalue())

    def test_missing_profile_exit_2(self):
        from opencode_config_switcher import cli
        err = io.StringIO()
        with self._env(), \
                mock.patch("sys.stdin", _FakeTty()), \
                mock.patch("sys.stdout", _FakeTty()), \
                mock.patch("sys.stderr", err):
            code = cli.main(["edit", "ghost"])
        self.assertEqual(code, 2)
        self.assertIn("Profile 'ghost' not found", err.getvalue())

    def test_invalid_profile_exit_2(self):
        (self.profiles / "broken.jsonc").write_text(
            "{,,}", encoding="utf-8")
        from opencode_config_switcher import cli
        err = io.StringIO()
        with self._env(), \
                mock.patch("sys.stdin", _FakeTty()), \
                mock.patch("sys.stdout", _FakeTty()), \
                mock.patch("sys.stderr", err):
            code = cli.main(["edit", "broken"])
        self.assertEqual(code, 2)
        self.assertIn("Cannot edit invalid profile: broken", err.getvalue())

    def test_tty_quit_prints_no_changes_exit_0(self):
        from opencode_config_switcher import cli
        out = _FakeTty()
        surface = _StubSurface(["q"])
        with self._env(), \
                mock.patch("sys.stdin", _FakeTty()), \
                mock.patch("sys.stdout", out), \
                mock.patch("curses.wrapper",
                           side_effect=lambda fn, *a: fn(surface, *a)):
            code = cli.main(["edit", "alpha"])
        self.assertEqual(code, 0)
        self.assertIn("No changes", out.getvalue())
        self.assertIn("Profile: alpha", " ".join(surface.texts))

    def test_tty_save_writes_profile_and_prints_saved(self):
        from opencode_config_switcher import cli
        from opencode_config_switcher.jsonc import loads
        out = _FakeTty()
        before = (self.profiles / "alpha.jsonc").read_text()
        # Given a TTY edit session on a legacy-form agent; When
        # enter-route, "," moves the fallback over the primary and "S"
        # saves; Then the store file is rewritten with a canonical
        # models list (promoted fallback first).
        surface = _StubSurface(["\r", ",", "S"])
        with self._env(), \
                mock.patch("sys.stdin", _FakeTty()), \
                mock.patch("sys.stdout", out), \
                mock.patch("curses.wrapper",
                           side_effect=lambda fn, *a: fn(surface, *a)):
            code = cli.main(["edit", "alpha"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("Saved profile: alpha", out.getvalue())
        after = (self.profiles / "alpha.jsonc").read_text()
        self.assertNotEqual(before, after)
        build = loads(after)["[opencode]"]["agents"]["build"]
        self.assertEqual(build, {"models": ["provider/fb",
                                            "provider/alpha"]})

    def test_tty_save_preserves_leading_comments(self):
        from opencode_config_switcher import cli
        from opencode_config_switcher.jsonc import loads
        out = _FakeTty()
        seed = ("// team choice\n// reviewed 2026\n"
                + json.dumps({"[opencode]": {"agents": {
                    "build": {"model": "provider/alpha",
                              "fallback_models": ["provider/fb"]}}}},
                    indent=2) + "\n")
        (self.profiles / "beta.jsonc").write_text(seed, encoding="utf-8")
        surface = _StubSurface(["\r", ",", "S"])
        with self._env(), \
                mock.patch("sys.stdin", _FakeTty()), \
                mock.patch("sys.stdout", out), \
                mock.patch("curses.wrapper",
                           side_effect=lambda fn, *a: fn(surface, *a)):
            code = cli.main(["edit", "beta"])
        self.assertEqual(code, 0, out.getvalue())
        after = (self.profiles / "beta.jsonc").read_text()
        self.assertTrue(
            after.startswith("// team choice\n// reviewed 2026\n\n"),
            after)
        build = loads(after)["[opencode]"]["agents"]["build"]
        self.assertEqual(build, {"models": ["provider/fb",
                                            "provider/alpha"]})

    def test_edit_listed_once_in_help(self):
        import re
        from opencode_config_switcher import cli
        with self._env():
            with mock.patch("sys.stdout", io.StringIO()) as out:
                with self.assertRaises(SystemExit) as caught:
                    cli.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        help_text = out.getvalue()
        listing = help_text.split("positional arguments:")[-1]
        self.assertEqual(len(re.findall(r"(?m)^ {4}edit\b", listing)), 1)


class _FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


if __name__ == "__main__":
    unittest.main()
