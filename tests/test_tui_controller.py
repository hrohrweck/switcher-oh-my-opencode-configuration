"""Controller tests for the v3 profile selector: run_profile_tui over
fake SelectorServices (zero file I/O) driving complete interactive flows.

Locked contracts:
- Enter: APPLIED/NOOP exit carrying the UseResult; BLOCKED/FAILED stay
  interactive with the engine message in the footer.
- n create / D delete prompt flows (empty/invalid/duplicate names,
  confirm/decline, refresh + selection clamping).
- Empty store: Enter and D surface a "No profiles" footer.
- Resize preserves selection; e/i/r are inert while EDITOR_AVAILABLE
  is False; quit/interrupt/fatal/termination outcomes survive v2.
"""

import curses
import signal
import unittest
from pathlib import Path
from unittest import mock

from opencode_config_switcher.engine import UseResult, UseStatus
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.profiles import (
    InvalidProfileName, ProfileExistsError, ProfileNotFoundError)
from opencode_config_switcher.tui import (
    SelectorServices, TuiOutcome, run_profile_tui)
import opencode_config_switcher.tui as tui_mod

from tests.test_tui_rendering import _summary

HOME = Path("/tmp/fake-controller-home")


def _use(status, profile="alpha", message="engine message"):
    return UseResult(status=status, profile=profile,
                     omo_path=HOME / "omo.jsonc",
                     backup=HOME / "omo.jsonc.BAK", message=message)


class ControllerTestCase(unittest.TestCase):
    """Mocked curses + fake services; getch fed from a key list."""

    def setUp(self):
        self._patches = []
        to_mock = [
            "curs_set", "has_colors", "start_color", "use_default_colors",
            "init_pair", "color_pair", "update_lines_cols",
        ]
        for name in to_mock:
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
        self.use_fn = mock.MagicMock(return_value=_use(UseStatus.APPLIED))
        self.create_fn = mock.MagicMock(return_value=None)
        self.delete_fn = mock.MagicMock(return_value=None)
        self.refresh_fn = mock.MagicMock(return_value=[])
        self.services = SelectorServices(
            use_fn=self.use_fn, create_fn=self.create_fn,
            delete_fn=self.delete_fn, refresh_fn=self.refresh_fn)

    def tearDown(self):
        for p in getattr(self, "_patches", []):
            p.stop()

    # ── helpers ────────────────────────────────────────────────────

    def _run(self, summaries, keys):
        self.stdscr.getch.side_effect = list(keys)
        return run_profile_tui(summaries, Paths.build(HOME), self.services)

    def _texts(self):
        return [c.args[2] for c in self.stdscr.addstr.call_args_list]

    def _frame_contains(self, needle):
        return any(needle in text for text in self._texts())

    @staticmethod
    def _type(text):
        return [ord(ch) for ch in text]


class ApplyFlowTests(ControllerTestCase):

    def test_enter_applied_exits_carrying_use_result(self):
        result = _use(UseStatus.APPLIED, "alpha", "Profile applied: alpha")
        self.use_fn.return_value = result
        outcome = self._run([_summary("alpha"), _summary("beta")], [10])
        self.assertEqual(outcome.outcome, TuiOutcome.APPLIED)
        self.assertIs(outcome.apply_result, result)
        self.use_fn.assert_called_once_with("alpha")

    def test_enter_noop_exits_carrying_use_result(self):
        result = _use(UseStatus.NOOP, "alpha",
                      "No change: profile 'alpha' is already active")
        self.use_fn.return_value = result
        outcome = self._run([_summary("alpha")], [10])
        self.assertEqual(outcome.outcome, TuiOutcome.NOOP)
        self.assertIs(outcome.apply_result, result)

    def test_enter_blocked_stays_interactive_with_footer(self):
        self.use_fn.return_value = _use(
            UseStatus.BLOCKED, "ghost", "Profile 'ghost' not found")
        outcome = self._run([_summary("alpha")], [10, ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.use_fn.assert_called_once_with("alpha")
        self.assertTrue(self._frame_contains("Profile 'ghost' not found"))

    def test_enter_failed_stays_interactive_with_footer(self):
        message = "Failed to render configuration: OSError: disk full"
        self.use_fn.return_value = _use(UseStatus.FAILED, "alpha", message)
        outcome = self._run([_summary("alpha")], [10, ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.assertTrue(self._frame_contains(message))

    def test_blocked_long_message_clipped_to_width(self):
        long_message = "m" * 300
        self.use_fn.return_value = _use(UseStatus.BLOCKED, "alpha",
                                        long_message)
        self._run([_summary("alpha")], [10, ord("q")])
        written = [text for text in self._texts()
                   if "mmmmm" in text or long_message[:10] in text]
        self.assertTrue(written)
        for text in written:
            self.assertLessEqual(tui_mod.display_width(text), 120)

    def test_navigation_then_enter_applies_selected(self):
        outcome = self._run([_summary("alpha"), _summary("beta")],
                            [curses.KEY_DOWN, 10])
        self.assertEqual(outcome.outcome, TuiOutcome.APPLIED)
        self.use_fn.assert_called_once_with("beta")

    def test_resize_preserves_selection(self):
        outcome = self._run([_summary("alpha"), _summary("beta")],
                            [curses.KEY_DOWN, curses.KEY_RESIZE, 10])
        self.assertEqual(outcome.outcome, TuiOutcome.APPLIED)
        self.use_fn.assert_called_once_with("beta")


class EmptyStoreTests(ControllerTestCase):

    def test_enter_on_empty_list_footers_no_profiles(self):
        outcome = self._run([], [10, ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.use_fn.assert_not_called()
        self.assertTrue(self._frame_contains("No profiles"))

    def test_delete_on_empty_list_footers_no_profiles(self):
        outcome = self._run([], [ord("D"), ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.delete_fn.assert_not_called()
        self.assertFalse(self._frame_contains("Delete profile"))
        self.assertTrue(self._frame_contains("No profiles"))


class CreateFlowTests(ControllerTestCase):

    def _create_keys(self, name):
        return [ord("n"), *self._type(name), 10]

    def test_create_success_refreshes_and_footers(self):
        self.refresh_fn.return_value = [
            _summary("alpha"), _summary("gamma")]
        outcome = self._run([_summary("alpha")],
                            [*self._create_keys("gamma"), ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.create_fn.assert_called_once_with("gamma")
        self.refresh_fn.assert_called_once_with()
        self.assertTrue(self._frame_contains("Profile created: gamma"))
        self.assertTrue(self._frame_contains("New profile name: gamma"))

    def test_create_empty_name_footers_and_skips_service(self):
        outcome = self._run([_summary("alpha")],
                            [ord("n"), 10, ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.create_fn.assert_not_called()
        self.assertTrue(
            self._frame_contains("Profile name must not be empty"))

    def test_create_whitespace_only_name_is_empty(self):
        outcome = self._run([_summary("alpha")],
                            [ord("n"), *self._type("   "), 10, ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.create_fn.assert_not_called()
        self.assertTrue(
            self._frame_contains("Profile name must not be empty"))

    def test_create_invalid_name_footers_str_of_exception(self):
        self.create_fn.side_effect = InvalidProfileName("a/b")
        outcome = self._run([_summary("alpha")],
                            [*self._create_keys("a/b"), ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.assertTrue(self._frame_contains("Invalid profile name: 'a/b'"))

    def test_create_duplicate_footers(self):
        self.create_fn.side_effect = ProfileExistsError("alpha")
        outcome = self._run([_summary("alpha")],
                            [*self._create_keys("alpha"), ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.refresh_fn.assert_not_called()
        self.assertTrue(
            self._frame_contains("Profile already exists: alpha"))

    def test_create_prompt_esc_cancels_without_service(self):
        outcome = self._run([_summary("alpha")],
                            [ord("n"), *self._type("ga"), 27, ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.create_fn.assert_not_called()
        self.assertFalse(self._frame_contains("Profile created"))

    def test_create_backspace_edits_buffer(self):
        self.refresh_fn.return_value = [_summary("alpha"), _summary("gama")]
        outcome = self._run(
            [_summary("alpha")],
            [ord("n"), *self._type("gamaX"), 127,
             *self._type(""), 10, ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.create_fn.assert_called_once_with("gama")


class DeleteFlowTests(ControllerTestCase):

    def _delete_keys(self, answer):
        return [ord("D"), *self._type(answer), 10]

    def test_delete_confirmed_deletes_refreshes_and_footers(self):
        self.refresh_fn.return_value = [_summary("beta")]
        outcome = self._run([_summary("alpha"), _summary("beta")],
                            [*self._delete_keys("y"), ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.delete_fn.assert_called_once_with("alpha")
        self.refresh_fn.assert_called_once_with()
        self.assertTrue(self._frame_contains("Deleted profile: alpha"))
        self.assertTrue(
            self._frame_contains("Delete profile 'alpha'? [y/N]: y"))

    def test_delete_declined_cancels_without_service(self):
        outcome = self._run([_summary("alpha")],
                            [*self._delete_keys("n"), ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.delete_fn.assert_not_called()
        self.refresh_fn.assert_not_called()
        self.assertTrue(self._frame_contains("Delete cancelled"))

    def test_delete_enter_alone_declines(self):
        outcome = self._run([_summary("alpha")],
                            [ord("D"), 10, ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.delete_fn.assert_not_called()
        self.assertTrue(self._frame_contains("Delete cancelled"))

    def test_delete_uppercase_y_confirms(self):
        self.refresh_fn.return_value = []
        outcome = self._run([_summary("alpha")],
                            [*self._delete_keys("Y"), ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.delete_fn.assert_called_once_with("alpha")

    def test_delete_last_profile_clamps_to_empty(self):
        self.refresh_fn.return_value = []
        outcome = self._run([_summary("alpha")],
                            [*self._delete_keys("y"), 10, ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.assertTrue(self._frame_contains("Deleted profile: alpha"))
        self.assertTrue(self._frame_contains("No profiles"))
        self.use_fn.assert_not_called()

    def test_delete_missing_profile_footers_not_found(self):
        self.delete_fn.side_effect = ProfileNotFoundError("alpha")
        outcome = self._run([_summary("alpha")],
                            [*self._delete_keys("y"), ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.assertTrue(self._frame_contains("Profile 'alpha' not found"))

    def test_delete_selected_row_refresh_keeps_index_clamped(self):
        # Selected row is the LAST of three; deleting it clamps to beta,
        # and the following Enter APPLIES the clamped selection.
        self.refresh_fn.return_value = [
            _summary("alpha"), _summary("beta")]
        outcome = self._run(
            [_summary("alpha"), _summary("beta"), _summary("gamma")],
            [curses.KEY_DOWN, curses.KEY_DOWN,
             *self._delete_keys("y"), 10])
        self.assertEqual(outcome.outcome, TuiOutcome.APPLIED)
        self.delete_fn.assert_called_once_with("gamma")
        self.use_fn.assert_called_once_with("beta")


class EditorKeyTests(ControllerTestCase):

    def test_eir_inert_while_unavailable(self):
        outcome = self._run([_summary("alpha")],
                            [ord("e"), ord("i"), ord("r"), ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.use_fn.assert_not_called()
        self.create_fn.assert_not_called()

    def test_eir_still_inert_when_flag_flips_until_task16(self):
        with mock.patch.object(tui_mod, "EDITOR_AVAILABLE", True):
            outcome = self._run([_summary("alpha")],
                                [ord("e"), ord("i"), ord("r"), ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.use_fn.assert_not_called()


class LifecycleTests(ControllerTestCase):

    def test_quit_with_q(self):
        outcome = self._run([_summary("alpha")], [ord("q")])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)
        self.assertIsNone(outcome.apply_result)

    def test_quit_with_ctrl_d(self):
        outcome = self._run([_summary("alpha")], [4])
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)

    def test_keyboard_interrupt(self):
        with mock.patch.object(
                tui_mod.curses, "wrapper",
                side_effect=KeyboardInterrupt()):
            outcome = run_profile_tui(
                [_summary("alpha")], Paths.build(HOME), self.services)
        self.assertEqual(outcome.outcome, TuiOutcome.QUIT)

    def test_fatal_exception(self):
        with mock.patch.object(
                tui_mod.curses, "wrapper",
                side_effect=RuntimeError("boom")):
            outcome = run_profile_tui(
                [_summary("alpha")], Paths.build(HOME), self.services)
        self.assertEqual(outcome.outcome, TuiOutcome.FATAL)
        self.assertEqual(outcome.error_type, "RuntimeError")

    def test_service_exception_is_fatal_not_crash(self):
        self.use_fn.side_effect = RuntimeError("injected use failure")
        outcome = self._run([_summary("alpha")], [10])
        self.assertEqual(outcome.outcome, TuiOutcome.FATAL)
        self.assertEqual(outcome.error_type, "RuntimeError")

    def test_signal_handlers_installed_and_restored(self):
        with mock.patch("signal.signal") as sig:
            self._run([_summary("alpha")], [ord("q")])
        registered = {call.args[0] for call in sig.call_args_list}
        self.assertIn(signal.SIGHUP, registered)
        self.assertIn(signal.SIGTERM, registered)
        # Two installs + two restorations.
        self.assertGreaterEqual(sig.call_count, 4)


if __name__ == "__main__":
    unittest.main()
