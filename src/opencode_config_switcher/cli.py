"""CLI dispatch: argparse subcommands, bare selector, stream/exit ownership.

This module is the ONLY place that prints user-facing output or chooses
exit codes (``__main__.py`` just does ``raise SystemExit(main())``).
Exit-code contract: 0 success/quit, 1 runtime failure (empty store,
write errors), 2 usage error / invalid input.
"""

import argparse
import os
import sys
from enum import Enum, auto
from pathlib import Path

from opencode_config_switcher import __version__
from opencode_config_switcher.engine import UseStatus, use_profile
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.profiles import (
    InvalidProfileName,
    ProfileNotFoundError,
    ProfileRecord,
    drift_status,
    list_profiles,
    read_active,
    read_profile,
)

EMPTY_STORE_HINT = ("Run 'opencode-config-switcher import --all-legacy' "
                    "or 'import --current' to get started.")


class _Parser(argparse.ArgumentParser):
    """Usage errors exit 2 — argparse's default is pinned here on purpose."""

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            sys.stderr.write(message)
        raise SystemExit(status)

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


_VERSION_PARENT = argparse.ArgumentParser(add_help=False)
_VERSION_PARENT.add_argument(
    "--version", action="version", version=__version__)


def _build_parser() -> _Parser:
    """The v3 subcommand surface; Tasks 9/10 register more subparsers here."""
    parser = _Parser(
        prog="opencode-config-switcher",
        description="Switch oh-my-opencode profiles rendered to "
                    "~/.omo/omo.jsonc.",
        parents=[_VERSION_PARENT],
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.add_parser("list", parents=[_VERSION_PARENT],
                   help="list stored profiles")
    show = sub.add_parser("show", parents=[_VERSION_PARENT],
                          help="print one profile")
    show.add_argument("name", help="profile name")
    show.add_argument("--raw", action="store_true",
                      help="print the cached profile bytes verbatim")
    sub.add_parser("active", parents=[_VERSION_PARENT],
                   help="print the active-profile state")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point: returns the process exit code."""
    args = _build_parser().parse_args(argv)
    paths = Paths.build(Path.home())
    if args.command is None:
        return _run_bare(paths)
    return _HANDLERS[args.command](paths, args)


# ── subcommands ────────────────────────────────────────────────────

def _markers(paths: Paths, active: str | None,
             record: ProfileRecord) -> str:
    """` [active]` / ` [custom]` / ` [invalid]` suffixes, in that order."""
    markers = ""
    if active == record.name:
        managed = drift_status(paths, record) == "managed"
        markers += " [active]" if managed else " [custom]"
    if not record.is_valid:
        markers += " [invalid]"
    return markers


def _cmd_list(paths: Paths, args: argparse.Namespace) -> int:
    records = list_profiles(paths)
    if not records:
        _report_empty_store(paths)
        return 1
    active = read_active(paths)
    for record in records:
        print(f"  {record.name}{_markers(paths, active, record)}")
    return 0


def _cmd_show(paths: Paths, args: argparse.Namespace) -> int:
    try:
        record = read_profile(paths, args.name)
    except InvalidProfileName as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ProfileNotFoundError:
        print(f"Profile '{args.name}' not found", file=sys.stderr)
        return 2
    if args.raw:
        sys.stdout.write(record.raw_text or "")
        return 0
    if not record.is_valid:
        print(f"INVALID: {record.error}")
    from opencode_config_switcher.tui_data import (
        build_summaries, format_details)
    for line in format_details(build_summaries(paths, [record])[0], 80):
        print(line)
    return 0


def _cmd_active(paths: Paths, args: argparse.Namespace) -> int:
    name = read_active(paths)
    if name is not None:
        try:
            record = read_profile(paths, name)
        except (InvalidProfileName, ProfileNotFoundError):
            pass
        else:
            if drift_status(paths, record) == "managed":
                print(f"Active profile: {name}")
            else:
                print(f"custom (configuration drifted from '{name}')")
            return 0
    print("custom (no profile active)")
    return 0


_HANDLERS = {
    "list": _cmd_list,
    "show": _cmd_show,
    "active": _cmd_active,
}


# ── bare invocation (no subcommand) ────────────────────────────────

def _run_bare(paths: Paths) -> int:
    records = list_profiles(paths)
    if not records:
        _report_empty_store(paths)
        return 1
    if _interactive_terminal():
        return _run_bare_tty(paths, records)
    return _plain_selector(paths, records)


def _report_empty_store(paths: Paths) -> None:
    print(f"No profiles found in {paths.profiles_dir}", file=sys.stderr)
    print(EMPTY_STORE_HINT, file=sys.stderr)


def _interactive_terminal() -> bool:
    term = os.environ.get("TERM", "")
    return (sys.stdin.isatty() and sys.stdout.isatty()
            and term not in ("", "dumb"))


def _plain_selector(paths: Paths, records: list[ProfileRecord]) -> int:
    print("Available profiles:")
    active = read_active(paths)
    for index, record in enumerate(records, 1):
        print(f"  {index}) {record.name}{_markers(paths, active, record)}")

    try:
        selection = input(f"Select 1-{len(records)} or q: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("Exiting without changes")
        return 0
    if selection.lower() == "q":
        print("Exiting without changes")
        return 0

    number = None
    try:
        number = int(selection)
    except ValueError:
        pass
    if number is None or not 1 <= number <= len(records):
        print(f"Invalid selection: {selection!r}; "
              f"expected 1-{len(records)} or q", file=sys.stderr)
        return 2

    result = use_profile(paths, records[number - 1].name)
    if result.status == UseStatus.APPLIED:
        print(result.message)
        print(f"Backup saved to: {result.backup}")
        return 0
    if result.status == UseStatus.NOOP:
        print(result.message)
        return 0
    if result.status == UseStatus.BLOCKED:
        print(result.message, file=sys.stderr)
        return 2
    print(result.message, file=sys.stderr)
    return 1


# ── interactive (TTY) selector adapter ─────────────────────────────

class TuiHandleOutcome(Enum):
    """Outcome of the interactive selector; Task 12 extends this."""

    QUIT = auto()


def run_tui_selector(summaries, paths: Paths) -> TuiHandleOutcome:
    """Adapter seam over the curses selector; stubbed until Task 12.

    Task 12 replaces this body with the real TUI (importing curses INSIDE
    this function — the stub never touches curses).
    """
    return TuiHandleOutcome.QUIT


def _run_bare_tty(paths: Paths, records: list[ProfileRecord]) -> int:
    from opencode_config_switcher.tui_data import build_summaries
    outcome = run_tui_selector(build_summaries(paths, records), paths)
    if outcome is TuiHandleOutcome.QUIT:
        print("Exiting without changes")
        return 0
    print(f"Unexpected selector outcome: {outcome}", file=sys.stderr)
    return 1
