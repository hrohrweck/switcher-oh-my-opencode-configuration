"""Filesystem layout of the ``~/.omo`` domain (pure path math, no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["Paths", "DEFAULT"]


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
