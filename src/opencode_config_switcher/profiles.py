"""Profile store for ``~/.omo/profiles``: CRUD, active marker, drift.

Contracts (binding for Tasks 6/9/11/12):

- Every public function takes a :class:`~opencode_config_switcher.paths.Paths`
  instance first; writes never leave ``paths.profiles_dir`` (drift reads
  ``paths.omo_path`` read-only).
- Names match ``^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`` and never equal the
  reserved marker name ``.active``.  :func:`validate_name` is the single
  gate — traversal/absolute names (``../evil``, ``/tmp/x``) die there,
  before any path math.
- The store WRITES ``{name}.jsonc`` only; it READS ``{name}.jsonc`` first,
  then ``{name}.json`` (hand-authored profiles).  The collision check and
  backup of :func:`write_profile` apply to the ``.jsonc`` target.
- Overwrites keep a SINGLE-generation backup: ``shutil.copy2(target,
  target + ".BAK")`` before writing (the v2 switching.py pattern,
  metadata-preserving).  First-time writes create no ``.BAK``.
- :func:`delete_profile` renames the profile to ``<same>.BAK`` (replacing
  any previous backup) and NEVER touches the ``.active`` marker: clearing
  the marker after deleting the active profile is the caller's job.
- :func:`drift_status` compares every non-``$schema``, non-control key the
  profile defines against the live ``omo.jsonc`` (deep equality: dicts
  order-insensitive, lists order-sensitive).  A ``LoadError`` live document
  is ``"drifted"``, never a crash.
"""

from __future__ import annotations

import copy
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from opencode_config_switcher.jsonc import JsoncError
from opencode_config_switcher.jsonc import dumps as jsonc_dumps
from opencode_config_switcher.jsonc import loads as jsonc_loads
from opencode_config_switcher.omoconfig import (
    CONTROL_KEYS,
    OMO_SCHEMA_URL,
    LoadError,
    OmoDocument,
    load_omo_document,
)
from opencode_config_switcher.paths import Paths

__all__ = [
    "InvalidProfileName",
    "ProfileExistsError",
    "ProfileNotFoundError",
    "ProfileRecord",
    "validate_name",
    "list_profiles",
    "read_profile",
    "write_profile",
    "create_profile",
    "delete_profile",
    "read_active",
    "set_active",
    "clear_active",
    "drift_status",
]

# fullmatch (not .match) so a trailing newline can never satisfy the "$".
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SUFFIXES = (".jsonc", ".json")


class InvalidProfileName(ValueError):
    """Raised for names outside NAME_PATTERN or the reserved ``.active``."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        return f"Invalid profile name: {self.name!r}"


class ProfileExistsError(Exception):
    """Raised when creating a profile whose name is already on disk."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        return f"Profile already exists: {self.name}"


class ProfileNotFoundError(Exception):
    """Raised when a named profile file is absent."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        return f"Profile not found: {self.name}"


@dataclass(frozen=True)
class ProfileRecord:
    """One profile file fully materialized; consumers never reread the disk.

    Invalid profiles carry ``document=None``, the failure reason in
    ``error``, and — whenever the bytes were readable — the full text in
    ``raw_text``.
    """

    name: str
    path: Path
    document: OmoDocument | None
    is_valid: bool
    error: str | None
    size_bytes: int | None
    modified_ns: int | None
    raw_text: str | None


def validate_name(name: str) -> None:
    """Raise :class:`InvalidProfileName` unless ``name`` is a legal stem."""
    if name == ".active" or NAME_PATTERN.fullmatch(name) is None:
        raise InvalidProfileName(name)


def _existing_path(paths: Paths, name: str) -> Path | None:
    """Prefer ``{name}.jsonc``; fall back to ``{name}.json``; else None."""
    for suffix in _SUFFIXES:
        candidate = paths.profiles_dir / (name + suffix)
        if candidate.is_file():
            return candidate
    return None


def _load_record(path: Path, name: str) -> ProfileRecord:
    size_bytes: int | None = None
    modified_ns: int | None = None
    raw_text: str | None = None
    document: OmoDocument | None = None
    error: str | None = None
    try:
        stat = path.stat()
    except OSError as exc:
        error = f"Cannot read {name}: {type(exc).__name__}: {exc}"
    else:
        size_bytes, modified_ns = stat.st_size, stat.st_mtime_ns
        try:
            raw_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raw_text = None
            error = f"Cannot read {name}: {type(exc).__name__}: {exc}"
        else:
            try:
                data = jsonc_loads(raw_text)
            except JsoncError as exc:
                error = str(exc)
            if error is None and not isinstance(data, dict):
                error = (f"{path}: expected object at top level, "
                         f"got {type(data).__name__}")
            if error is None:
                document = OmoDocument(raw=data)
    return ProfileRecord(
        name=name, path=path, document=document, is_valid=error is None,
        error=error, size_bytes=size_bytes, modified_ns=modified_ns,
        raw_text=raw_text,
    )


def list_profiles(paths: Paths) -> list[ProfileRecord]:
    """Every stored profile as a :class:`ProfileRecord`, sorted by name.

    Globs ``*.jsonc`` and ``*.json`` in ``profiles_dir``, skipping names
    that start with ``.`` (the active marker) or end with ``.BAK``.  A
    missing or empty directory yields ``[]``; unreadable or malformed
    files come back as invalid records, never as exceptions.
    """
    records: list[ProfileRecord] = []
    if not paths.profiles_dir.is_dir():
        return records
    for suffix in _SUFFIXES:
        for path in paths.profiles_dir.glob("*" + suffix):
            if path.name.startswith(".") or path.name.endswith(".BAK"):
                continue
            records.append(_load_record(path, path.stem))
    records.sort(key=lambda record: (record.name, record.path.name))
    return records


def read_profile(paths: Paths, name: str) -> ProfileRecord:
    """Load one profile by name; missing files raise ProfileNotFoundError."""
    validate_name(name)
    path = _existing_path(paths, name)
    if path is None:
        raise ProfileNotFoundError(name)
    return _load_record(path, name)


def write_profile(paths: Paths, name: str, document: dict, *,
                  overwrite: bool = False) -> Path:
    """Serialize ``document`` to ``{name}.jsonc`` via ``jsonc.dumps``.

    Existing target without ``overwrite`` raises :class:`ProfileExistsError`;
    with ``overwrite`` the previous bytes go to a single-generation
    ``{name}.jsonc.BAK`` first (first-time writes create no ``.BAK``).
    ``profiles_dir`` is created on demand.
    """
    validate_name(name)
    target = paths.profiles_dir / (name + ".jsonc")
    if target.exists():
        if not overwrite:
            raise ProfileExistsError(name)
        shutil.copy2(target, target.with_name(target.name + ".BAK"))
    paths.profiles_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(jsonc_dumps(document), encoding="utf-8")
    return target


def create_profile(paths: Paths, name: str, from_document: dict | None = None, *,
                   overwrite: bool = False) -> Path:
    """Create a profile: the minimal document, or a deep copy of a source.

    The minimal document is ``{"$schema": OMO_SCHEMA_URL, "[opencode]": {}}``.
    Existence/backup rules are exactly :func:`write_profile`'s.
    """
    if from_document is None:
        document = {"$schema": OMO_SCHEMA_URL, "[opencode]": {}}
    else:
        document = copy.deepcopy(from_document)
    return write_profile(paths, name, document, overwrite=overwrite)


def delete_profile(paths: Paths, name: str) -> Path:
    """Rename the profile file to ``<same>.BAK`` and return the backup path.

    A previous ``.BAK`` is replaced (still one generation).  Missing
    profiles raise :class:`ProfileNotFoundError`.  The ``.active`` marker is
    NEVER followed or renamed, and deleting the ACTIVE profile does NOT
    clear the marker here — coordinating that is the caller's job.
    """
    validate_name(name)
    source = _existing_path(paths, name)
    if source is None:
        raise ProfileNotFoundError(name)
    backup = source.with_name(source.name + ".BAK")
    source.rename(backup)
    return backup


def read_active(paths: Paths) -> str | None:
    """The stripped marker content; ``None`` when missing, empty, or unreadable."""
    try:
        content = paths.active_marker.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return content.strip() or None


def set_active(paths: Paths, name: str) -> None:
    """Write the marker as ``name + "\\n"`` (deliberately unvalidated)."""
    paths.active_marker.parent.mkdir(parents=True, exist_ok=True)
    paths.active_marker.write_text(name + "\n", encoding="utf-8")


def clear_active(paths: Paths) -> None:
    """Remove the marker; a missing marker is not an error."""
    paths.active_marker.unlink(missing_ok=True)


def _deep_equal(left: object, right: object) -> bool:
    """Recursive equality: dicts order-insensitive, lists order-sensitive.

    ``bool`` is never equal to ``int`` (Python's ``True == 1`` quirk would
    otherwise mask a real ``true``/``1`` drift).
    """
    if isinstance(left, dict) and isinstance(right, dict):
        return (left.keys() == right.keys()
                and all(_deep_equal(left[k], right[k]) for k in left))
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _deep_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return left == right


def drift_status(paths: Paths, profile: ProfileRecord) -> str:
    """"managed", "drifted", or "unmanaged" for one profile record.

    ``unmanaged`` — the active marker does not name this profile.
    ``drifted`` — active, but the live ``omo.jsonc`` is missing/unloadable
    (``LoadError``), or any profile-defined non-``$schema``/non-control key
    is absent or not deep-equal to the live value.  An invalid profile
    (``document is None``) also reports ``"drifted"``: an unloadable
    document can never be certified as managed.
    """
    if read_active(paths) != profile.name:
        return "unmanaged"
    if profile.document is None:
        return "drifted"
    live = load_omo_document(paths.omo_path)
    if isinstance(live, LoadError):
        return "drifted"
    for key, profile_value in profile.document.raw.items():
        if key == "$schema" or key in CONTROL_KEYS:
            continue
        if key not in live.raw or not _deep_equal(live.raw[key], profile_value):
            return "drifted"
    return "managed"
