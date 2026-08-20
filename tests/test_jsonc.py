"""Tests for the stdlib-only JSONC parser and deterministic serializer.

Error-line contract notes (binding for downstream tasks 3/6):
- ``jsonc.JsoncError`` carries ``line`` (1-based, ORIGINAL text) and ``message``;
  ``str(exc)`` renders exactly ``Invalid JSONC at line {line}: {message}``.
- Unclosed block comments are detected by the lexer itself and report the line
  where the comment STARTS.
- All other errors come from ``json.JSONDecodeError`` on the cleaned text; the
  cleaner is length- and newline-preserving, so json's lineno is already the
  ORIGINAL line and json's ``msg`` attribute is used verbatim (it carries no
  position suffix).

BOM note: the BOM case is constructed in-test from ``b"\\xef\\xbb\\xbf" + ...``
rather than shipped as a fixture file because editors and tooling routinely
strip BOMs from saved files.
"""

import ast
import json
import unittest
from pathlib import Path

from opencode_config_switcher.jsonc import (
    JsoncError,
    dumps,
    extract_leading_comments,
    loads,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SRC = Path(__file__).resolve().parents[1] / "src" / "opencode_config_switcher"

ACCEPTED_FIXTURES = (
    "comments_everywhere.jsonc",
    "trailing_commas.jsonc",
    "strings_with_comment_markers.jsonc",
    "replica_omo.jsonc",
)

REPLICA_EXPECTED = {
    "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/omo.schema.json",
    "[opencode]": {
        "model_fallback": True,
        "runtime_fallback": {
            "enabled": True,
            "retry_on_errors": [429, 503, 529],
            "max_fallback_attempts": 3,
            "cooldown_seconds": 60,
        },
        "agents": {
            "agent-one": {
                "model": "provider-a/model-x",
                "reasoning": "max",
                "fallback_models": ["provider-b/model-y", "provider-c/model-z"],
            },
            "agent-two": {
                "model": "provider-a/model-x",
                "reasoning": "high",
                "variant": "high",
                "fallback_models": [
                    {"model": "provider-d/model-w", "reasoning": "xhigh"},
                    {"model": "provider-b/model-y", "reasoning": "max"},
                ],
            },
            "agent-three": {
                "model": "provider-c/model-z",
                "fallback_models": [
                    {"model": "provider-e/model-v", "reasoning": "medium"}
                ],
            },
        },
        "categories": {
            "category-one": {
                "models": [
                    "provider-b/model-y",
                    {"model": "provider-a/model-x"},
                    {"model": "provider-f/model-u", "reasoning": "high"},
                ]
            },
            "category-two": {
                "models": [
                    {"model": "provider-a/model-x", "reasoning": "max"},
                    "provider-c/model-z",
                ]
            },
        },
    },
    "_migrations": [
        "2026-07-opencode-config-unification",
        "2026-08-reasoning-unification",
    ],
}


class LoadsAcceptedDialectTest(unittest.TestCase):
    """loads() accepts exactly the upstream parseJsoncSafe2 dialect."""

    def test_comments_everywhere_fixture_parses(self):
        text = (FIXTURES / "comments_everywhere.jsonc").read_text(encoding="utf-8")
        self.assertEqual(
            loads(text), {"alpha": 1, "beta": [2, 3], "gamma": True}
        )

    def test_trailing_commas_fixture_parses(self):
        text = (FIXTURES / "trailing_commas.jsonc").read_text(encoding="utf-8")
        self.assertEqual(
            loads(text),
            {"top": {"nested": [1, 2, {"deep": ["x"]}]}, "list": [1, [2, 3]]},
        )

    def test_strings_with_comment_markers_survive_verbatim(self):
        text = (FIXTURES / "strings_with_comment_markers.jsonc").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            loads(text),
            {
                "url": "https://example.com/path//double//slash",
                "block_markers": "/* not a comment */",
                "escaped_quote": 'she said "// still not a comment /*" done',
                "backslash_escape": "C:\\path\\to//file",
                "close_marker": "text with */ inside",
                "empty": "",
            },
        )

    def test_replica_omo_fixture_parses_to_exact_document(self):
        text = (FIXTURES / "replica_omo.jsonc").read_text(encoding="utf-8")
        self.assertEqual(loads(text), REPLICA_EXPECTED)

    def test_bom_prefixed_bytes_parse(self):
        raw = b"\xef\xbb\xbf" + b'{"a": [1, 2,] // trailing\n}'
        self.assertEqual(loads(raw.decode("utf-8")), {"a": [1, 2]})


class LoadsMalformedInputTest(unittest.TestCase):
    """loads() raises JsoncError with the exact rendered message and line."""

    def assert_jsonc_error(self, text, expected_line, expected_message):
        with self.assertRaises(JsoncError) as ctx:
            loads(text)
        exc = ctx.exception
        self.assertEqual(exc.line, expected_line)
        self.assertEqual(exc.message, expected_message)
        self.assertEqual(
            str(exc),
            f"Invalid JSONC at line {expected_line}: {expected_message}",
        )

    def test_unclosed_block_comment_reports_comment_start_line(self):
        text = (FIXTURES / "malformed_unclosed_comment.jsonc").read_text(
            encoding="utf-8"
        )
        self.assert_jsonc_error(text, 3, "Unterminated block comment")

    def test_empty_object_comma_reports_json_error_line(self):
        text = (FIXTURES / "malformed_empty_object_comma.jsonc").read_text(
            encoding="utf-8"
        )
        # json reports the error at the '}' on line 3 (the empty property slot
        # is on line 2, but json points at the token that betrayed it).
        self.assert_jsonc_error(
            text, 3, "Expecting property name enclosed in double quotes"
        )

    def test_empty_input_raises_expecting_value_at_line_1(self):
        self.assert_jsonc_error("", 1, "Expecting value")

    def test_whitespace_only_input_raises_expecting_value_at_line_1(self):
        self.assert_jsonc_error("   \n \t \n", 1, "Expecting value")

    def test_single_line_empty_object_comma(self):
        self.assert_jsonc_error(
            '{"a":1,,}',
            1,
            "Expecting property name enclosed in double quotes",
        )

    def test_array_empty_slot_comma(self):
        self.assert_jsonc_error("[1,,2]", 1, "Expecting value")

    def test_unterminated_string_detected_via_json(self):
        # Unterminated strings are caught by json.JSONDecodeError on the
        # cleaned text (the lexer just reaches EOF in string state); the
        # reported line is the string's START line.
        self.assert_jsonc_error('{"a": "unterminated', 1, "Unterminated string starting at")


class LineRemapTest(unittest.TestCase):
    """Error lines after comments remap to ORIGINAL text lines."""

    def test_error_after_multiline_block_comment_maps_to_original_line(self):
        text = (
            "{\n"
            '  "a": 1,\n'
            "  /* comment\n"
            "     spanning\n"
            "     lines */\n"
            '  "b": oops\n'
            "}\n"
        )
        with self.assertRaises(JsoncError) as ctx:
            loads(text)
        # original lines 3-5 are the block comment; "oops" starts on line 6
        self.assertEqual(ctx.exception.line, 6)
        self.assertEqual(ctx.exception.message, "Expecting value")

    def test_error_line_correct_under_crlf(self):
        text = '{\r\n  "a": bad\r\n}\r\n'
        with self.assertRaises(JsoncError) as ctx:
            loads(text)
        self.assertEqual(ctx.exception.line, 2)
        self.assertEqual(str(ctx.exception), "Invalid JSONC at line 2: Expecting value")

    def test_crlf_document_parses(self):
        text = '{\r\n  "a": [1, 2,], // c\r\n}\r\n'
        self.assertEqual(loads(text), {"a": [1, 2]})


class LoadsAdversarialProbesTest(unittest.TestCase):
    def test_double_slash_inside_string_is_not_a_comment(self):
        self.assertEqual(
            loads('{"u": "http://x//y"}'), {"u": "http://x//y"}
        )

    def test_trailing_comma_immediately_before_comment_then_bracket(self):
        self.assertEqual(loads("[1, /* c */]"), [1])

    def test_deeply_nested_trailing_commas_accepted(self):
        depth = 60
        text = "[" * depth + "1" + ",]" * depth
        value = loads(text)
        nested = value
        for _ in range(depth):
            self.assertIsInstance(nested, list)
            self.assertEqual(len(nested), 1)
            nested = nested[0]
        self.assertEqual(nested, 1)

    def test_line_comment_at_eof_without_newline(self):
        self.assertEqual(loads('{"a": 1} // eof'), {"a": 1})


class DumpsContractTest(unittest.TestCase):
    def test_dumps_output_is_byte_exact(self):
        value = {"b": 2, "a": {"ä": "é", "list": [1, True, None]}}
        expected = (
            "// OMO configuration\n"
            + json.dumps(value, indent=2, ensure_ascii=False)
            + "\n"
        )
        self.assertEqual(dumps(value), expected)
        self.assertIn('"ä"', dumps(value))  # ensure_ascii=False proof
        self.assertTrue(dumps(value).endswith("\n"))

    def test_dumps_preserves_insertion_order(self):
        text = (FIXTURES / "replica_omo.jsonc").read_text(encoding="utf-8")
        dumped = dumps(loads(text))
        self.assertLess(dumped.index("$schema"), dumped.index("[opencode]"))
        self.assertLess(dumped.index("[opencode]"), dumped.index("_migrations"))

    def test_dumps_is_idempotent(self):
        value = {"z": 1, "a": ["ünïcode", {"k": 2}], "m": True}
        self.assertEqual(dumps(loads(dumps(value))), dumps(value))


class RoundTripInvariantTest(unittest.TestCase):
    def test_roundtrip_and_idempotence_over_all_accepted_fixtures(self):
        for name in ACCEPTED_FIXTURES:
            with self.subTest(fixture=name):
                value = loads((FIXTURES / name).read_text(encoding="utf-8"))
                self.assertEqual(loads(dumps(value)), value)
                self.assertEqual(dumps(loads(dumps(value))), dumps(value))


class ExtractLeadingCommentsTest(unittest.TestCase):
    """extract_leading_comments() returns the contiguous leading // block."""

    def test_collects_comment_block_dropping_trailing_blank(self):
        self.assertEqual(
            extract_leading_comments("// a\n// b\n\n{\n}"),
            ["// a", "// b"],
        )

    def test_stops_at_first_content_line(self):
        self.assertEqual(extract_leading_comments('{"a": 1}\n// later\n'), [])

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(extract_leading_comments(""), [])

    def test_blank_lines_inside_block_are_kept_verbatim(self):
        self.assertEqual(
            extract_leading_comments("// a\n\n// b\n{\n}"),
            ["// a", "", "// b"],
        )

    def test_trailing_whitespace_stripped_from_each_line(self):
        self.assertEqual(
            extract_leading_comments("// a   \n// b\t\n{\n}"),
            ["// a", "// b"],
        )

    def test_only_blank_lines_returns_empty_list(self):
        self.assertEqual(extract_leading_comments("\n  \n\t\n"), [])

    def test_indented_comment_counts_as_comment_kept_verbatim(self):
        self.assertEqual(
            extract_leading_comments("  // indented\n{\n}"),
            ["  // indented"],
        )


class DumpsCommentsTest(unittest.TestCase):
    """dumps(value, comments=...) preserves a leading block on demand."""

    def test_no_comments_output_is_byte_identical_to_legacy(self):
        value = {"b": 2, "a": {"ä": "é", "list": [1, True, None]}}
        legacy = (
            "// OMO configuration\n"
            + json.dumps(value, indent=2, ensure_ascii=False)
            + "\n"
        )
        self.assertEqual(dumps(value), legacy)
        self.assertEqual(dumps(value, comments=None), legacy)
        self.assertEqual(dumps(value, comments=[]), legacy)

    def test_comments_emitted_first_then_blank_then_json(self):
        result = dumps({"a": 1}, ["// a"])
        self.assertTrue(result.startswith("// a\n\n{"))

    def test_canonical_header_not_duplicated_when_comments_given(self):
        result = dumps({"a": 1}, ["// OMO configuration", "// user note"])
        self.assertEqual(result.count("// OMO configuration"), 1)
        self.assertTrue(
            result.startswith("// OMO configuration\n// user note\n\n{"))

    def test_bare_comment_lines_get_slash_prefix(self):
        result = dumps({"a": 1}, ["plain note"])
        self.assertTrue(result.startswith("// plain note\n\n{"))

    def test_document_still_round_trips_through_loads(self):
        value = {"x": [1, 2], "y": {"z": "w"}}
        self.assertEqual(loads(dumps(value, ["// note"])), value)


class StdlibOnlyTest(unittest.TestCase):
    def test_module_imports_stdlib_modules_only(self):
        tree = ast.parse((SRC / "jsonc.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertEqual(node.level, 0)
                imported.add(node.module.split(".")[0])
        self.assertLessEqual(imported, {"json", "re", "typing"})


if __name__ == "__main__":
    unittest.main()
