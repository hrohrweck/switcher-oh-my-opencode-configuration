# allow: SIZE_OK — plan-pinned single-module CLI: Tasks 9/10 append the
# use/create/delete/import (and later replace-model) handlers here.
"""CLI dispatch: argparse subcommands, bare selector, stream/exit ownership.

This module is the ONLY place that prints user-facing output or chooses
exit codes (``__main__.py`` just does ``raise SystemExit(main())``).
Exit-code contract: 0 success/quit, 1 runtime failure (empty store,
write errors), 2 usage error / invalid input.
"""

import argparse
import json
import os
import sys
from enum import Enum, auto
from pathlib import Path

from opencode_config_switcher import __version__
from opencode_config_switcher.config import parse_all
from opencode_config_switcher.engine import (
    UseResult,
    UseStatus,
    capture_current,
    use_profile,
)
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.profiles import (
    InvalidProfileName,
    ProfileExistsError,
    ProfileNotFoundError,
    ProfileRecord,
    clear_active,
    create_profile,
    delete_profile,
    drift_status,
    list_profiles,
    read_active,
    read_profile,
    write_profile,
)
from opencode_config_switcher.transform import (
    derive_profile_name,
    discover_legacy,
    transform_legacy,
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
    for command, help_text in (
            ("use", "apply a profile to ~/.omo/omo.jsonc"),
            ("select", "alias of 'use'")):
        picker = sub.add_parser(command, parents=[_VERSION_PARENT],
                                help=help_text)
        picker.add_argument("name", nargs="?", help="profile name")
    create = sub.add_parser("create", parents=[_VERSION_PARENT],
                            help="create a new profile")
    create.add_argument("name", help="new profile name")
    create.add_argument("--from", dest="from_profile", metavar="PROFILE",
                        help="deep-copy this existing profile's document")
    delete = sub.add_parser("delete", parents=[_VERSION_PARENT],
                            help="delete a profile (kept as a .BAK backup)")
    delete.add_argument("name", help="profile name")
    delete.add_argument("--yes", action="store_true",
                        help="delete without prompting")
    importer = sub.add_parser("import", parents=[_VERSION_PARENT],
                              help="import configurations as profiles")
    importer.add_argument("--all-legacy", dest="all_legacy",
                          action="store_true",
                          help="import every legacy configuration file")
    importer.add_argument("--source", action="append", metavar="PATH",
                          help="import this file (repeatable)")
    importer.add_argument("--name", metavar="NAME",
                          help="profile name for a single source")
    importer.add_argument("--force", action="store_true",
                          help="overwrite an existing profile")
    importer.add_argument("--current", nargs="?", const="current",
                          default=None, metavar="NAME",
                          help="import the live omo.jsonc "
                               "(default name 'current')")
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


    print(f"Deleted profile: {args.name} (backup: {backup})")
    if was_active:
        clear_active(paths)
        print("No profile is active now.")
    return 0


# ── use / create / delete (Task 9) ─────────────────────────────────

def _cmd_use(paths: Paths, args: argparse.Namespace) -> int:
    if args.name is None:
        return _run_bare(paths)
    return _report_use_result(use_profile(paths, args.name))


def _report_use_result(result: UseResult) -> int:
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


def _cmd_create(paths: Paths, args: argparse.Namespace) -> int:
    from_document = None
    if args.from_profile is not None:
        try:
            record = read_profile(paths, args.from_profile)
        except InvalidProfileName as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except ProfileNotFoundError:
            print(f"Profile '{args.from_profile}' not found",
                  file=sys.stderr)
            return 2
        if not record.is_valid or record.document is None:
            print(f"Cannot copy invalid profile: {args.from_profile}: "
                  f"{record.error}", file=sys.stderr)
            return 2
        from_document = record.document.raw
    try:
        create_profile(paths, args.name, from_document)
    except InvalidProfileName as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ProfileExistsError:
        print(f"Profile already exists: {args.name}", file=sys.stderr)
        return 2
    print(f"Profile created: {args.name}")
    return 0


def _cmd_delete(paths: Paths, args: argparse.Namespace) -> int:
    try:
        read_profile(paths, args.name)
    except InvalidProfileName as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ProfileNotFoundError:
        print(f"Profile '{args.name}' not found", file=sys.stderr)
        return 2
    if not args.yes:
        if not sys.stdin.isatty():
            print("Refusing to delete without --yes in non-interactive mode",
                  file=sys.stderr)
            return 2
        try:
            answer = input(f"Delete profile '{args.name}'? [y/N]: ")
        except EOFError:
            print("Exiting without changes")
            return 0
        if answer.strip().lower() != "y":
            print("Exiting without changes")
            return 0
    was_active = read_active(paths) == args.name
    try:
        backup = delete_profile(paths, args.name)
    except InvalidProfileName as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ProfileNotFoundError:
        print(f"Profile '{args.name}' not found", file=sys.stderr)
        return 2
    print(f"Deleted profile: {args.name} (backup: {backup})")
    if was_active:
        clear_active(paths)
        print("No profile is active now.")
    return 0


# ── import (Task 9; Task 17 reuses these helpers) ───────────────────

IMPORT_SPECIFY_MESSAGE = ("Specify --all-legacy, --source PATH, "
                          "or --current to import configurations.")


def _cmd_import(paths: Paths, args: argparse.Namespace) -> int:
    if args.current is not None:
        code = _import_current(paths, args.current, force=args.force)
        if code != 0:
            return code
        if not args.all_legacy and not args.source:
            return 0
    elif not args.all_legacy and not args.source:
        print(IMPORT_SPECIFY_MESSAGE, file=sys.stderr)
        return 2

    if args.source:
        candidates = []
        for text in args.source:
            source = Path(text)
            if not source.is_file():
                print(f"Source file not found: {text}", file=sys.stderr)
                return 2
            candidates.append(source)
    else:
        candidates = discover_legacy(paths)

    if args.name is not None and len(candidates) != 1:
        print("--name requires exactly one source", file=sys.stderr)
        return 2
    if not candidates:
        print(f"No legacy configuration files found in {paths.legacy_dir}",
              file=sys.stderr)
        return 1

    if (args.name is None and not args.all_legacy
            and sys.stdin.isatty()):
        code, chosen = _choose_import_files(candidates)
        if code != 0 or not chosen:
            return code
        candidates = chosen

    for source in candidates:
        code = _import_legacy_file(paths, source,
                                   name=args.name, force=args.force)
        if code != 0:
            return code
    return 0


def _import_current(paths: Paths, name: str, *, force: bool) -> int:
    if not paths.omo_path.exists():
        print(f"No configuration found at {paths.omo_path}", file=sys.stderr)
        return 1
    result = capture_current(paths, name, overwrite=force)
    if result.status == UseStatus.APPLIED:
        print(f"Imported profile: {name} (from {paths.omo_path})")
        return 0
    if result.status == UseStatus.BLOCKED:
        print(result.message, file=sys.stderr)
        return 2
    print(result.message, file=sys.stderr)
    return 1


def _choose_import_files(candidates: list[Path]) -> tuple[int, list[Path]]:
    """One-shot chooser: ``(exit code, files to import)``.

    ``(0, files)`` proceeds; ``(0, [])`` is a clean quit (quit and EOF);
    ``(2, [])`` is an invalid selection, already reported to stderr.
    """
    summaries = parse_all(candidates[0], candidates)
    print("Importable configurations:")
    for index, summary in enumerate(summaries, 1):
        marker = "" if summary.is_valid else " [invalid]"
        print(f"  {index}) {summary.file.name}{marker}")
    try:
        selection = input(
            f"Import 1-{len(candidates)}, a for all, or q: ").strip()
    except EOFError:
        print("Exiting without changes")
        return 0, []
    lowered = selection.lower()
    if lowered == "q":
        print("Exiting without changes")
        return 0, []
    if lowered == "a":
        return 0, list(candidates)
    number = None
    try:
        number = int(selection)
    except ValueError:
        pass
    if number is None or not 1 <= number <= len(candidates):
        print(f"Invalid selection: {selection!r}; expected "
              f"1-{len(candidates)}, a, or q", file=sys.stderr)
        return 2, []
    return 0, [candidates[number - 1]]


def _import_legacy_file(paths: Paths, source: Path, *,
                        name: str | None = None,
                        force: bool = False) -> int:
    """Import ONE legacy file end-to-end; the unit Task 17 reuses."""
    summary = parse_all(source, [source])[0]
    if not summary.is_valid:
        print(f"Cannot import invalid configuration: {source.name}: "
              f"{summary.error}", file=sys.stderr)
        return 2
    profile_name = name or derive_profile_name(source)
    document, warnings = transform_legacy(json.loads(summary.file.raw_text))
    try:
        write_profile(paths, profile_name, document, overwrite=force)
    except InvalidProfileName as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ProfileExistsError:
        print(f"Profile already exists: {profile_name}", file=sys.stderr)
        return 2
    print(f"Imported profile: {profile_name} (from {source.name})")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


_HANDLERS = {
    "list": _cmd_list,
    "show": _cmd_show,
    "active": _cmd_active,
    "use": _cmd_use,
    "select": _cmd_use,
    "create": _cmd_create,
    "delete": _cmd_delete,
    "import": _cmd_import,
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
