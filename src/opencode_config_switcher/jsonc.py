"""Stdlib-only JSONC parsing and deterministic serialization.

The accepted dialect mirrors upstream oh-my-openagent ``parseJsoncSafe2``:
line comments, block comments, trailing commas in objects and arrays, and a
leading UTF-8 BOM.  Nothing beyond that set is accepted (no JSON5 unquoted
keys, no single quotes, no hex numbers) — those inputs still fail through
``json.loads``.

Design: ``loads`` runs two iterative, length-preserving passes over the text
(comments -> spaces with newlines kept; trailing commas -> a single space) and
then calls ``json.loads`` on the cleaned text.  Because cleaning never changes
text length or newline positions, every position ``json`` reports (including
``JSONDecodeError.lineno``) is valid for the ORIGINAL text — no remapping is
required.  Comment-like sequences inside string literals are never touched
because both passes track string state with escape handling.

``dumps`` intentionally strips comments: profiles are machine-managed, and the
rendered document is the canonical ``// OMO configuration`` header plus strict
JSON.
"""

import json

__all__ = ["JsoncError", "dumps", "loads"]

_HEADER = "// OMO configuration\n"


class JsoncError(ValueError):
    """Raised when JSONC text cannot be parsed.

    ``line`` is the 1-based line in the ORIGINAL text; ``message`` carries no
    position suffix.  ``str(exc)`` renders exactly
    ``Invalid JSONC at line {line}: {message}``.
    """

    def __init__(self, line: int, message: str) -> None:
        super().__init__(line, message)
        self.line = line
        self.message = message

    def __str__(self) -> str:
        return f"Invalid JSONC at line {self.line}: {self.message}"


def _blank_comments(text: str) -> list[str]:
    """Replace comments with spaces, preserving length and newlines.

    Raises ``JsoncError`` for an unclosed block comment, reported at the line
    where the comment starts.
    """
    chars = list(text)
    n = len(chars)
    i = 0
    in_string = False
    while i < n:
        c = chars[i]
        if in_string:
            if c == "\\":
                i += 2  # skip the escaped character
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            i += 1
            continue
        if c == "/" and i + 1 < n and chars[i + 1] == "/":
            while i < n and chars[i] != "\n":
                chars[i] = " "
                i += 1
            continue
        if c == "/" and i + 1 < n and chars[i + 1] == "*":
            line = text.count("\n", 0, i) + 1
            j = i + 2
            while j < n and not (chars[j] == "*" and j + 1 < n and chars[j + 1] == "/"):
                j += 1
            if j >= n:
                raise JsoncError(line, "Unterminated block comment")
            for k in range(i, j + 2):
                if chars[k] != "\n":
                    chars[k] = " "
            i = j + 2
            continue
        i += 1
    return chars


def _blank_trailing_commas(chars: list[str]) -> list[str]:
    """Replace trailing commas (next significant char ``}`` or ``]``) with a space."""
    n = len(chars)
    i = 0
    in_string = False
    while i < n:
        c = chars[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            i += 1
            continue
        if c == ",":
            j = i + 1
            while j < n and chars[j] in " \t\r\n":
                j += 1
            if j < n and chars[j] in "}]":
                chars[i] = " "
            i += 1
            continue
        i += 1
    return chars


def loads(text: str) -> object:
    """Parse JSONC ``text`` into Python objects.

    Accepts ``//`` and ``/* */`` comments, trailing commas, and a leading BOM;
    never treats comment markers inside string literals as comments.  Raises
    ``JsoncError`` on malformed input.
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    cleaned = "".join(_blank_trailing_commas(_blank_comments(text)))
    if not cleaned.strip():
        # no value anywhere in the document; json would point at EOF
        raise JsoncError(1, "Expecting value")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Cleaning is length- and newline-preserving, so exc.lineno already
        # addresses the original text; exc.msg has no position suffix.
        raise JsoncError(exc.lineno, exc.msg) from exc


def dumps(value: object) -> str:
    """Serialize ``value`` deterministically: header comment + strict JSON."""
    return _HEADER + json.dumps(value, indent=2, ensure_ascii=False) + "\n"
