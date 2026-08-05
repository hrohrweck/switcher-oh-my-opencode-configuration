"""Tests for pure TUI formatting and display-width helpers."""

import unittest
from pathlib import Path

from opencode_config_switcher.config import (
    ConfigSummary, FileSummary, ModelSpec, RouteSummary,
    RuntimeFallbackSummary)
from opencode_config_switcher.tui import (
    display_width, truncate_display, format_details, format_overlay,
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


class FormatDetailsTests(unittest.TestCase):
    def _summary(self, **kw):
        defaults = dict(
            file=FileSummary(
                path=Path("/tmp/test.json"),
                name="test.json",
                size_bytes=100,
                modified_ns=123456789,
                is_current=False,
                raw_text='{"key":"val"}'),
            is_valid=True,
            model_fallback=True,
            runtime_fallback=RuntimeFallbackSummary(enabled=True),
            agents=(),
            categories=(),
        )
        defaults.update(kw)
        return ConfigSummary(**defaults)

    def test_basic_file_info(self):
        lines = format_details(self._summary(), 60)
        text = "\n".join(lines)
        self.assertIn("File:", text)
        self.assertIn("test.json", text)
        self.assertIn("VALID", text)

    def test_current_indicator(self):
        s = self._summary(file=FileSummary(
            path=Path("/tmp/active.json"),
            name="active.json",
            size_bytes=50,
            modified_ns=1,
            is_current=True,
            raw_text="{}"))
        lines = format_details(s, 60)
        self.assertIn("CURRENT VALID", "\n".join(lines))

    def test_invalid_indicator(self):
        s = self._summary(is_valid=False, error="trailing comma")
        lines = format_details(s, 60)
        self.assertIn("INVALID", "\n".join(lines))
        self.assertIn("trailing comma", "\n".join(lines))

    def test_agents_section(self):
        s = self._summary(agents=(
            RouteSummary("sisyphus",
                         ModelSpec(model="gpt-5", variant="max"),
                         (ModelSpec(model="k3"),), ()),
            RouteSummary("oracle",
                         ModelSpec(model="gpt-5.6", variant="xhigh"),
                         (ModelSpec(model="k3"),), ()),
        ))
        lines = format_details(s, 60)
        text = "\n".join(lines)
        self.assertIn("Agents (2)", text)
        self.assertIn("sisyphus", text)
        self.assertIn("gpt-5 [max]", text)
        self.assertIn("gpt-5.6 [xhigh]", text)
        self.assertIn("[1] k3", text)

    def test_categories_section(self):
        s = self._summary(categories=(
            RouteSummary("quick",
                         ModelSpec(model="gpt-4o-mini"),
                         (), ()),
        ))
        lines = format_details(s, 60)
        text = "\n".join(lines)
        self.assertIn("Categories (1)", text)
        self.assertIn("quick", text)

    def test_empty_agents_categories(self):
        lines = format_details(self._summary(), 60)
        text = "\n".join(lines)
        self.assertIn("none configured", text)

    def test_warnings_appear(self):
        s = self._summary(
            agents=(
                RouteSummary("x",
                             ModelSpec(model="m"),
                             (ModelSpec(model="ok"),),
                             ("fallback[0]: bad entry",)),),
            warnings=("Top-level warning",))
        lines = format_details(s, 60)
        text = "\n".join(lines)
        self.assertIn("bad entry", text)
        self.assertIn("Top-level warning", text)

    def test_missing_model_label(self):
        s = self._summary(agents=(
            RouteSummary("x", ModelSpec(model=None), (), ()),))
        lines = format_details(s, 60)
        text = "\n".join(lines)
        self.assertIn("not configured", text)

    def test_reasoning_effort(self):
        s = self._summary(agents=(
            RouteSummary("librarian",
                         ModelSpec(model="gpt4",
                                   reasoning_effort="high"),
                         (), ()),))
        lines = format_details(s, 60)
        text = "\n".join(lines)
        self.assertIn("gpt4 (effort: high)", text)

    def test_truncation(self):
        s = self._summary(agents=(
            RouteSummary("very-long-agent-name-that-exceeds-width",
                         ModelSpec(model="gpt-5"), (), ()),))
        lines = format_details(s, 20)
        for line in lines:
            self.assertLessEqual(display_width(line), 20)


class FormatOverlayTests(unittest.TestCase):
    def test_overlay_lines(self):
        raw = '{"key": "val"}\n{"key2": 42}\n'
        lines = format_overlay(raw, 30)
        self.assertEqual(len(lines), 2)
        self.assertIn("key", lines[0])

    def test_overlay_truncation(self):
        raw = 'x' * 100
        lines = format_overlay(raw, 20)
        self.assertLessEqual(display_width(lines[0]), 20)


if __name__ == "__main__":
    unittest.main()
