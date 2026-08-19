"""Real-curses v3 selector entry for PTY tests (NOT the CLI).

Builds the temp-HOME profile store through the real profiles store,
wires :class:`SelectorServices` over the real engine, runs
:func:`run_profile_tui`, and prints post-exit markers the parent test
asserts on (after curses restored the terminal):

    TUI-EXIT:{outcome}
    TUI-USE:{status}:{message}   (only when a UseResult is carried)

Usage: ``python3.11 profile_tui_entry.py [MODE] HOME``

Modes:
    ""           normal (three seeded profiles: alpha/beta/gamma)
    "noop-seed"  apply "alpha" through the real engine BEFORE launching
    "fail-apply" use_fn raises RuntimeError (fatal-cleanup probe)
    "ascii-border"  force the ASCII border fallback before launching
"""

import sys
from pathlib import Path

from opencode_config_switcher.engine import use_profile
from opencode_config_switcher.omoconfig import OMO_SCHEMA_URL
from opencode_config_switcher.paths import Paths
from opencode_config_switcher.profiles import (
    create_profile,
    delete_profile,
    list_profiles,
)
from opencode_config_switcher.tui import (
    SelectorServices,
    TuiOutcome,
    run_profile_tui,
)
from opencode_config_switcher.tui_data import build_summaries

SEED = ("alpha", "beta", "gamma")


def _doc(model: str) -> dict:
    return {
        "$schema": OMO_SCHEMA_URL,
        "[opencode]": {
            "agents": {
                "build": {"model": model,
                          "fallback_models": ["provider/fallback"]},
            },
        },
    }


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    home = Path(sys.argv[2] if len(sys.argv) > 2 else ".")
    paths = Paths.build(home)
    for name in SEED:
        if not (paths.profiles_dir / f"{name}.jsonc").exists():
            create_profile(paths, name, _doc(f"provider/{name}"))

    if mode == "ascii-border":
        import opencode_config_switcher.tui as tui_mod
        tui_mod._draw_acs_border = tui_mod._draw_ascii_border
    if mode == "noop-seed":
        seeded = use_profile(paths, "alpha")
        assert seeded.status.value == "APPLIED", seeded.message

    def refresh():
        return build_summaries(paths, list_profiles(paths))

    def failing_use(name):
        raise RuntimeError("injected use failure")

    services = SelectorServices(
        use_fn=(failing_use if mode == "fail-apply"
                else lambda name: use_profile(paths, name)),
        create_fn=lambda name: create_profile(paths, name),
        delete_fn=lambda name: delete_profile(paths, name),
        refresh_fn=refresh,
    )
    result = run_profile_tui(refresh(), paths, services)
    print(f"TUI-EXIT:{result.outcome.value}")
    if result.apply_result is not None:
        use = result.apply_result
        print(f"TUI-USE:{use.status.value}:{use.message}")
    return 1 if result.outcome is TuiOutcome.FATAL else 0


if __name__ == "__main__":
    raise SystemExit(main())
