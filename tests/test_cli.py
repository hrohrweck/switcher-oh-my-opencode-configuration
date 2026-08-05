"""Tests for CLI dispatch, plain mode, and version output."""

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opencode_config_switcher import __version__
from opencode_config_switcher.cli import main, _run_plain
from opencode_config_switcher.config import (
    FileSummary, ConfigSummary, RuntimeFallbackSummary,
    )
from opencode_config_switcher.switching import ApplyResult, ApplyStatus


def _cfg(name, is_current=False, is_valid=True, error=None):
    return ConfigSummary(
        file=FileSummary(path=Path(f"/tmp/{name}"), name=name,
                         size_bytes=100, modified_ns=1,
                         is_current=is_current, raw_text="{}"),
        is_valid=is_valid, error=error, model_fallback=True,
        runtime_fallback=RuntimeFallbackSummary(enabled=True),
    )


class VersionTests(unittest.TestCase):
    def test_version_output(self):
        with mock.patch("sys.argv", ["prog", "--version"]):
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                exit_code = main()
                self.assertEqual(exit_code, 0)
                self.assertEqual(out.getvalue().strip(), __version__)


class PlainModeTests(unittest.TestCase):
    def test_plain_quit(self):
        cfgs = [_cfg("a.json"), _cfg("b.json", is_current=True)]
        with mock.patch("builtins.input", return_value="q"):
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                exit_code = _run_plain(cfgs, Path("/fake/active"))
        self.assertEqual(exit_code, 0)
        self.assertIn("Available", out.getvalue())

    def test_plain_select_valid(self):
        cfgs = [_cfg("a.json"), _cfg("b.json")]
        with mock.patch("builtins.input", return_value="2"):
            import opencode_config_switcher.cli as cli
            with mock.patch.object(cli, "apply_config") as ma:
                ma.return_value = ApplyResult(
                    ApplyStatus.APPLIED, Path("s"), Path("a"),
                    Path("b"), "Configuration applied: b.json")
                exit_code = _run_plain(cfgs, Path("/fake/active"))
        self.assertEqual(exit_code, 0)

    def test_plain_select_invalid(self):
        cfgs = [_cfg("a.json"), _cfg("bad.json", is_valid=False,
                                      error="parse error")]
        with mock.patch("builtins.input", return_value="2"):
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                exit_code = _run_plain(cfgs, Path("/fake/active"))
        self.assertEqual(exit_code, 2)
        self.assertIn("Cannot apply", err.getvalue())

    def test_plain_out_of_range(self):
        cfgs = [_cfg("a.json")]
        with mock.patch("builtins.input", return_value="99"):
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                exit_code = _run_plain(cfgs, Path("/fake/active"))
        self.assertEqual(exit_code, 2)

    def test_plain_non_numeric(self):
        cfgs = [_cfg("a.json")]
        with mock.patch("builtins.input", return_value="abc"):
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                exit_code = _run_plain(cfgs, Path("/fake/active"))
        self.assertEqual(exit_code, 2)

    def test_plain_eof(self):
        cfgs = [_cfg("a.json")]
        with mock.patch("builtins.input", side_effect=EOFError):
            exit_code = _run_plain(cfgs, Path("/fake/active"))
        self.assertEqual(exit_code, 0)

    def test_plain_noop_current(self):
        cfgs = [_cfg("a.json", is_current=True)]
        with mock.patch("builtins.input", return_value="1"):
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                exit_code = _run_plain(cfgs, Path("/fake/active"))
        self.assertEqual(exit_code, 0)
        self.assertIn("already active", out.getvalue())


class TTYDispatchTests(unittest.TestCase):
    def test_version_bypasses_tty_check(self):
        with mock.patch("sys.argv", ["prog", "--version"]):
            exit_code = main()
            self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
