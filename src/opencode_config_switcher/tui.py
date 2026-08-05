"""Pure TUI layout, state transitions, and structured formatting.

No curses — all functions are deterministic and testable without a terminal.
"""

import json
import unicodedata
from dataclasses import dataclass, field
from enum import Enum, auto

from opencode_config_switcher.config import ConfigSummary, ModelSpec


# ── display-width helpers ──────────────────────────────────────────

def display_width(text: str) -> int:
    """Compute the display width of *text*, handling CJK and combining marks."""
    w = 0
    for ch in text:
        if unicodedata.combining(ch) != 0:
            continue
        ea = unicodedata.east_asian_width(ch)
        w += 2 if ea in ("W", "F") else 1
    return w


def truncate_display(text: str, max_width: int,
                     indicator: str = "…") -> str:
    """Truncate *text* so its display width is at most *max_width*."""
    if max_width <= 0:
        return ""
    ind_w = display_width(indicator)
    if display_width(text) <= max_width:
        return text

    result: list[str] = []
    cur_w = 0
    limit = max_width - ind_w
    for ch in text:
        if unicodedata.combining(ch) != 0:
            result.append(ch)
            continue
        ch_w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cur_w + ch_w > limit:
            break
        result.append(ch)
        cur_w += ch_w
    return "".join(result) + indicator


# ── layout modes ───────────────────────────────────────────────────

class LayoutMode(Enum):
    TOO_SMALL = auto()
    NARROW = auto()
    WIDE = auto()


HEADER_ROWS = 3
FOOTER_ROWS = 2
AVAILABLE_ROWS = 12  # minimum rows for content


def compute_layout(cols: int, rows: int) -> LayoutMode:
    """Return the layout mode for the given terminal dimensions."""
    if cols < 40 or rows < 12:
        return LayoutMode.TOO_SMALL
    if cols >= 100 and rows >= 18:
        return LayoutMode.WIDE
    return LayoutMode.NARROW


def left_width(cols: int) -> int:
    """Compute the left pane width in WIDE mode."""
    return min(44, max(30, cols // 3))


class NarrowPane(Enum):
    MENU = auto()
    DETAILS = auto()


# ── app state ──────────────────────────────────────────────────────

@dataclass
class AppState:
    """Mutable TUI application state."""
    selected_idx: int = 0
    config_count: int = 0
    menu_offset: int = 0
    detail_offset: int = 0
    narrow_pane: NarrowPane = NarrowPane.MENU
    overlay_open: bool = False
    overlay_offset: int = 0
    status: str | None = None
    layout: LayoutMode = LayoutMode.NARROW

    def clamp(self) -> None:
        """Clamp all indices after config change or resize."""
        if self.config_count == 0:
            self.selected_idx = 0
            self.menu_offset = 0
            self.detail_offset = 0
            return
        self.selected_idx = max(0, min(
            self.selected_idx, self.config_count - 1))
        # Menu scroll: keep selection visible
        menu_height = AVAILABLE_ROWS - HEADER_ROWS - FOOTER_ROWS - 2
        # Actually the available content rows depend on mode...
        # Use a simple clamp for menu
        max_menu_off = max(0, self.config_count - 1)
        self.menu_offset = max(0, min(
            self.menu_offset, max_menu_off))


# ── key transitions (pure) ─────────────────────────────────────────

def handle_key(state: AppState, key: str) -> str | None:
    """Apply *key* to *state* and return an intent string or None.

    Valid intents: 'quit', 'apply'.
    """
    lm = state.layout

    # Overlay mode — check BEFORE universal quit keys (exclude TOO_SMALL)
    if state.overlay_open and lm != LayoutMode.TOO_SMALL:
        if key in ("q", "d"):
            state.overlay_open = False
            state.overlay_offset = 0
            return None
        if key == "up":
            state.overlay_offset = max(0, state.overlay_offset - 1)
        elif key == "down":
            state.overlay_offset += 1
        elif key == "pageup":
            state.overlay_offset = max(0, state.overlay_offset - 10)
        elif key == "pagedown":
            state.overlay_offset += 10
        return None

    # Universal quit keys
    if key in ("q", "ctrlc", "ctrld"):
        return "quit"

    # TOO_SMALL: only quit allowed (already handled above), everything else ignored
    if lm == LayoutMode.TOO_SMALL:
        return None

    # WIDE mode — root
    if lm == LayoutMode.WIDE:
        if key == "up":
            state.selected_idx = max(0, state.selected_idx - 1)
            state.detail_offset = 0  # reset on selection change
        elif key == "down":
            state.selected_idx = min(
                state.config_count - 1, state.selected_idx + 1)
            state.detail_offset = 0
        elif key == "pageup":
            state.detail_offset = max(0, state.detail_offset - 5)
        elif key == "pagedown":
            state.detail_offset += 5
        elif key == "d":
            state.overlay_open = True
            state.overlay_offset = 0
        elif key == "enter":
            return "apply"
        elif key == "tab":
            pass  # no-op in WIDE
        return None

    # NARROW mode — root
    if state.narrow_pane == NarrowPane.MENU:
        if key == "up":
            state.selected_idx = max(0, state.selected_idx - 1)
            state.detail_offset = 0
        elif key == "down":
            state.selected_idx = min(
                state.config_count - 1, state.selected_idx + 1)
            state.detail_offset = 0
        elif key == "tab":
            state.narrow_pane = NarrowPane.DETAILS
        elif key == "d":
            state.overlay_open = True
            state.overlay_offset = 0
        elif key == "enter":
            return "apply"
        elif key in ("pageup", "pagedown"):
            pass
    else:  # NARROW DETAILS
        if key == "up":
            state.detail_offset = max(0, state.detail_offset - 1)
        elif key == "down":
            state.detail_offset += 1
        elif key == "pageup":
            state.detail_offset = max(0, state.detail_offset - 5)
        elif key == "pagedown":
            state.detail_offset += 5
        elif key == "tab":
            state.narrow_pane = NarrowPane.MENU
        elif key == "d":
            state.overlay_open = True
            state.overlay_offset = 0
        elif key == "enter":
            return "apply"

    return None


# ── formatting ────────────────────────────────────────────────────

def _format_model(m: ModelSpec) -> str:
    parts = [m.model or "not configured"]
    if m.variant:
        parts.append(f"[{m.variant}]")
    elif m.reasoning:
        parts.append(f"(reasoning: {m.reasoning})")
    elif m.reasoning_effort:
        parts.append(f"(effort: {m.reasoning_effort})")
    return " ".join(parts)


def format_details(summary: ConfigSummary, width: int) -> list[str]:
    """Format a ConfigSummary into display lines for the details pane."""
    lines: list[str] = []
    w = max(width, 1)
    f = summary.file

    # File section
    lines.append(f"File: {truncate_display(str(f.path), w - 6)}")
    lines.append(f"Name: {f.name}")
    status_str = "CURRENT " if f.is_current else ""
    if summary.is_valid:
        status_str += "VALID"
    else:
        status_str += "INVALID"
    lines.append(f"Status: {status_str}")
    if f.size_bytes is not None:
        lines.append(f"Size: {f.size_bytes} bytes")
    if summary.error:
        lines.append(f"Error: {summary.error}")

    # Schema / fallback policy
    if summary.schema:
        lines.append(f"Schema: {truncate_display(summary.schema, w - 8)}")
    lines.append(f"Model Fallback: {summary.model_fallback}")

    # Runtime fallback
    rf = summary.runtime_fallback
    lines.append(f"Runtime Fallback: enabled={rf.enabled}")
    if rf.retry_on_errors:
        lines.append(f"  Retry on: {rf.retry_on_errors}")
    if rf.max_fallback_attempts is not None:
        lines.append(f"  Max attempts: {rf.max_fallback_attempts}")
    if rf.cooldown_seconds is not None:
        lines.append(f"  Cooldown: {rf.cooldown_seconds}s")
    for key, val in rf.additional:
        lines.append(f"  {key}: {val}")

    # Additional settings
    if summary.additional_settings:
        lines.append("Additional Settings:")
        for key, val in summary.additional_settings:
            if isinstance(val, (str, int, float, bool, type(None))):
                lines.append(f"  {key}: {val!r}")
            elif isinstance(val, list):
                lines.append(f"  {key}: [list, {len(val)} items]")
            elif isinstance(val, dict):
                lines.append(f"  {key}: {{object, {len(val)} keys}}")
            else:
                lines.append(f"  {key}: {type(val).__name__}")

    # Agents
    if not summary.agents and not summary.categories:
        lines.append("")
        lines.append("Agents: none configured")
        lines.append("Categories: none configured")
    else:
        if summary.agents:
            lines.append(f"Agents ({len(summary.agents)}):")
            for route in summary.agents:
                lines.append(f"  {route.name}: {_format_model(route.primary)}")
                for i, fb in enumerate(route.fallbacks, 1):
                    lines.append(f"    [{i}] {_format_model(fb)}")
                for warn in route.warnings:
                    lines.append(f"    ! {warn}")

        if summary.categories:
            lines.append(f"Categories ({len(summary.categories)}):")
            for route in summary.categories:
                lines.append(f"  {route.name}: {_format_model(route.primary)}")
                for i, fb in enumerate(route.fallbacks, 1):
                    lines.append(f"    [{i}] {_format_model(fb)}")
                for warn in route.warnings:
                    lines.append(f"    ! {warn}")

    # Global warnings
    for warn_msg in summary.warnings:
        lines.append(f"! {warn_msg}")

    # Truncate each line to width
    return [truncate_display(line, w) for line in lines]


def format_overlay(raw_text: str, width: int) -> list[str]:
    """Format raw JSON text for the overlay pane (vertical scroll only)."""
    lines = raw_text.splitlines()
    return [truncate_display(line, width) for line in lines]
