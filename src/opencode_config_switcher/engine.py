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

Model replacement (Task 7; binding for Tasks 10/16):

- ``UseStatus`` gains ``NO_MATCHES`` and ``PREVIEW`` (dry-run success,
  zero writes) alongside the four use/capture values above.
- ``replace_model(document, old, new)`` is pure: it returns a NEW
  deep-copied document (the input is never mutated) plus a tuple of
  ``ReplacementHit``s.  Hit ``field`` grammar: ``model`` /
  ``fallback_models[{i}]`` / ``models[{i}]`` / ``catalog:{name}``;
  ``section`` is the harness block key or ``"<root>"``; ``route`` is the
  agent/category name, ``""`` for catalog hits.  Surfaces are walked
  ``<root>`` first, then every HARNESS_BLOCKS block present; inside a
  section: agents (primary, then fallback chain) -> categories ->
  ``models`` catalog.  Exact string equality only; malformed containers
  are skipped silently.  A bare-string ``fallback_models``/``models``
  chain unwraps to its single entry (hit field ``{name}[0]``), mirroring
  omoconfig normalization.
- ``replace_model_in_profile`` reuses the BLOCKED messages above and
  adds: ``No matches for model '{old}' in profile '{name}'``
  (NO_MATCHES — zero writes, checked before the dry-run branch),
  ``Would replace {n} model reference(s) in profile '{name}'``
  (PREVIEW — zero writes), ``Replaced {n} model reference(s) in profile
  '{name}'`` (APPLIED; write errors are FAILED with ``str(exc)``).
  When the profile is active the engine ALWAYS re-renders through
  ``use_profile``: an APPLIED re-render appends
  ``; re-rendered active configuration``; NOOP/BLOCKED append nothing;
  a FAILED re-render appends ``; re-render failed: {error}`` while the
  overall status STAYS APPLIED — the profile write itself succeeded
  and must not be reported as failed.
- ``replace_model_all`` iterates ``list_profiles`` order; invalid
  profiles surface as their BLOCKED result (skip-and-report, never
  raises).

Profile migration — legacy ``fallback_models`` → canonical ``models``
chains (reuses the Task 1 transform helpers):

- ``MigrateResult`` mirrors ``ReplaceResult`` with ``routes`` (legacy
  route count from :func:`transform.migrate_document`) in place of
  ``hits`` plus a ``rerendered`` flag.  Exact messages:
  - ``Profile '{name}' not found``                      (BLOCKED, missing)
  - ``Invalid profile name: {name!r}``                  (BLOCKED, bad name)
  - ``Cannot migrate invalid profile: {n}: {error}``    (BLOCKED, invalid)
  - ``No migration needed for profile '{name}'``        (NO_MATCHES, zero
    writes — the profile is already canonical)
  - ``Would migrate profile '{name}' ({n} route(s))``   (PREVIEW, dry-run,
    zero writes)
  - ``Migrated profile '{name}' ({n} route(s))``        (APPLIED; write
    errors are FAILED with ``str(exc)``)
- Applying through :func:`profiles.write_profile` inherits the
  ``.BAK`` backup and leading-comment preservation; a zero-route
  profile is NEVER rewritten.  When the migrated profile is ACTIVE the
  engine ALWAYS re-renders through ``use_profile`` with the same
  suffix rules as ``replace_model_in_profile`` (``;
  re-rendered active configuration`` sets ``rerendered=True``; a
  FAILED re-render appends ``; re-render failed: {error}`` while the
  status stays APPLIED; NOOP appends nothing).
- ``migrate_all`` iterates ``list_profiles`` order; invalid profiles
  surface as their BLOCKED result (skip-and-report, never raises).
"""

from __future__ import annotations

import copy
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from opencode_config_switcher.jsonc import dumps as jsonc_dumps
from opencode_config_switcher.omoconfig import (
    HARNESS_BLOCKS,
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
    list_profiles,
    read_active,
    read_profile,
    set_active,
    write_profile,
)
from opencode_config_switcher.transform import migrate_document

__all__ = [
    "UseStatus",
    "UseResult",
    "ReplacementHit",
    "ReplaceResult",
    "MigrateResult",
    "render_document",
    "use_profile",
    "capture_current",
    "replace_model",
    "replace_model_in_profile",
    "replace_model_all",
    "migrate_profile",
    "migrate_all",
]


class UseStatus(str, Enum):
    """Outcome of a use/capture operation (v2 switching.py style)."""

    APPLIED = "APPLIED"
    NOOP = "NOOP"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    NO_MATCHES = "NO_MATCHES"  # model replace: zero matching surfaces
    PREVIEW = "PREVIEW"        # model replace: dry-run preview success


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


# Model replacement (Task 7; binding for Tasks 10/16).
# allow: SIZE_OK — plan-pinned single-module engine: the replace service
# appends here (a split would add a source file the plan forbids).


@dataclass(frozen=True)
class ReplacementHit:
    """One exact-match model replacement inside a profile document.

    ``section`` is the harness block key (``"[opencode]"``) or
    ``"<root>"`` for the harness-neutral document root; ``route`` is the
    agent/category name and ``""`` for ``models``-catalog hits; ``field``
    follows the grammar ``model`` / ``fallback_models[{i}]`` /
    ``models[{i}]`` / ``catalog:{name}``.
    """

    section: str
    route: str
    field: str
    old: str
    new: str


@dataclass(frozen=True)
class ReplaceResult:
    """Immutable result of one model-replacement operation."""

    status: UseStatus
    profile: str
    hits: tuple[ReplacementHit, ...]
    message: str
    error: str | None = None


def replace_model(document: dict, old: str,
                  new: str) -> tuple[dict, tuple[ReplacementHit, ...]]:
    """Exact-match replace ``old`` -> ``new``; see module docstring.

    Returns a NEW deep-copied document (the input is never mutated) plus
    every hit, walked ``<root>`` first then HARNESS_BLOCKS order; inside
    a section: agents (primary, then fallback chain) -> categories ->
    ``models`` catalog.  Malformed containers are skipped silently.
    """

    changed = copy.deepcopy(document)
    hits: list[ReplacementHit] = []

    def hit(section: str, route: str, field: str) -> None:
        hits.append(ReplacementHit(section, route, field, old, new))

    def replace_section(section: dict, label: str) -> None:
        def replace_entries(block: dict, key: str, route: str) -> None:
            """One route's ordered chain (``fallback_models``/``models``).

            A bare string unwraps to its single entry (index 0), mirroring
            ``omoconfig`` normalization; other non-list shapes are skipped.
            """
            entries = block.get(key)
            if isinstance(entries, str):
                if entries == old:
                    block[key] = new
                    hit(label, route, f"{key}[0]")
                return
            if not isinstance(entries, list):
                return
            for index, item in enumerate(entries):
                if isinstance(item, str) and item == old:
                    entries[index] = new
                    hit(label, route, f"{key}[{index}]")
                elif isinstance(item, dict) and item.get("model") == old:
                    item["model"] = new  # other fields preserved
                    hit(label, route, f"{key}[{index}]")

        agents = section.get("agents")
        if isinstance(agents, dict):
            for name, block in agents.items():
                if not isinstance(block, dict):
                    continue
                if block.get("model") == old:
                    block["model"] = new
                    hit(label, name, "model")
                replace_entries(block, "fallback_models", name)
                replace_entries(block, "models", name)
        categories = section.get("categories")
        if isinstance(categories, dict):
            for name, block in categories.items():
                if isinstance(block, dict):
                    replace_entries(block, "models", name)
        catalog = section.get("models")
        if isinstance(catalog, dict):
            for name, item in catalog.items():
                field = f"catalog:{name}"
                if isinstance(item, str) and item == old:
                    catalog[name] = new
                    hit(label, "", field)
                elif isinstance(item, dict) and item.get("model") == old:
                    item["model"] = new
                    hit(label, "", field)

    sections: list[tuple[str, dict]] = [("<root>", changed)]
    for block_key in HARNESS_BLOCKS:
        if isinstance(changed.get(block_key), dict):
            sections.append((block_key, changed[block_key]))
    for label, section in sections:
        replace_section(section, label)
    return changed, tuple(hits)


def replace_model_in_profile(paths: Paths, name: str, old: str, new: str, *,
                             dry_run: bool = False) -> ReplaceResult:
    """Replace ``old`` with ``new`` in stored profile ``name``; module doc."""

    def result(status: UseStatus, hits: tuple[ReplacementHit, ...] = (),
               message: str = "",
               error: str | None = None) -> ReplaceResult:
        return ReplaceResult(status=status, profile=name, hits=hits,
                             message=message, error=error)

    try:
        record = read_profile(paths, name)
    except InvalidProfileName as exc:
        return result(UseStatus.BLOCKED, message=str(exc))
    except ProfileNotFoundError:
        return result(UseStatus.BLOCKED,
                      message=f"Profile '{name}' not found")

    if not record.is_valid or record.document is None:
        return result(
            UseStatus.BLOCKED,
            message=f"Cannot apply invalid profile: {name}: {record.error}",
            error=record.error,
        )

    changed_doc, hits = replace_model(record.document.raw, old, new)
    if not hits:
        return result(
            UseStatus.NO_MATCHES,
            message=f"No matches for model '{old}' in profile '{name}'",
        )
    if dry_run:
        return result(
            UseStatus.PREVIEW, hits,
            f"Would replace {len(hits)} model reference(s) "
            f"in profile '{name}'",
        )

    try:
        write_profile(paths, name, changed_doc, overwrite=True)
    except Exception as exc:
        return result(UseStatus.FAILED, hits, str(exc), error=str(exc))

    message = f"Replaced {len(hits)} model reference(s) in profile '{name}'"
    if read_active(paths) == name:
        rerender = use_profile(paths, name)
        if rerender.status == UseStatus.APPLIED:
            message += "; re-rendered active configuration"
        elif rerender.status == UseStatus.FAILED:
            message += f"; re-render failed: {rerender.error}"
    return result(UseStatus.APPLIED, hits, message)


def replace_model_all(paths: Paths, old: str, new: str, *,
                      dry_run: bool = False) -> list[tuple[str, ReplaceResult]]:
    """Replace in every stored profile, ``list_profiles`` order.

    Invalid profiles surface as their BLOCKED result (skip-and-report);
    never raises for per-profile failures.
    """
    return [
        (record.name,
         replace_model_in_profile(paths, record.name, old, new,
                                  dry_run=dry_run))
        for record in list_profiles(paths)
    ]


# Profile migration (legacy ``fallback_models`` -> canonical ``models``
# chains); allow: SIZE_OK — plan-pinned single-module engine.


@dataclass(frozen=True)
class MigrateResult:
    """Immutable result of one profile-migration operation.

    ``routes`` is the number of legacy routes converted (from
    :func:`transform.migrate_document`); ``rerendered`` is True only
    when the ACTIVE profile's live ``omo.jsonc`` was rewritten.
    """

    status: UseStatus
    profile: str
    routes: int = 0
    message: str = ""
    error: str | None = None
    rerendered: bool = False


def migrate_profile(paths: Paths, name: str, *,
                    dry_run: bool = False) -> MigrateResult:
    """Convert stored profile ``name`` to canonical chains; module doc."""

    def result(status: UseStatus, routes: int = 0, message: str = "",
               error: str | None = None,
               rerendered: bool = False) -> MigrateResult:
        return MigrateResult(status=status, profile=name, routes=routes,
                             message=message, error=error,
                             rerendered=rerendered)

    try:
        record = read_profile(paths, name)
    except InvalidProfileName as exc:
        return result(UseStatus.BLOCKED, message=str(exc))
    except ProfileNotFoundError:
        return result(UseStatus.BLOCKED,
                      message=f"Profile '{name}' not found")

    if not record.is_valid or record.document is None:
        return result(
            UseStatus.BLOCKED,
            message=f"Cannot migrate invalid profile: {name}: "
                    f"{record.error}",
            error=record.error,
        )

    migrated_doc, routes = migrate_document(record.document.raw)
    if routes == 0:
        return result(
            UseStatus.NO_MATCHES,
            message=f"No migration needed for profile '{name}'",
        )
    if dry_run:
        return result(
            UseStatus.PREVIEW, routes,
            f"Would migrate profile '{name}' ({routes} route(s))",
        )

    try:
        write_profile(paths, name, migrated_doc, overwrite=True)
    except Exception as exc:
        return result(UseStatus.FAILED, routes, str(exc), error=str(exc))

    message = f"Migrated profile '{name}' ({routes} route(s))"
    rerendered = False
    if read_active(paths) == name:
        rerender = use_profile(paths, name)
        if rerender.status == UseStatus.APPLIED:
            rerendered = True
            message += "; re-rendered active configuration"
        elif rerender.status == UseStatus.FAILED:
            message += f"; re-render failed: {rerender.error}"
    return result(UseStatus.APPLIED, routes, message,
                  rerendered=rerendered)


def migrate_all(paths: Paths, *,
                dry_run: bool = False) -> list[tuple[str, MigrateResult]]:
    """Migrate every stored profile, ``list_profiles`` order.

    Invalid profiles surface as their BLOCKED result (skip-and-report);
    never raises for per-profile failures.
    """
    return [
        (record.name,
         migrate_profile(paths, record.name, dry_run=dry_run))
        for record in list_profiles(paths)
    ]
