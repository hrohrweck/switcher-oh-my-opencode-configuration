"""Configuration switching service with exact legacy-preserving behaviour."""

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from opencode_config_switcher.config import BACKUP_PATH


class ApplyStatus(str, Enum):
    """Outcome of an apply operation."""
    APPLIED = "APPLIED"
    NOOP = "NOOP"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class ApplyStage(str, Enum):
    """Stage at which a failure occurred (NONE for success/blocked)."""
    NONE = "NONE"
    VALIDATION = "VALIDATION"
    BACKUP = "BACKUP"
    COPY = "COPY"


@dataclass(frozen=True)
class ApplyResult:
    """Immutable result of attempting to apply a configuration."""
    status: ApplyStatus
    source: Path
    active: Path
    backup: Path
    message: str
    error: str | None = None
    failed_stage: ApplyStage = ApplyStage.NONE


def apply_config(source: Path, /, *,
                 active: Path,
                 backup: Path | None = None,
                 is_valid: bool = True,
                 error_reason: str | None = None,
                 ) -> ApplyResult:
    """Copy *source* to *active*, creating a single-generation backup.

    Preserves exact legacy semantics:
    - If *source* resolves to *active*, return NOOP without any file I/O.
      This holds even when *is_valid* is False.
    - If *source* != *active* and *is_valid* is False, return BLOCKED.
    - Otherwise: shutil.copy2(active, backup) when active exists, then
      shutil.copy2(source, active).

    *backup* defaults to BACKUP_PATH from config when omitted.
    """
    if backup is None:
        backup = BACKUP_PATH

    # No-op when selecting the already-active config
    if source.resolve() == active.resolve():
        return ApplyResult(
            status=ApplyStatus.NOOP,
            source=source, active=active, backup=backup,
            message=f"No change: {source.name} is already active",
        )

    # Block invalid / unreadable non-active configs
    if not is_valid:
        reason = error_reason or "invalid configuration"
        return ApplyResult(
            status=ApplyStatus.BLOCKED,
            source=source, active=active, backup=backup,
            message=(
                f"Cannot apply invalid configuration: "
                f"{source.name}: {reason}"
            ),
            error=reason,
            failed_stage=ApplyStage.VALIDATION,
        )

    # ── apply (backup + copy) ─────────────────────────────────────
    if active.exists():
        try:
            shutil.copy2(active, backup)
        except Exception as exc:
            return ApplyResult(
                status=ApplyStatus.FAILED,
                source=source, active=active, backup=backup,
                message=f"Failed to create backup: "
                        f"{type(exc).__name__}: {exc}",
                error=str(exc),
                failed_stage=ApplyStage.BACKUP,
            )

    try:
        shutil.copy2(source, active)
    except Exception as exc:
        return ApplyResult(
            status=ApplyStatus.FAILED,
            source=source, active=active, backup=backup,
            message=f"Failed to apply configuration: "
                    f"{type(exc).__name__}: {exc}",
            error=str(exc),
            failed_stage=ApplyStage.COPY,
        )

    return ApplyResult(
        status=ApplyStatus.APPLIED,
        source=source, active=active, backup=backup,
        message=f"Configuration applied: {source.name}",
    )
