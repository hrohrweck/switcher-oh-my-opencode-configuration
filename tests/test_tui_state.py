"""Tests for pure TUI state transitions and layout modes (v3 selector).

Covers the v2 navigation contracts (kept verbatim) plus the v3 prompt
state machine (``n`` create / ``D`` delete), the EDITOR_AVAILABLE-gated
``e``/``i``/``r`` intents, and prompt-mode key capture.
"""

import unittest
from unittest import mock

import opencode_config_switcher.tui as tui_mod
from opencode_config_switcher.tui import (
    AppState, LayoutMode, NarrowPane, compute_layout, handle_key,
    )


class LayoutTests(unittest.TestCase):
    def test_too_small_cols(self):
        self.assertEqual(compute_layout(39, 24), LayoutMode.TOO_SMALL)

    def test_too_small_rows(self):
        self.assertEqual(compute_layout(80, 11), LayoutMode.TOO_SMALL)

    def test_too_small_both(self):
        self.assertEqual(compute_layout(30, 8), LayoutMode.TOO_SMALL)

    def test_narrow_boundary(self):
        self.assertEqual(compute_layout(40, 12), LayoutMode.NARROW)
        self.assertEqual(compute_layout(99, 17), LayoutMode.NARROW)
        self.assertEqual(compute_layout(80, 24), LayoutMode.NARROW)

    def test_wide_boundary(self):
        self.assertEqual(compute_layout(100, 18), LayoutMode.WIDE)
        self.assertEqual(compute_layout(120, 40), LayoutMode.WIDE)


class StateTransitionsTests(unittest.TestCase):
    def _state(self, **kw):
        s = AppState(config_count=5)
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    # ── root quit keys ─────────────────────────────────────────────

    def test_q_quits_all_modes(self):
        for lm in LayoutMode:
            s = self._state(layout=lm)
            self.assertEqual(handle_key(s, "q"), "quit")

    def test_ctrlc_d_quit(self):
        s = self._state()
        self.assertEqual(handle_key(s, "ctrlc"), "quit")
        self.assertEqual(handle_key(s, "ctrld"), "quit")

    # ── TOO_SMALL ──────────────────────────────────────────────────

    def test_too_small_ignores_keys(self):
        s = self._state(layout=LayoutMode.TOO_SMALL)
        for k in ("enter", "tab", "d", "up", "down",
                  "pageup", "pagedown", "n", "D", "e", "i", "r"):
            self.assertIsNone(handle_key(s, k),
                              f"TOO_SMALL should ignore {k}")

    def test_too_small_allows_quit(self):
        s = self._state(layout=LayoutMode.TOO_SMALL)
        self.assertEqual(handle_key(s, "q"), "quit")

    # ── WIDE mode ──────────────────────────────────────────────────

    def test_wide_up_down_selection(self):
        s = self._state(layout=LayoutMode.WIDE, selected_idx=2)
        handle_key(s, "up")
        self.assertEqual(s.selected_idx, 1)
        handle_key(s, "down")
        self.assertEqual(s.selected_idx, 2)
        handle_key(s, "down")
        self.assertEqual(s.selected_idx, 3)
        # Boundary
        handle_key(s, "down")
        handle_key(s, "down")
        self.assertEqual(s.selected_idx, 4)  # max is config_count-1

    def test_wide_selection_resets_details(self):
        s = self._state(layout=LayoutMode.WIDE, detail_offset=10)
        handle_key(s, "down")
        self.assertEqual(s.detail_offset, 0)

    def test_wide_detail_scroll(self):
        s = self._state(layout=LayoutMode.WIDE, detail_offset=5)
        handle_key(s, "pagedown")
        self.assertEqual(s.detail_offset, 10)
        handle_key(s, "pageup")
        self.assertEqual(s.detail_offset, 5)
        handle_key(s, "pageup")
        handle_key(s, "pageup")
        self.assertEqual(s.detail_offset, 0)  # clamped

    def test_wide_apply(self):
        s = self._state(layout=LayoutMode.WIDE)
        self.assertEqual(handle_key(s, "enter"), "apply")

    def test_wide_d_toggles_raw(self):
        s = self._state(layout=LayoutMode.WIDE)
        handle_key(s, "d")
        self.assertTrue(s.detail_raw)
        self.assertEqual(s.detail_offset, 0)
        handle_key(s, "d")
        self.assertFalse(s.detail_raw)

    def test_wide_tab_noop(self):
        s = self._state(layout=LayoutMode.WIDE)
        self.assertIsNone(handle_key(s, "tab"))

    # ── NARROW mode ────────────────────────────────────────────────

    def test_narrow_menu_selection(self):
        s = self._state(layout=LayoutMode.NARROW,
                        narrow_pane=NarrowPane.MENU)
        self.assertEqual(handle_key(s, "down"), None)
        self.assertEqual(s.selected_idx, 1)

    def test_narrow_tab_switch(self):
        s = self._state(layout=LayoutMode.NARROW,
                        narrow_pane=NarrowPane.MENU)
        handle_key(s, "tab")
        self.assertEqual(s.narrow_pane, NarrowPane.DETAILS)
        handle_key(s, "tab")
        self.assertEqual(s.narrow_pane, NarrowPane.MENU)

    def test_narrow_details_scroll(self):
        s = self._state(layout=LayoutMode.NARROW,
                        narrow_pane=NarrowPane.DETAILS)
        handle_key(s, "down")
        self.assertEqual(s.detail_offset, 1)
        handle_key(s, "pagedown")
        self.assertEqual(s.detail_offset, 6)
        handle_key(s, "up")
        self.assertEqual(s.detail_offset, 5)

    def test_narrow_apply_from_details(self):
        s = self._state(layout=LayoutMode.NARROW,
                        narrow_pane=NarrowPane.DETAILS)
        self.assertEqual(handle_key(s, "enter"), "apply")

    def test_narrow_menu_d_switches_to_details_and_toggles_raw(self):
        s = self._state(layout=LayoutMode.NARROW,
                        narrow_pane=NarrowPane.MENU)
        handle_key(s, "d")
        self.assertTrue(s.detail_raw)
        self.assertEqual(s.narrow_pane, NarrowPane.DETAILS)

    def test_narrow_details_d_toggles_raw(self):
        s = self._state(layout=LayoutMode.NARROW,
                        narrow_pane=NarrowPane.DETAILS)
        handle_key(s, "d")
        self.assertTrue(s.detail_raw)
        handle_key(s, "d")
        self.assertFalse(s.detail_raw)

    def test_d_resets_detail_offset(self):
        s = self._state(layout=LayoutMode.WIDE,
                        detail_offset=10, detail_raw=False)
        handle_key(s, "d")
        self.assertEqual(s.detail_offset, 0)

    # ── Space ignored (outside prompts) ────────────────────────────

    def test_space_ignored(self):
        for lm in [LayoutMode.WIDE, LayoutMode.NARROW]:
            s = self._state(layout=lm)
            self.assertIsNone(handle_key(s, " "))

    # ── v3: create / delete intents ────────────────────────────────

    def test_wide_n_returns_create(self):
        s = self._state(layout=LayoutMode.WIDE)
        self.assertEqual(handle_key(s, "n"), "create")
        self.assertIsNone(s.prompt)  # controller opens the prompt

    def test_wide_D_returns_delete(self):
        s = self._state(layout=LayoutMode.WIDE)
        self.assertEqual(handle_key(s, "D"), "delete")

    def test_narrow_menu_n_and_D(self):
        s = self._state(layout=LayoutMode.NARROW,
                        narrow_pane=NarrowPane.MENU)
        self.assertEqual(handle_key(s, "n"), "create")
        self.assertEqual(handle_key(s, "D"), "delete")

    def test_narrow_details_n_and_D(self):
        s = self._state(layout=LayoutMode.NARROW,
                        narrow_pane=NarrowPane.DETAILS)
        self.assertEqual(handle_key(s, "n"), "create")
        self.assertEqual(handle_key(s, "D"), "delete")

    def test_uppercase_N_is_not_create(self):
        s = self._state(layout=LayoutMode.WIDE)
        self.assertIsNone(handle_key(s, "N"))

    def test_lowercase_d_is_not_delete(self):
        s = self._state(layout=LayoutMode.WIDE)
        self.assertIsNone(handle_key(s, "d"))  # overlay toggle, not delete
        self.assertTrue(s.detail_raw)  # toggled ON by that press
        self.assertEqual(handle_key(s, "D"), "delete")


class PromptStateTests(unittest.TestCase):
    """Keys captured while a prompt is open."""

    def _prompt(self, prompt="create", buffer="", **kw):
        s = AppState(config_count=3, layout=LayoutMode.WIDE,
                     prompt=prompt, prompt_label="New profile name: ",
                     prompt_buffer=buffer)
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    def test_enter_returns_prompt_submit(self):
        s = self._prompt(buffer="gamma")
        self.assertEqual(handle_key(s, "enter"), "prompt_submit")
        # State survives so the controller can read prompt/buffer.
        self.assertEqual(s.prompt, "create")
        self.assertEqual(s.prompt_buffer, "gamma")

    def test_printable_chars_append(self):
        s = self._prompt()
        for ch in "gamma":
            self.assertIsNone(handle_key(s, ch))
        self.assertEqual(s.prompt_buffer, "gamma")

    def test_q_appends_instead_of_quitting(self):
        s = self._prompt()
        self.assertIsNone(handle_key(s, "q"))
        self.assertEqual(s.prompt_buffer, "q")

    def test_uppercase_D_appends(self):
        s = self._prompt()
        self.assertIsNone(handle_key(s, "D"))
        self.assertEqual(s.prompt_buffer, "D")

    def test_space_appends(self):
        s = self._prompt()
        self.assertIsNone(handle_key(s, " "))
        self.assertEqual(s.prompt_buffer, " ")

    def test_backspace_removes_last_char(self):
        s = self._prompt(buffer="ab")
        self.assertIsNone(handle_key(s, "backspace"))
        self.assertEqual(s.prompt_buffer, "a")
        handle_key(s, "backspace")
        self.assertEqual(s.prompt_buffer, "")
        handle_key(s, "backspace")
        self.assertEqual(s.prompt_buffer, "")  # clamped at empty

    def test_esc_cancels_prompt(self):
        s = self._prompt(buffer="xx")
        self.assertIsNone(handle_key(s, "esc"))
        self.assertIsNone(s.prompt)
        self.assertEqual(s.prompt_buffer, "")

    def test_ctrlc_cancels_prompt_not_quit(self):
        s = self._prompt(buffer="xx")
        self.assertIsNone(handle_key(s, "ctrlc"))
        self.assertIsNone(s.prompt)

    def test_ctrld_cancels_prompt_not_quit(self):
        s = self._prompt(buffer="xx")
        self.assertIsNone(handle_key(s, "ctrld"))
        self.assertIsNone(s.prompt)

    def test_navigation_keys_ignored_during_prompt(self):
        s = self._prompt(buffer="a", detail_raw=False, detail_offset=7,
                         narrow_pane=NarrowPane.MENU)
        # Non-printable pseudo-keys never leak into navigation state.
        for k in ("up", "down", "tab", "pageup", "pagedown"):
            self.assertIsNone(handle_key(s, k), f"prompt ate {k}")
        self.assertEqual(s.prompt_buffer, "a")
        self.assertFalse(s.detail_raw)
        self.assertEqual(s.detail_offset, 7)
        self.assertEqual(s.narrow_pane, NarrowPane.MENU)
        # Printable selector keys are CHARACTERS inside the prompt.
        for ch in "dneirDq":
            handle_key(s, ch)
        self.assertEqual(s.prompt_buffer, "adneirDq")


class EditorFlagTests(unittest.TestCase):
    """e/i/r are registered but inert while EDITOR_AVAILABLE is False."""

    def test_eir_no_intent_while_unavailable(self):
        for lm in (LayoutMode.WIDE, LayoutMode.NARROW):
            for pane in (NarrowPane.MENU, NarrowPane.DETAILS):
                s = AppState(config_count=2, layout=lm, narrow_pane=pane)
                for k in ("e", "i", "r"):
                    self.assertIsNone(handle_key(s, k))

    def test_eir_intents_when_available(self):
        with mock.patch.object(tui_mod, "EDITOR_AVAILABLE", True):
            s = AppState(config_count=2, layout=LayoutMode.WIDE)
            self.assertEqual(handle_key(s, "e"), "edit")
            self.assertEqual(handle_key(s, "i"), "import")
            self.assertEqual(handle_key(s, "r"), "replace")

    def test_eir_available_in_narrow_details_too(self):
        with mock.patch.object(tui_mod, "EDITOR_AVAILABLE", True):
            s = AppState(config_count=2, layout=LayoutMode.NARROW,
                         narrow_pane=NarrowPane.DETAILS)
            self.assertEqual(handle_key(s, "e"), "edit")

    def test_flag_defaults_false(self):
        self.assertFalse(tui_mod.EDITOR_AVAILABLE)


if __name__ == "__main__":
    unittest.main()
