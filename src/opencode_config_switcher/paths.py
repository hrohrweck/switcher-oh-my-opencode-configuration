"""Filesystem layout of the ``~/.omo`` domain (pure path math, no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

__all__ = ["ApplyScope", "Paths", "project_omo_paths", "DEFAULT"]


class ApplyScope(str, Enum):
    """Target of a profile apply: the user's home or a project folder."""

    GLOBAL = "GLOBAL"
    LOCAL = "LOCAL"


def project_omo_paths(cwd: Path) -> tuple[Path, Path]:
    """Project-scoped ``omo.jsonc`` and its ``.BAK`` under ``cwd``."""
    omo_path = cwd / ".omo" / "omo.jsonc"
    return omo_path, omo_path.with_name(omo_path.name + ".BAK")


@dataclass(frozen=True)
class Paths:
    """Absolute paths for one user's omo.jsonc world, derived from ``home``."""

    home: Path
    omo_path: Path
    omo_backup: Path
    profiles_dir: Path
    active_marker: Path
    legacy_dir: Path

    @classmethod
    def build(cls, home: Path) -> Paths:
        """Derive every path from ``home`` without touching the filesystem."""
        omo_path = home / ".omo" / "omo.jsonc"
        profiles_dir = home / ".omo" / "profiles"
        return cls(
            home=home,
            omo_path=omo_path,
            omo_backup=omo_path.with_name(omo_path.name + ".BAK"),
            profiles_dir=profiles_dir,
            active_marker=profiles_dir / ".active",
            legacy_dir=home / ".config" / "opencode",
        )


DEFAULT = Paths.build(Path.home())
