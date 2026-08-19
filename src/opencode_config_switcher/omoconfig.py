"""Domain model for ``~/.omo/omo.jsonc``: load, merge, and summarize.

Merge semantics implemented by :func:`replace_sections` (binding for the
render engine):

1. Start from an ordered shallow copy of ``target.raw`` (an empty target —
   the caller-side representation of a missing file — starts empty).
2. Every profile key except the control keys (``profiles``, ``_migrations``)
   and ``$schema`` replaces or inserts: existing keys keep their ORIGINAL
   target insertion position, new keys append at the end in profile order.
3. Control keys always come from the target; a profile never injects them.
4. ``$schema`` is always first and equals the target's value when present,
   else the profile's, else :data:`OMO_SCHEMA_URL`.

Route summaries reuse the v2 ``config.ModelSpec``/``config.RouteSummary``
shapes (imported — single source, never copied).  Per-route warnings live in
``RouteSummary.warnings``; section-level warnings for SKIPPED non-dict route
blocks are emitted through the stdlib ``warnings`` channel because the
binding two-tuple return signature of :func:`summarize_routes` has no slot
for them.
"""

import warnings
from dataclasses import dataclass
from pathlib import Path

from opencode_config_switcher.config import ModelSpec, RouteSummary
from opencode_config_switcher.jsonc import JsoncError
from opencode_config_switcher.jsonc import loads as jsonc_loads

__all__ = [
    "CONTROL_KEYS",
    "HARNESS_BLOCKS",
    "OMO_SCHEMA_URL",
    "LoadError",
    "OmoDocument",
    "load_omo_document",
    "replace_sections",
    "summarize_routes",
]

OMO_SCHEMA_URL = (
    "https://raw.githubusercontent.com/code-yeongyu/"
    "oh-my-openagent/dev/assets/omo.schema.json"
)
CONTROL_KEYS = ("profiles", "_migrations")
HARNESS_BLOCKS = ("[opencode]", "[codex]", "[senpi]")


@dataclass
class OmoDocument:
    """Lightweight wrapper around one parsed omo.jsonc document."""

    raw: dict

    @property
    def schema(self) -> object | None:
        return self.raw.get("$schema")

    @property
    def migrations(self) -> tuple:
        value = self.raw.get("_migrations")
        return tuple(value) if isinstance(value, list) else ()

    @property
    def control_profiles(self) -> dict | None:
        value = self.raw.get("profiles")
        return value if isinstance(value, dict) else None

    def harness(self, name: str) -> dict | None:
        if name not in HARNESS_BLOCKS:
            return None
        value = self.raw.get(name)
        return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class LoadError:
    """An omo.jsonc that could not be loaded (returned, never raised)."""

    path: Path
    message: str


def load_omo_document(path: Path) -> OmoDocument | LoadError:
    """Read and parse ``path`` exactly once; failures return ``LoadError``.

    Message contract: missing file → ``"File not found: {path}"``; read
    failure → ``"Cannot read {path}: {type}: {exc}"``; malformed JSONC →
    ``str(JsoncError)``; non-dict top level → ``"{path}: expected object at
    top level, got {type}"``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return LoadError(path=path, message=f"File not found: {path}")
    except (OSError, UnicodeDecodeError) as exc:
        return LoadError(
            path=path,
            message=f"Cannot read {path}: {type(exc).__name__}: {exc}",
        )
    try:
        data = jsonc_loads(text)
    except JsoncError as exc:
        return LoadError(path=path, message=str(exc))
    if not isinstance(data, dict):
        return LoadError(
            path=path,
            message=f"{path}: expected object at top level, "
                    f"got {type(data).__name__}",
        )
    return OmoDocument(raw=data)


def replace_sections(target: OmoDocument, profile: OmoDocument) -> dict:
    """Layer ``profile`` over ``target``; see module docstring for ordering."""
    merged = dict(target.raw)
    for key, value in profile.raw.items():
        if key == "$schema" or key in CONTROL_KEYS:
            continue
        merged[key] = value  # existing key keeps position; new key appends
    if "$schema" in target.raw:
        schema = target.raw["$schema"]
    elif "$schema" in profile.raw:
        schema = profile.raw["$schema"]
    else:
        schema = OMO_SCHEMA_URL
    merged.pop("$schema", None)
    return {"$schema": schema, **merged}


def _normalize_entries(raw: object, section: str, route: str,
                       field: str) -> tuple[tuple[ModelSpec, ...],
                                            tuple[str, ...]]:
    """Normalize a model-entry field exactly like ``config._parse_fallbacks``.

    ``field`` only names the warning messages; ``"fallback_models"``
    reproduces config.py byte-for-byte, ``"models"`` reuses the same shapes.
    """
    if raw is None:
        return (), ()
    entries: object
    if isinstance(raw, str):
        entries = [raw]
    elif isinstance(raw, list):
        entries = raw
    else:
        return (), (f"{section}.{route}.{field}: "
                    f"unexpected type {type(raw).__name__}",)
    specs: list[ModelSpec] = []
    warn_list: list[str] = []
    for idx, entry in enumerate(entries):  # type: ignore[union-attr]
        if isinstance(entry, str):
            specs.append(ModelSpec(model=entry))
        elif isinstance(entry, dict) and "model" in entry:
            specs.append(ModelSpec(
                model=str(entry.get("model")),
                variant=entry.get("variant"),
                reasoning=entry.get("reasoning"),
                reasoning_effort=entry.get("reasoningEffort"),
            ))
        else:
            warn_list.append(
                f"{section}.{route}.{field}[{idx}]: "
                f"expected a string or an object containing model")
    return tuple(specs), tuple(warn_list)


def _primary_from_block(block: dict) -> ModelSpec:
    return ModelSpec(
        model=block.get("model"),
        variant=block.get("variant"),
        reasoning=block.get("reasoning"),
        reasoning_effort=block.get("reasoningEffort"),
    )


def _summarize_route(name: str, block: dict, section: str) -> RouteSummary:
    route_warnings: list[str] = []
    fallbacks: list[ModelSpec] = []

    models_raw = block.get("models")
    models_specs, models_warns = _normalize_entries(
        models_raw, section, name, "models")
    route_warnings.extend(models_warns)

    if "model" in block:
        # Form A primary; a coexisting `models` list feeds fallbacks first.
        primary = _primary_from_block(block)
        fallbacks.extend(models_specs)
    elif models_specs:
        # Form B: first `models` entry is the primary, the rest fall back.
        primary = models_specs[0]
        fallbacks.extend(models_specs[1:])
    elif isinstance(models_raw, list) and not models_raw:
        primary = ModelSpec(model=None)
        route_warnings.append(f"{section}.{name}: empty models list")
    else:
        # Neither form present (or `models` malformed): mirror config.py's
        # ModelSpec-from-block-fields behavior with model=None.
        primary = _primary_from_block(block)

    fb_specs, fb_warns = _normalize_entries(
        block.get("fallback_models"), section, name, "fallback_models")
    fallbacks.extend(fb_specs)
    route_warnings.extend(fb_warns)

    return RouteSummary(
        name=name,
        primary=primary,
        fallbacks=tuple(fallbacks),
        warnings=tuple(route_warnings),
    )


def _summarize_section(routes_raw: object, section: str) \
        -> tuple[RouteSummary, ...]:
    if not isinstance(routes_raw, dict):
        return ()
    summaries: list[RouteSummary] = []
    for name, block in routes_raw.items():
        if not isinstance(block, dict):
            warnings.warn(
                f"{section}.{name}: expected object, "
                f"got {type(block).__name__}")
            continue
        summaries.append(_summarize_route(name, block, section))
    return tuple(summaries)


def summarize_routes(harness_block: dict | None) \
        -> tuple[tuple[RouteSummary, ...], tuple[RouteSummary, ...]]:
    """Summarize the ``agents``/``categories`` of one harness block.

    Returns ``(agents, categories)`` of v2-shaped ``RouteSummary`` tuples;
    per-route warnings ride inside each ``RouteSummary.warnings``.
    """
    if not isinstance(harness_block, dict):
        return (), ()
    agents = _summarize_section(harness_block.get("agents"), "agents")
    categories = _summarize_section(
        harness_block.get("categories"), "categories")
    return agents, categories
