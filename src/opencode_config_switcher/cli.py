"""CLI dispatch: version, preflight, TTY/plain-mode routing, and exit codes."""

import os
import sys
from pathlib import Path

from opencode_config_switcher import __version__
from opencode_config_switcher.config import (
    discover_configs, parse_all, BACKUP_PATH, ACTIVE_PATH)
from opencode_config_switcher.switching import (
    ApplyStatus, apply_config)


def main() -> int:
    """Entry point: returns an integer for SystemExit."""
    if len(sys.argv) > 1 and sys.argv[1] == "--version":
        print(__version__)
        return 0

    # ── preflight ────────────────────────────────────────────────
    try:
        active, candidates = discover_configs()
    except FileNotFoundError as exc:
        print(f"Configuration directory not found: {Path.home() / '.config' / 'opencode'}",
              file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    summaries = parse_all(active, candidates)

    # ── TTY dispatch ─────────────────────────────────────────────
    stdin_tty = sys.stdin.isatty()
    stdout_tty = sys.stdout.isatty()
    term = os.environ.get("TERM", "")
    use_curses = stdin_tty and stdout_tty and term not in ("", "dumb")

    if use_curses:
        return _run_tui(summaries, active)
    else:
        return _run_plain(summaries, active)


# ── TUI path ──────────────────────────────────────────────────────

def _run_tui(summaries, active) -> int:
    from opencode_config_switcher.switching import apply_config as _apply
    from opencode_config_switcher.tui import run_tui, TuiOutcome

    def apply_fn(source, **kw):
        return _apply(source, active=active, **kw)

    try:
        result = run_tui(summaries, apply_fn)
    except Exception as exc:
        print(f"TUI error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if result.outcome == TuiOutcome.QUIT:
        print("Exiting without changes")
        return 0
    elif result.outcome == TuiOutcome.APPLIED:
        ar = result.apply_result
        if ar:
            print(ar.message)
            print(f"Backup saved to: {ar.backup}")
        return 0
    elif result.outcome == TuiOutcome.NOOP:
        ar = result.apply_result
        if ar:
            print(ar.message)
        return 0
    elif result.outcome == TuiOutcome.TERMINATED:
        sig_name = {1: "SIGHUP", 15: "SIGTERM"}.get(
            result.signal_number, f"signal {result.signal_number}")
        print(f"Interrupted by signal {sig_name}", file=sys.stderr)
        return 128 + (result.signal_number or 0)
    else:  # FATAL
        print(f"TUI error: {result.error_type}: {result.error_message}",
              file=sys.stderr)
        return 1


# ── plain (non-TTY) path ──────────────────────────────────────────

def _run_plain(summaries, active) -> int:
    print("Available configurations:")
    for i, s in enumerate(summaries, 1):
        markers = ""
        if s.file.is_current:
            markers += " [current]"
        if not s.is_valid:
            markers += " [invalid]"
        print(f"  {i}) {s.file.name}{markers}")

    try:
        user_input = input(f"Select 1-{len(summaries)} or q: ").strip()
    except (EOFError, KeyboardInterrupt):
        return 0

    if user_input.lower() == "q":
        print("Exiting without changes")
        return 0

    try:
        num = int(user_input)
    except ValueError:
        print(f"Invalid selection: {user_input!r}; "
              f"expected 1-{len(summaries)} or q", file=sys.stderr)
        return 2

    if num < 1 or num > len(summaries):
        print(f"Invalid selection: {user_input!r}; "
              f"expected 1-{len(summaries)} or q", file=sys.stderr)
        return 2

    selected = summaries[num - 1]
    if selected.file.is_current:
        # No-op even if invalid JSON
        print(f"No change: {selected.file.name} is already active")
        return 0

    if not selected.is_valid:
        print(f"Cannot apply invalid configuration: "
              f"{selected.file.name}: {selected.error}", file=sys.stderr)
        return 2

    result = apply_config(
        selected.file.path, active=active,
        is_valid=selected.is_valid)

    if result.status == ApplyStatus.APPLIED:
        print(result.message)
        print(f"Backup saved to: {result.backup}")
        return 0
    elif result.status == ApplyStatus.NOOP:
        print(result.message)
        return 0
    else:
        print(result.message, file=sys.stderr)
        return 1
