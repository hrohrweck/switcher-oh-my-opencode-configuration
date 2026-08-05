"""Tests for pure TUI state transitions and layout modes."""

import unittest
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
                  "pageup", "pagedown"):
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

    # ── Space ignored ──────────────────────────────────────────────

    def test_space_ignored(self):
        for lm in [LayoutMode.WIDE, LayoutMode.NARROW]:
            s = self._state(layout=lm)
            self.assertIsNone(handle_key(s, " "))


if __name__ == "__main__":
    unittest.main()
