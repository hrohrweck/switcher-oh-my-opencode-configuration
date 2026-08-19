"""Tests for pure TUI formatting: display-width helpers (byte-identical
v2 contracts, imported by tui_data) and the v3 footer/prompt composers.
"""

import unittest
from unittest import mock

import opencode_config_switcher.tui as tui_mod
from opencode_config_switcher.tui import (
    AppState, LayoutMode, NarrowPane, CREATE_PROMPT_LABEL,
    compose_footer, delete_prompt_label, display_width, truncate_display,
    )


class DisplayWidthTests(unittest.TestCase):
    def test_ascii(self):
        self.assertEqual(display_width("hello"), 5)

    def test_cjk(self):
        self.assertEqual(display_width("你好"), 4)

    def test_mixed(self):
        self.assertEqual(display_width("a你好b"), 6)

    def test_empty(self):
        self.assertEqual(display_width(""), 0)

    def test_emoji(self):
        # Emoji characters are typically East Asian Wide
        w = display_width("🎉")
        self.assertIn(w, (1, 2))  # depends on Unicode version

    def test_combining(self):
        w = display_width("e\u0301")  # e + combining acute
        self.assertEqual(w, 1)


class TruncateDisplayTests(unittest.TestCase):
    def test_no_truncation(self):
        self.assertEqual(truncate_display("hello", 10), "hello")

    def test_exact_fit(self):
        self.assertEqual(truncate_display("hello", 5), "hello")

    def test_truncate_ascii(self):
        self.assertEqual(truncate_display("hello world", 8), "hello w…")

    def test_truncate_cjk(self):
        self.assertEqual(truncate_display("你好世界", 5), "你好…")

    def test_empty(self):
        self.assertEqual(truncate_display("", 10), "")

    def test_zero_width(self):
        self.assertEqual(truncate_display("abc", 0), "")


class PromptLabelTests(unittest.TestCase):
    def test_create_prompt_label_pinned(self):
        self.assertEqual(CREATE_PROMPT_LABEL, "New profile name: ")

    def test_delete_prompt_label_exact(self):
        self.assertEqual(delete_prompt_label("alpha"),
                         "Delete profile 'alpha'? [y/N]: ")


class ComposeFooterTests(unittest.TestCase):
    """Footer text per layout/pane, status prefix, and prompt capture."""

    def _state(self, **kw):
        base = dict(layout=LayoutMode.WIDE, narrow_pane=NarrowPane.MENU)
        base.update(kw)
        return AppState(config_count=3, **base)

    def test_wide_footer_actions(self):
        footer = compose_footer(self._state())
        self.assertIn("Enter: use", footer)
        self.assertIn("n: new", footer)
        self.assertIn("D: delete", footer)
        self.assertIn("d: raw", footer)
        self.assertIn("q: quit", footer)

    def test_narrow_menu_footer(self):
        footer = compose_footer(self._state(layout=LayoutMode.NARROW))
        self.assertIn("Tab: Details", footer)
        self.assertIn("n: new", footer)
        self.assertIn("Enter: use", footer)

    def test_narrow_details_footer(self):
        footer = compose_footer(
            self._state(layout=LayoutMode.NARROW,
                        narrow_pane=NarrowPane.DETAILS))
        self.assertIn("Tab: Menu", footer)
        self.assertIn("PgUp/PgDn", footer)
        self.assertIn("d: raw", footer)

    def test_too_small_footer(self):
        footer = compose_footer(self._state(layout=LayoutMode.TOO_SMALL))
        self.assertEqual(footer, "q/Ctrl-C quit")

    def test_editor_keys_hidden_while_unavailable(self):
        for state in (self._state(),
                      self._state(layout=LayoutMode.NARROW),
                      self._state(layout=LayoutMode.NARROW,
                                  narrow_pane=NarrowPane.DETAILS)):
            footer = compose_footer(state)
            self.assertNotIn("e: edit", footer)
            self.assertNotIn("i: import", footer)
            self.assertNotIn("r: replace", footer)

    def test_editor_keys_advertised_when_available(self):
        with mock.patch.object(tui_mod, "EDITOR_AVAILABLE", True):
            footer = compose_footer(self._state())
            self.assertIn("e: edit", footer)
            self.assertIn("i: import", footer)
            self.assertIn("r: replace", footer)

    def test_status_prefixes_mode_footer(self):
        state = self._state(status="Profile created: gamma")
        footer = compose_footer(state)
        self.assertTrue(footer.startswith("Profile created: gamma  |  "))
        self.assertIn("Enter: use", footer)

    def test_create_prompt_captures_footer(self):
        state = self._state(prompt="create",
                            prompt_label=CREATE_PROMPT_LABEL,
                            prompt_buffer="ga")
        self.assertEqual(compose_footer(state), "New profile name: ga")

    def test_delete_prompt_captures_footer(self):
        state = self._state(
            prompt="delete",
            prompt_label=delete_prompt_label("alpha"),
            prompt_buffer="y")
        self.assertEqual(compose_footer(state),
                         "Delete profile 'alpha'? [y/N]: y")

    def test_prompt_hides_status(self):
        state = self._state(status="earlier message", prompt="create",
                            prompt_label=CREATE_PROMPT_LABEL,
                            prompt_buffer="")
        self.assertEqual(compose_footer(state),
                         CREATE_PROMPT_LABEL)

    def test_empty_prompt_buffer_shows_bare_label(self):
        state = self._state(prompt="create",
                            prompt_label=CREATE_PROMPT_LABEL)
        self.assertEqual(compose_footer(state), "New profile name: ")


if __name__ == "__main__":
    unittest.main()
