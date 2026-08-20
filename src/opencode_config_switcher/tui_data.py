"""Pure TUI data layer for the v3 profile selector: summaries + lines.

Everything here is deterministic plain-text formatting over already-loaded
:class:`~opencode_config_switcher.profiles.ProfileRecord` values — no
curses (only the pure display-width helpers of :mod:`.tui` are imported),
no printing, and profile files are NEVER reread.  The only I/O inside
:func:`build_summaries` is reading the ``.active`` marker and — for the
active record only — the live ``omo.jsonc`` through
:func:`~opencode_config_switcher.profiles.drift_status`.

Line contracts (binding for Task 12 — keep byte-identical):

- ``menu_row``: ``"{name}{badge suffix}"``; the name is truncated first so
  the badge always survives at width >= len(name) + 12.
- ``format_details`` section order: Profile / State / File / Schema /
  model_fallback + runtime_fallback block (only when the ``[opencode]``
  harness block exists) / Agents / Categories / Warnings (section
  warnings harvested from the stdlib warnings channel, then per-route
  warnings, then runtime_fallback parse warnings) / Additional settings.
- :class:`RouteDisplay` wraps a :class:`RouteSummary` plus
  ``models_list_len`` (None unless the raw route block had a ``models``
  LIST), which drives the ``(n entries in models list)`` trailer line.
"""

import warnings
from dataclasses import dataclass
from typing import NamedTuple

from opencode_config_switcher.config import ModelSpec, RouteSummary, _parse_runtime
from opencode_config_switcher.omoconfig import summarize_routes
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.profiles import (
    ProfileRecord,
    drift_status,
    read_active,
)
from opencode_config_switcher.tui import display_width, truncate_display

__all__ = [
    "ProfileSummary",
    "RouteDisplay",
    "build_summaries",
    "state_badge",
    "state_detail",
    "menu_row",
    "format_details",
    "format_raw",
]

_KNOWN_HARNESS_KEYS = ("model_fallback", "runtime_fallback",
                       "agents", "categories")
_NOT_CONFIGURED = "not configured"


class RouteDisplay(NamedTuple):
    """A :class:`RouteSummary` wrapped with its raw ``models`` list length.

    ``models_list_len`` is None unless the route block literally had a
    ``models`` LIST (a bare string ``models`` unwraps to one entry but is
    not a list); it drives the ``(n entries in models list)`` trailer.
    """

    route: RouteSummary
    models_list_len: int | None = None


@dataclass(frozen=True)
class ProfileSummary:
    """One profile ready for the selector: record + state + routes.

    ``drift`` is one of managed/drifted/unmanaged; it is computed (via
    ``drift_status``) for the ACTIVE record only — every non-active
    profile reports ``"unmanaged"``.
    """

    record: ProfileRecord
    is_active: bool
    drift: str
    agents: tuple[RouteDisplay, ...]
    categories: tuple[RouteDisplay, ...]
    section_warnings: tuple[str, ...]


def _route_display(route: RouteSummary, section_raw: object) -> RouteDisplay:
    """Lift one RouteSummary, attaching its raw ``models`` list length."""
    models_list_len: int | None = None
    if isinstance(section_raw, dict):
        block = section_raw.get(route.name)
        models = block.get("models") if isinstance(block, dict) else None
        if isinstance(models, list):
            models_list_len = len(models)
    return RouteDisplay(route=route, models_list_len=models_list_len)


def build_summaries(paths: Paths,
                    records: list[ProfileRecord]) -> list[ProfileSummary]:
    """Summarize ``records`` in order; see the module docstring for the I/O."""
    active = read_active(paths)
    summaries: list[ProfileSummary] = []
    for record in records:
        is_active = record.name == active
        drift = drift_status(paths, record) if is_active else "unmanaged"
        agents: tuple[RouteDisplay, ...] = ()
        categories: tuple[RouteDisplay, ...] = ()
        section_warnings: tuple[str, ...] = ()
        harness = (record.document.harness("[opencode]")
                   if record.document is not None else None)
        if harness is not None:
            # Harvest section-level skip warnings without stderr leakage;
            # "always" also defeats ambient filters that would swallow them.
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                agents_raw, categories_raw = summarize_routes(harness)
            section_warnings = tuple(
                str(entry.message) for entry in caught
                if issubclass(entry.category, UserWarning)
                and str(entry.message).startswith(("agents.", "categories."))
            )
            agents = tuple(_route_display(r, harness.get("agents"))
                           for r in agents_raw)
            categories = tuple(_route_display(r, harness.get("categories"))
                               for r in categories_raw)
        summaries.append(ProfileSummary(
            record=record,
            is_active=is_active,
            drift=drift,
            agents=agents,
            categories=categories,
            section_warnings=section_warnings,
        ))
    return summaries


def state_badge(summary: ProfileSummary) -> str:
    """"ACTIVE", "CUSTOM", "INVALID", or "" — the menu badge text."""
    if not summary.record.is_valid:
        return "INVALID"
    if summary.is_active and summary.drift == "managed":
        return "ACTIVE"
    if summary.is_active and summary.drift == "drifted":
        return "CUSTOM"
    return ""


def state_detail(summary: ProfileSummary) -> str:
    """The State-line text; empty string for plain inactive profiles."""
    if not summary.record.is_valid:
        return f"invalid: {summary.record.error}"
    if summary.is_active and summary.drift == "managed":
        return "active"
    if summary.is_active and summary.drift == "drifted":
        return (f"custom (configuration drifted from "
                f"'{summary.record.name}')")
    return ""


def menu_row(summary: ProfileSummary, width: int) -> str:
    """One menu line: the name truncated first so the badge always fits."""
    badge = state_badge(summary)
    suffix = f" [{badge}]" if badge else ""
    name = summary.record.name
    name_budget = width - display_width(suffix)
    if name_budget < 1:
        return truncate_display(name + suffix, width)
    return truncate_display(name, name_budget) + suffix


def _primary_extras(spec: ModelSpec) -> str:
    """``variant=… reasoning=… effort=…`` with absent parts omitted."""
    parts: list[str] = []
    if spec.variant:
        parts.append(f"variant={spec.variant}")
    if spec.reasoning:
        parts.append(f"reasoning={spec.reasoning}")
    if spec.reasoning_effort:
        parts.append(f"effort={spec.reasoning_effort}")
    return " ".join(parts)


def _fallback_suffix(spec: ModelSpec) -> str:
    """Compact ``(reasoning=…, variant=…)`` trailer for fallback entries."""
    parts: list[str] = []
    if spec.reasoning:
        parts.append(f"reasoning={spec.reasoning}")
    if spec.variant:
        parts.append(f"variant={spec.variant}")
    if spec.reasoning_effort:
        parts.append(f"effort={spec.reasoning_effort}")
    return f" ({', '.join(parts)})" if parts else ""


def _format_route(display: RouteDisplay) -> list[str]:
    route = display.route
    lines = [f"  {route.name}: {route.primary.model or _NOT_CONFIGURED}"]
    extras = _primary_extras(route.primary)
    if extras:
        lines.append(f"    primary: {extras}")
    lines.append("    fallbacks:")
    if route.fallbacks:
        for index, fallback in enumerate(route.fallbacks, 1):
            lines.append(f"      {index}. {fallback.model}"
                         f"{_fallback_suffix(fallback)}")
    else:
        lines.append(f"      {_NOT_CONFIGURED}")
    if display.models_list_len is not None:
        lines.append(f"    ({display.models_list_len} "
                     f"entries in models list)")
    return lines


def _or_not_configured(value: object) -> str:
    return _NOT_CONFIGURED if value is None else f"{value}"


def _compact_value(value: object) -> str:
    if isinstance(value, list):
        return f"[{len(value)} items]"
    if isinstance(value, dict):
        return "{" + ", ".join(str(key) for key in value) + "}"
    return f"{value}"


def format_details(summary: ProfileSummary, width: int) -> list[str]:
    """Deterministic details-panel lines, every line clipped to ``width``."""
    record = summary.record
    w = max(width, 1)
    lines = [f"Profile: {record.name}"]
    lines.append(f"State: {state_detail(summary) or 'inactive'}")
    if record.size_bytes is not None and record.modified_ns is not None:
        lines.append(f"File: {record.path.name} ({record.size_bytes} bytes, "
                     f"modified {record.modified_ns})")
    else:
        lines.append(f"File: {record.path.name}")
    harness = (record.document.harness("[opencode]")
               if record.document is not None else None)
    runtime_warnings: tuple[str, ...] = ()
    if record.document is not None:
        lines.append(f"Schema: {record.document.schema}")
    if harness is not None:
        fallback_flag = harness.get("model_fallback")
        lines.append(f"model_fallback: "
                     f"{_or_not_configured(fallback_flag)}")
        runtime, runtime_warnings = _parse_runtime(
            harness.get("runtime_fallback"))
        lines.append("runtime_fallback:")
        lines.append(f"  enabled: {_or_not_configured(runtime.enabled)}")
        retry = ", ".join(str(code) for code in runtime.retry_on_errors)
        lines.append(f"  retry_on_errors: "
                     f"{retry if retry else _NOT_CONFIGURED}")
        lines.append(f"  max_fallback_attempts: "
                     f"{_or_not_configured(runtime.max_fallback_attempts)}")
        lines.append(f"  cooldown_seconds: "
                     f"{_or_not_configured(runtime.cooldown_seconds)}")
        lines.append(f"Agents ({len(summary.agents)}):")
        for route in summary.agents:
            lines.extend(_format_route(route))
        lines.append(f"Categories ({len(summary.categories)}):")
        for route in summary.categories:
            lines.extend(_format_route(route))
    collected = list(summary.section_warnings)
    for display in (*summary.agents, *summary.categories):
        collected.extend(display.route.warnings)
    collected.extend(runtime_warnings)
    if collected:
        lines.append("Warnings:")
        lines.extend(f"  {warning}" for warning in collected)
    if harness is not None:
        additional = [(key, value) for key, value in harness.items()
                      if key not in _KNOWN_HARNESS_KEYS]
        if additional:
            lines.append("Additional settings:")
            lines.extend(f"  {key}: {_compact_value(value)}"
                         for key, value in additional)
    return [truncate_display(line, w) for line in lines]


def format_raw(raw_text: str, width: int) -> list[str]:
    """Cached raw text as clipped overlay lines (splitlines + clipping)."""
    w = max(width, 1)
    return [truncate_display(line, w) for line in raw_text.splitlines()]
