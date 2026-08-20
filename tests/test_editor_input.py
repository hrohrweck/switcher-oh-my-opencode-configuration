"""Tests for the pure editor input components.

parse_number / parse_retry_errors / cycle_reasoning / clamp — no
curses, no Textbox: validation is pure so forms (Task 15) stay
deterministic.
"""

import unittest

from opencode_config_switcher.editor import (
    REASONING_CYCLE,
    EditorState,
    FieldError,
    cycle_reasoning,
    format_reasoning_custom,
    parse_number,
    parse_retry_errors,
)


class FieldErrorContractTests(unittest.TestCase):
    def test_field_error_is_value_error(self):
        self.assertTrue(issubclass(FieldError, ValueError))

    def test_invalid_number_message_exact(self):
        with self.assertRaises(FieldError) as ctx:
            parse_number("abc", kind="max_tokens")
        self.assertEqual(str(ctx.exception), "Invalid number: 'abc'")

    def test_temperature_range_message_exact(self):
        with self.assertRaises(FieldError) as ctx:
            parse_number("3", kind="temperature")
        self.assertEqual(str(ctx.exception),
                         "temperature must be within 0..2")

    def test_top_p_range_message_exact(self):
        with self.assertRaises(FieldError) as ctx:
            parse_number("1.5", kind="top_p")
        self.assertEqual(str(ctx.exception), "top_p must be within 0..1")

    def test_max_tokens_positive_message_exact(self):
        with self.assertRaises(FieldError) as ctx:
            parse_number("-1", kind="max_tokens")
        self.assertEqual(str(ctx.exception),
                         "max_tokens must be a positive integer")

    def test_retry_errors_message_exact(self):
        with self.assertRaises(FieldError) as ctx:
            parse_retry_errors("a,b")
        self.assertEqual(str(ctx.exception),
                         "retry_on_errors must be a comma-separated "
                         "list of integers")


class ParseNumberTests(unittest.TestCase):
    def test_empty_and_whitespace_mean_absent(self):
        for kind in ("temperature", "top_p", "max_tokens", "generic"):
            with self.subTest(kind=kind):
                self.assertIsNone(parse_number("", kind=kind))
                self.assertIsNone(parse_number("   ", kind=kind))
                self.assertIsNone(parse_number("\t\n", kind=kind))

    def test_temperature_range(self):
        self.assertEqual(parse_number("0", kind="temperature"), 0.0)
        self.assertEqual(parse_number("2", kind="temperature"), 2.0)
        self.assertEqual(parse_number(" 1.25 ", kind="temperature"), 1.25)
        self.assertIsInstance(
            parse_number("1", kind="temperature"), float)

    def test_temperature_out_of_range(self):
        for text in ("3", "-0.5", "2.1", "100"):
            with self.subTest(text=text):
                with self.assertRaises(FieldError):
                    parse_number(text, kind="temperature")

    def test_temperature_non_numeric(self):
        with self.assertRaises(FieldError) as ctx:
            parse_number("warm", kind="temperature")
        self.assertEqual(str(ctx.exception), "Invalid number: 'warm'")

    def test_top_p_range(self):
        self.assertEqual(parse_number("0", kind="top_p"), 0.0)
        self.assertEqual(parse_number("1", kind="top_p"), 1.0)
        self.assertEqual(parse_number("0.5", kind="top_p"), 0.5)

    def test_top_p_out_of_range(self):
        for text in ("-0.1", "1.01", "2"):
            with self.subTest(text=text):
                with self.assertRaises(FieldError):
                    parse_number(text, kind="top_p")

    def test_max_tokens(self):
        self.assertEqual(parse_number("7", kind="max_tokens"), 7)
        self.assertEqual(parse_number(" 128 ", kind="max_tokens"), 128)
        self.assertIsInstance(parse_number("7", kind="max_tokens"), int)

    def test_max_tokens_rejects_non_positive(self):
        for text in ("0", "-1", "-100"):
            with self.subTest(text=text):
                with self.assertRaises(FieldError):
                    parse_number(text, kind="max_tokens")

    def test_max_tokens_rejects_float_text(self):
        with self.assertRaises(FieldError) as ctx:
            parse_number("7.5", kind="max_tokens")
        self.assertEqual(str(ctx.exception), "Invalid number: '7.5'")

    def test_generic_kind_int_first(self):
        self.assertEqual(parse_number(" 7 ", kind="generic"), 7)
        self.assertIsInstance(
            parse_number(" 7 ", kind="generic"), int)
        self.assertEqual(parse_number("-5", kind="cooldown"), -5)

    def test_generic_kind_falls_back_to_float(self):
        self.assertEqual(parse_number("3.14", kind="generic"), 3.14)
        self.assertIsInstance(
            parse_number("3.14", kind="generic"), float)

    def test_generic_kind_rejects_garbage(self):
        with self.assertRaises(FieldError) as ctx:
            parse_number("abc", kind="generic")
        self.assertEqual(str(ctx.exception), "Invalid number: 'abc'")


class ParseRetryErrorsTests(unittest.TestCase):
    def test_empty_means_absent(self):
        self.assertIsNone(parse_retry_errors(""))
        self.assertIsNone(parse_retry_errors("   "))

    def test_simple_list(self):
        self.assertEqual(parse_retry_errors("404,429"), [404, 429])

    def test_spaces_tolerated(self):
        self.assertEqual(parse_retry_errors("404, 429 ,500"),
                         [404, 429, 500])

    def test_single_value(self):
        self.assertEqual(parse_retry_errors("7"), [7])

    def test_empty_segment_rejected(self):
        with self.assertRaises(FieldError):
            parse_retry_errors("1,,2")

    def test_non_integer_rejected(self):
        for text in ("a", "1.5", "404,x", "1 2"):
            with self.subTest(text=text):
                with self.assertRaises(FieldError):
                    parse_retry_errors(text)


class ReasoningCycleTests(unittest.TestCase):
    def test_cycle_values_exact(self):
        self.assertEqual(
            REASONING_CYCLE,
            ("unset", "off", "minimal", "low", "medium", "high",
             "xhigh", "max", "auto"))

    def test_forward_full_cycle(self):
        current = None
        seen = []
        for _ in range(10):
            current = cycle_reasoning(current, 1)
            seen.append(current)
        self.assertEqual(
            seen,
            ["off", "minimal", "low", "medium", "high", "xhigh", "max",
             "auto", None, "off"])

    def test_backward_full_cycle(self):
        current = None
        seen = []
        for _ in range(10):
            current = cycle_reasoning(current, -1)
            seen.append(current)
        self.assertEqual(
            seen,
            ["auto", "max", "xhigh", "high", "medium", "low", "minimal",
             "off", None, "auto"])

    def test_unset_position_maps_to_none_both_ways(self):
        self.assertEqual(cycle_reasoning("unset", 1), "off")   # leaving unset
        self.assertEqual(cycle_reasoning("unset", 1),
                         cycle_reasoning(None, 1))
        self.assertIsNone(cycle_reasoning("off", -1))   # landing on unset
        self.assertIsNone(cycle_reasoning("auto", 1))

    def test_custom_values_cycle_to_unset(self):
        self.assertIsNone(cycle_reasoning("turbo", 1))
        self.assertIsNone(cycle_reasoning("turbo", -1))
        self.assertIsNone(cycle_reasoning("<custom:turbo>", 1))
        self.assertIsNone(cycle_reasoning("<custom:turbo>", -1))

    def test_custom_round_trip(self):
        raw = "gpt-9-turbo"
        wrapped = format_reasoning_custom(raw)
        self.assertEqual(wrapped, "<custom:gpt-9-turbo>")
        self.assertNotIn(wrapped, REASONING_CYCLE)
        # cycling from the wrapped custom lands on unset (None) ...
        self.assertIsNone(cycle_reasoning(wrapped, 1))
        # ... and unset cycles onward into the enum
        self.assertEqual(cycle_reasoning(None, 1), "off")

    def test_format_reasoning_custom(self):
        self.assertEqual(format_reasoning_custom("x"), "<custom:x>")
        self.assertEqual(format_reasoning_custom(""), "<custom:>")


class ClampTests(unittest.TestCase):
    def test_negative_indices_clamp_to_zero(self):
        state = EditorState(route_index=-5, entry_index=-2,
                            field_index=-1)
        state.clamp(3, 2, 4)
        self.assertEqual(
            (state.route_index, state.entry_index, state.field_index),
            (0, 0, 0))

    def test_oversized_indices_clamp_to_last(self):
        state = EditorState(route_index=99, entry_index=99,
                            field_index=99)
        state.clamp(3, 2, 4)
        self.assertEqual(
            (state.route_index, state.entry_index, state.field_index),
            (2, 1, 3))

    def test_zero_counts_clamp_to_zero(self):
        state = EditorState(route_index=3, entry_index=2, field_index=1)
        state.clamp(0, 0, 0)
        self.assertEqual(
            (state.route_index, state.entry_index, state.field_index),
            (0, 0, 0))

    def test_in_range_indices_unchanged(self):
        state = EditorState(route_index=1, entry_index=1, field_index=2)
        state.clamp(3, 2, 4)
        self.assertEqual(
            (state.route_index, state.entry_index, state.field_index),
            (1, 1, 2))


if __name__ == "__main__":
    unittest.main()
