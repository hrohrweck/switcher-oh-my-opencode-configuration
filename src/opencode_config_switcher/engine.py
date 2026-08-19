"""Render/use engine: apply a stored profile to ``~/.omo/omo.jsonc``.

Contracts (binding for Tasks 7/8/9/12):

- Results are typed, frozen, and never raise for anticipated user-facing
  failures — the CLI/TUI own streams and exit codes, this module owns
  decisions.  Exact messages:
    - ``Profile '{name}' not found``                      (BLOCKED, missing)
    - ``Cannot apply invalid profile: {n}: {error}``      (BLOCKED, invalid)
    - ``No change: profile '{name}' is already active``   (NOOP)
    - ``Failed to create backup: {Type}: {exc}``          (FAILED)
    - ``Failed to render configuration: {Type}: {exc}``   (FAILED)
    - ``Profile applied: {name}``                         (APPLIED)
    - ``Profile captured: {name}``                        (APPLIED, capture)
    - ``Cannot import invalid configuration: {error}``    (BLOCKED, capture)
    - ``Profile already exists: {name}``                  (BLOCKED, capture)
  ``InvalidProfileName`` from the store is reported as its ``str(exc)``
  (``Invalid profile name: {name!r}``).

- ``use_profile`` evaluation order: read profile (not-found / bad name →
  BLOCKED) → invalid record BLOCKED (zero writes) → active marker matches
  AND ``drift_status == "managed"`` → NOOP with ZERO writes (not even a
  touch) → otherwise backup-if-exists, render write, ``set_active``.

- A ``LoadError`` live document (corrupt or unreadable ``omo.jsonc``) is
  treated as ABSENT for rendering (fresh start) but still byte-preserved
  into the single-generation ``.BAK`` — a corrupt live file never blocks.

- The backup runs BEFORE the render write: a failed backup aborts with no
  write to ``omo.jsonc``; a failed write leaves the ``.BAK`` on disk and
  the live bytes untouched.  Only ``shutil.copy2`` + ``write_text`` — no
  ``os.replace`` atomicity, no multi-generation backups.

- ``capture_current`` imports the live document as a profile through the
  store and NEVER touches ``omo.jsonc`` or the ``.active`` marker.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from opencode_config_switcher.jsonc import dumps as jsonc_dumps
from opencode_config_switcher.omoconfig import (
    LoadError,
    OmoDocument,
    load_omo_document,
    replace_sections,
)
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.profiles import (
    InvalidProfileName,
    ProfileExistsError,
    ProfileNotFoundError,
    drift_status,
    read_active,
    read_profile,
    set_active,
    write_profile,
)

__all__ = [
    "UseStatus",
    "UseResult",
    "render_document",
    "use_profile",
    "capture_current",
]


class UseStatus(str, Enum):
    """Outcome of a use/capture operation (v2 switching.py style)."""

    APPLIED = "APPLIED"
    NOOP = "NOOP"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class UseResult:
    """Immutable result of one engine operation."""

    status: UseStatus
    profile: str
    omo_path: Path
    backup: Path
    message: str
    error: str | None = None


def render_document(record_document: OmoDocument,
                    live: OmoDocument | LoadError | None) -> dict:
    """Layer ``record_document`` over ``live``; pure, no I/O.

    A ``None`` or :class:`LoadError` live document starts from an empty
    document (fresh render — corrupt bytes must never be merged nor block);
    otherwise this is exactly :func:`replace_sections(live, record)`.
    """
    if live is None or isinstance(live, LoadError):
        live = OmoDocument(raw={})
    return replace_sections(live, record_document)


def use_profile(paths: Paths, name: str) -> UseResult:
    """Render ``name``'s profile into ``~/.omo/omo.jsonc``; see module doc."""

    def result(status: UseStatus, message: str,
               error: str | None = None) -> UseResult:
        return UseResult(
            status=status, profile=name, omo_path=paths.omo_path,
            backup=paths.omo_backup, message=message, error=error,
        )

    try:
        record = read_profile(paths, name)
    except InvalidProfileName as exc:
        return result(UseStatus.BLOCKED, str(exc))
    except ProfileNotFoundError:
        return result(UseStatus.BLOCKED, f"Profile '{name}' not found")

    if not record.is_valid:
        return result(
            UseStatus.BLOCKED,
            f"Cannot apply invalid profile: {name}: {record.error}",
            error=record.error,
        )

    if (read_active(paths) == name
            and drift_status(paths, record) == "managed"):
        return result(
            UseStatus.NOOP,
            f"No change: profile '{name}' is already active",
        )

    merged = render_document(
        record.document, load_omo_document(paths.omo_path))

    if paths.omo_path.exists():
        try:
            shutil.copy2(paths.omo_path, paths.omo_backup)
        except Exception as exc:
            return result(
                UseStatus.FAILED,
                f"Failed to create backup: {type(exc).__name__}: {exc}",
                error=str(exc),
            )

    try:
        paths.omo_path.write_text(jsonc_dumps(merged), encoding="utf-8")
    except Exception as exc:
        return result(
            UseStatus.FAILED,
            f"Failed to render configuration: {type(exc).__name__}: {exc}",
            error=str(exc),
        )

    set_active(paths, name)
    return result(UseStatus.APPLIED, f"Profile applied: {name}")


def capture_current(paths: Paths, name: str, *,
                    overwrite: bool = True) -> UseResult:
    """Save the live ``omo.jsonc`` as profile ``name``; see module doc.

    The live document is NEVER modified and the ``.active`` marker is
    never read or written here.
    """

    def result(status: UseStatus, message: str,
               error: str | None = None) -> UseResult:
        return UseResult(
            status=status, profile=name, omo_path=paths.omo_path,
            backup=paths.omo_backup, message=message, error=error,
        )

    live = load_omo_document(paths.omo_path)
    if isinstance(live, LoadError):
        return result(
            UseStatus.BLOCKED,
            f"Cannot import invalid configuration: {live.message}",
            error=live.message,
        )

    try:
        write_profile(paths, name, live.raw, overwrite=overwrite)
    except InvalidProfileName as exc:
        return result(UseStatus.BLOCKED, str(exc))
    except ProfileExistsError:
        return result(UseStatus.BLOCKED, f"Profile already exists: {name}")

    return result(UseStatus.APPLIED, f"Profile captured: {name}")
