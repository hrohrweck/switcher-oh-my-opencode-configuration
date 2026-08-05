"""Configuration discovery, JSON parsing, and typed summary extraction."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple


# ── typed contracts ────────────────────────────────────────────────

class ModelSpec(NamedTuple):
    """A single model reference with optional metadata fields."""
    model: str | None
    variant: str | None = None
    reasoning: str | None = None
    reasoning_effort: str | None = None


class RouteSummary(NamedTuple):
    """An agent or category with primary model and fallback chain."""
    name: str
    primary: ModelSpec
    fallbacks: tuple[ModelSpec, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeFallbackSummary:
    """Parsed runtime_fallback block."""
    enabled: bool | None = None
    retry_on_errors: tuple[int, ...] = ()
    max_fallback_attempts: int | None = None
    cooldown_seconds: int | float | None = None
    additional: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True)
class FileSummary:
    """Filesystem metadata for one discovered configuration file."""
    path: Path
    name: str = ""
    size_bytes: int | None = None
    modified_ns: int | None = None
    is_current: bool = False
    raw_text: str | None = None


@dataclass(frozen=True)
class ConfigSummary:
    """Complete parsed view of one configuration, ready for rendering."""
    file: FileSummary
    is_valid: bool = True
    error: str | None = None
    schema: str | None = None
    model_fallback: bool | None = None
    runtime_fallback: RuntimeFallbackSummary = field(
        default_factory=RuntimeFallbackSummary)
    agents: tuple[RouteSummary, ...] = ()
    categories: tuple[RouteSummary, ...] = ()
    additional_settings: tuple[tuple[str, object], ...] = ()
    warnings: tuple[str, ...] = ()


# ── path contracts ─────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "opencode"
CURRENT_AGENT = CONFIG_DIR / "oh-my-openagent.json"
CURRENT_LEGACY = CONFIG_DIR / "oh-my-opencode.json"


def _resolve_active() -> Path:
    """Return the active config path, preferring openagent over opencode."""
    return CURRENT_AGENT if CURRENT_AGENT.exists() else CURRENT_LEGACY


def _resolve_backup(active: Path) -> Path:
    """Return the backup path corresponding to the active config."""
    return active.with_suffix(active.suffix + ".BAK")


ACTIVE_PATH = _resolve_active()
BACKUP_PATH = _resolve_backup(ACTIVE_PATH)


# ── discovery ──────────────────────────────────────────────────────

def _glob_configs() -> list[Path]:
    """Collect candidate configs using both naming conventions."""
    files: list[Path] = []
    seen: set[str] = set()

    for pattern in ("oh-my-opencode*.json", "oh-my-openagent*.json"):
        for p in CONFIG_DIR.glob(pattern):
            name = p.name
            if name in seen:
                continue
            if name in ("oh-my-opencode.json", "oh-my-openagent.json"):
                continue
            if name.endswith(".BAK"):
                continue
            files.append(p)
            seen.add(name)

    return sorted(files)


def discover_configs() -> tuple[Path, list[Path]]:
    """Return (active_path, sorted_candidate_list) including the active config.

    Raises FileNotFoundError if CONFIG_DIR is missing.
    Raises RuntimeError if no config files are found.
    """
    if not CONFIG_DIR.exists():
        raise FileNotFoundError(
            f"Configuration directory not found: {CONFIG_DIR}")

    active = ACTIVE_PATH
    candidates: list[Path] = []

    # Always include the current active config first, then sort
    if active.exists():
        candidates.append(active)

    candidates.extend(_glob_configs())
    candidates.sort(key=lambda p: p.name)

    if not candidates:
        raise RuntimeError(
            f"No configuration files found in {CONFIG_DIR}")

    return active, candidates


# ── parsing ────────────────────────────────────────────────────────


def _parse_fallbacks(raw: object | None, section: str,
                     route: str) -> tuple[tuple[ModelSpec, ...], tuple[str, ...]]:
    """Normalize fallback_models field from any valid shape."""
    if raw is None:
        return (), ()

    spec_list: list[ModelSpec] = []
    warn_list: list[str] = []

    entries: list[object]
    if isinstance(raw, str):
        entries = [raw]
    elif isinstance(raw, list):
        entries = raw  # type: ignore[assignment]
    else:
        warn_list.append(
            f"{section}.{route}.fallback_models: "
            f"unexpected type {type(raw).__name__}")
        return (), tuple(warn_list)

    for idx, entry in enumerate(entries):
        if isinstance(entry, str):
            spec_list.append(ModelSpec(model=entry))
        elif isinstance(entry, dict) and "model" in entry:
            spec_list.append(ModelSpec(
                model=str(entry.get("model")),
                variant=entry.get("variant"),
                reasoning=entry.get("reasoning"),
                reasoning_effort=entry.get("reasoningEffort"),
            ))
        else:
            warn_list.append(
                f"{section}.{route}.fallback_models[{idx}]: "
                f"expected a string or an object containing model")

    return tuple(spec_list), tuple(warn_list)


def _parse_runtime(raw: object | None) -> tuple[
        RuntimeFallbackSummary, tuple[str, ...]]:
    """Extract runtime_fallback block with safe type coercion."""
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return RuntimeFallbackSummary(), ()

    enabled: bool | None = None
    retry: tuple[int, ...] = ()
    attempts: int | None = None
    cooldown: int | float | None = None
    additional: list[tuple[str, object]] = []

    for key, val in raw.items():  # type: ignore[union-attr]
        if key == "enabled":
            if isinstance(val, bool):
                enabled = val
            else:
                warnings.append(
                    f"runtime_fallback.enabled: expected boolean, "
                    f"got {type(val).__name__}")
        elif key == "retry_on_errors":
            if isinstance(val, list):
                try:
                    retry = tuple(int(v) for v in val)
                except (ValueError, TypeError):
                    warnings.append(
                        "runtime_fallback.retry_on_errors: "
                        "expected list of integers")
            else:
                warnings.append(
                    "runtime_fallback.retry_on_errors: "
                    "expected a list")
        elif key == "max_fallback_attempts":
            if isinstance(val, (int, float)):
                attempts = int(val)
            else:
                warnings.append(
                    f"runtime_fallback.max_fallback_attempts: "
                    f"expected number, got {type(val).__name__}")
        elif key == "cooldown_seconds":
            if isinstance(val, (int, float)):
                cooldown = val
            else:
                warnings.append(
                    f"runtime_fallback.cooldown_seconds: "
                    f"expected number, got {type(val).__name__}")
        else:
            additional.append((key, val))

    return RuntimeFallbackSummary(
        enabled=enabled,
        retry_on_errors=retry,
        max_fallback_attempts=attempts,
        cooldown_seconds=cooldown,
        additional=tuple(additional),
    ), tuple(warnings)


def _parse_routes(routes_raw: object | None,
                  section: str) -> tuple[tuple[RouteSummary, ...],
                                         tuple[str, ...]]:
    """Parse agents or categories section preserving insertion order."""
    if not isinstance(routes_raw, dict):
        return (), ()

    results: list[RouteSummary] = []
    all_warnings: list[str] = []

    for name, block in routes_raw.items():  # type: ignore[union-attr]
        route_warnings: list[str] = []
        if not isinstance(block, dict):
            all_warnings.append(
                f"{section}.{name}: expected object, "
                f"got {type(block).__name__}")
            continue

        primary = ModelSpec(
            model=block.get("model"),
            variant=block.get("variant"),
            reasoning=block.get("reasoning"),
            reasoning_effort=block.get("reasoningEffort"),
        )

        fallbacks, fb_warns = _parse_fallbacks(
            block.get("fallback_models"), section, name)
        route_warnings.extend(fb_warns)

        results.append(RouteSummary(
            name=name,
            primary=primary,
            fallbacks=fallbacks,
            warnings=tuple(route_warnings),
        ))
        all_warnings.extend(route_warnings)

    return tuple(results), tuple(all_warnings)


def _parse_config(path: Path, raw: str, is_current: bool) -> ConfigSummary:
    """Parse a single configuration file into a ConfigSummary."""
    file = FileSummary(
        path=path,
        name=path.name,
        size_bytes=path.stat().st_size,
        modified_ns=path.stat().st_mtime_ns,
        is_current=is_current,
        raw_text=raw,
    )

    # Attempt JSON parse
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ConfigSummary(
            file=file,
            is_valid=False,
            error=f"Invalid JSON in {path.name} at line {exc.lineno}, "
                  f"column {exc.colno}: {exc.msg}",
        )
    except Exception as exc:
        return ConfigSummary(
            file=file,
            is_valid=False,
            error=f"Cannot read {path.name}: "
                  f"{type(exc).__name__}: {exc}",
        )

    warnings: list[str] = []

    # Top-level must be an object
    if not isinstance(data, dict):
        warnings.append(
            f"Top-level JSON value is {type(data).__name__}; "
            f"expected object")
        return ConfigSummary(
            file=file,
            is_valid=True,
            schema=None,
            warnings=tuple(warnings),
        )

    # Dedicated fields
    schema = data.get("$schema")
    model_fallback = data.get("model_fallback")

    runtime_summary, rt_warns = _parse_runtime(data.get("runtime_fallback"))
    warnings.extend(rt_warns)

    agents, ag_warns = _parse_routes(data.get("agents"), "agents")
    warnings.extend(ag_warns)

    categories, cat_warns = _parse_routes(data.get("categories"), "categories")
    warnings.extend(cat_warns)

    # Additional settings: everything except dedicated keys
    consumed = {"$schema", "model_fallback", "runtime_fallback",
                "agents", "categories"}
    additional: list[tuple[str, object]] = [
        (k, v) for k, v in data.items() if k not in consumed
    ]

    return ConfigSummary(
        file=file,
        is_valid=True,
        schema=schema,
        model_fallback=model_fallback if isinstance(model_fallback, bool) else None,
        runtime_fallback=runtime_summary,
        agents=agents,
        categories=categories,
        additional_settings=tuple(additional),
        warnings=tuple(warnings),
    )


def parse_all(active: Path, candidates: list[Path]
              ) -> list[ConfigSummary]:
    """Parse and cache all discovered configurations.

    The returned list is in the same order as *candidates*.
    """
    summaries: list[ConfigSummary] = []
    for config_path in candidates:
        try:
            raw = config_path.read_text(encoding="utf-8")
        except Exception as exc:
            summaries.append(ConfigSummary(
                file=FileSummary(
                    path=config_path,
                    name=config_path.name,
                    is_current=(config_path.resolve() == active.resolve()),
                ),
                is_valid=False,
                error=f"Cannot read {config_path.name}: "
                      f"{type(exc).__name__}: {exc}",
            ))
            continue

        summaries.append(
            _parse_config(config_path, raw, config_path == active))

    return summaries
