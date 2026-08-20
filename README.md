# OpenCode Configuration Switcher v3.1.0 — Profile-Based omo.jsonc Switcher

A command-line toolkit that manages named configuration profiles under `~/.omo/profiles/` and renders the selected profile into `~/.omo/omo.jsonc`, the configuration read by oh-my-opencode / oh-my-openagent. Switching is render-on-select: profiles are stored documents, the live file is generated from them, and nothing in your legacy `~/.config/opencode/` directory is ever modified.

## Features

- **Profile Store**: Named profiles live as JSONC files in `~/.omo/profiles/`; profile names use letters, digits, `.`, `_`, `-` (up to 64 characters)
- **Render-on-Select**: Applying a profile merges its sections into `~/.omo/omo.jsonc` while preserving sections the profile does not define (for example `[codex]`) and control keys (`profiles`, `_migrations`)
- **Safe Operations**: A single-generation `.BAK` backup of the live file is written before every change
- **Active Marker and Drift Detection**: `~/.omo/profiles/.active` records the applied profile; the tool reports whether the live file still matches it (ACTIVE) or has drifted (CUSTOM)
- **Full-Screen TUI**: A curses profile selector plus a structured profile editor with model-entry and settings forms
- **Model Replacement**: Find-and-replace a model ID in one profile or across every stored profile, with dry-run previews
- **Canonical `models` Chains and `migrate`**: Agent fallback routes are stored as canonical `models` chains matching oh-my-openagent's format; the `migrate` subcommand converts pre-3.1 stores
- **Legacy Import**: Onboarding and `import` subcommand bring v2-era configuration files in as profiles
- **Plain Mode**: Non-TTY/piped execution uses a one-shot numbered selector with documented exit codes

## Requirements

- **Python 3.11+**
- **oh-my-opencode** or **oh-my-openagent** reading `~/.omo/omo.jsonc`
- **Unix-like system** (Linux, macOS). Windows is unsupported (no stdlib curses).

## Installation

### pipx (Recommended)

```bash
pipx install .
```

Or from the repository directory:

```bash
./setup.sh
```

The script prefers pipx, falling back to pip --user.

### pip --user (fallback)

```bash
python3.11 -m pip install --user .
```

If you encounter an `externally-managed-environment` error (PEP 668), install pipx first or use an isolated virtual environment.

### Commands

After installation, three commands are available, all invoking the same CLI:

- `opencode-config-switcher`: canonical command
- `switch-omo-config`: short alias
- `switch_oh-my-opencode_config.py`: legacy alias preserved from earlier versions

Check what you are running with:

```bash
opencode-config-switcher --version
```

### Uninstall

Use `pipx uninstall opencode-config-switcher`, or:

```bash
python3.11 -m pip uninstall opencode-config-switcher
```

## Commands

Invoked without a subcommand, the tool launches the interactive selector (or the plain numbered selector when stdin is not a TTY). Every subcommand also accepts `--version`.

### `list`: list stored profiles

Prints every profile, one per row, with markers: ` [active]` for the applied profile whose live file still matches, ` [custom]` when the live file has drifted from it, ` [invalid]` for profiles that fail to parse.

```bash
opencode-config-switcher list
```

### `show`: print one profile

Prints the structured details (agents, categories, fallback chains, settings) for the named profile; invalid profiles print an `INVALID:` error line first. `--raw` prints the stored bytes verbatim.

```bash
opencode-config-switcher show default
```

### `active`: print the active-profile state

Prints `Active profile: <name>` when the marker matches and the live `omo.jsonc` is unchanged, `custom (configuration drifted from '<name>')` after manual edits, or `custom (no profile active)` when no marker is set.

```bash
opencode-config-switcher active
```

### `use` / `select`: apply a profile to `~/.omo/omo.jsonc`

Renders the named profile into the live file (backing up the previous one first) and records it in `.active`. `select` is a full alias of `use`; `opencode-config-switcher select work` behaves identically. Called without a name, `use` launches the bare selector. On success:

    Profile applied: work
    Backup saved to: /home/you/.omo/omo.jsonc.BAK

```bash
opencode-config-switcher use work
```

### `create`: create a new profile

Creates `~/.omo/profiles/<name>.jsonc`. `--from PROFILE` deep-copies an existing profile's document as the starting point.

```bash
opencode-config-switcher create scratch --from work
```

### `edit`: modify a profile in the curses editor

Opens the profile in the full-screen editor (route list, model-entry forms, settings form). Saving writes the profile back, and when the edited profile is the active one, `~/.omo/omo.jsonc` is re-rendered automatically. Requires a terminal: without a TTY it prints `Editor requires a TTY` and exits 1.

```bash
opencode-config-switcher edit work
```

### `delete`: delete a profile (kept as a .BAK backup)

Prompts for confirmation (`Delete profile '<name>'? [y/N]:`) on a TTY; `--yes` skips the prompt and is required in non-interactive mode. The profile file is renamed to `<name>.jsonc.BAK` (replacing any previous backup). Deleting the active profile also clears the active marker (`No profile is active now.`).

```bash
opencode-config-switcher delete scratch --yes
```

### `import`: import configurations as profiles

- `--all-legacy`: import every legacy configuration file found in `~/.config/opencode/`
- `--source PATH` (repeatable): import specific files; `--name NAME` names a single-source import
- `--current [NAME]`: capture the live `~/.omo/omo.jsonc` as a profile (default name `current`)
- `--force`: overwrite an existing profile of the same name

On a TTY without `--all-legacy` or `--name`, an interactive chooser lists importable files (`Import 1-<n>, a for all, or q:`). Each success prints `Imported profile: <name> (from <file>)`. Legacy files are only read, never modified.

```bash
opencode-config-switcher import --all-legacy
```

### `replace-model`: replace a model ID in stored profiles

Replaces exact model ID matches in agent primaries, fallback chains, category `models` lists, and the models catalog. Exactly one of `--profile NAME` or `--all` is required; `--dry-run` previews every matching route without writing:

    Would replace in profile 'work':
      [opencode].agents.sisyphus.model
      [opencode].categories.planner.models[0]

An apply reports `Replaced <n> model reference(s) in profile '<name>'` (with `; re-rendered active configuration` appended when the active profile was touched); `--all` finishes with a `Replaced in <k>/<m> profile(s)` summary.

```bash
opencode-config-switcher replace-model acme/old-model acme/new-model --all --dry-run
```

### `migrate`: convert stored profiles to the canonical models format

Rewrites legacy `fallback_models` routes into canonical `models` chains in stored profiles. Called bare, it migrates every stored profile; `--profile NAME` targets a single one. `--dry-run` previews each conversion without writing:

    Would migrate profile 'work' (3 route(s))

An apply reports `Migrated profile '<name>' (<n> route(s))`. Profiles already in canonical form are never rewritten (no `.BAK`, no reformatting) and report `No migration needed for profile '<name>'`. Every actual write backs up the previous bytes to `<name>.jsonc.BAK` and preserves leading comments. When the migrated profile is the active one, `~/.omo/omo.jsonc` is re-rendered automatically, appending `; re-rendered active configuration` to that profile's message; the bare (all-profiles) run finishes with a `Migrated <k>/<m> profile(s)` summary (`Would migrate <k>/<m> profile(s)` with `--dry-run`).

Exit codes: 0 on success or no-op, 2 for an unknown profile or usage error, 1 for an invalid profile (reported as `<name>: INVALID: <error>` on stderr; other profiles still migrate).

```bash
opencode-config-switcher migrate
opencode-config-switcher migrate --profile work --dry-run
```

## The TUI

### SELECTOR (bare invocation)

Running `opencode-config-switcher` with no subcommand opens the full-screen profile selector. The layout adapts to the terminal: WIDE shows menu and details side by side, NARROW alternates panes with Tab, TOO SMALL only offers quitting.

| Context | Key | Action |
|---------|-----|--------|
| **WIDE** (100+ cols, 18+ rows) | Up / Down | Select profile |
| | PageUp / PageDown | Scroll details panel |
| | `d` | Toggle raw JSON / structured details |
| | Enter | Apply selected profile and exit |
| **NARROW** (Menu pane) | Up / Down | Select profile |
| | Tab | Switch to Details pane |
| | `d` | Toggle raw view (switches to Details) |
| | Enter | Apply selected profile and exit |
| **NARROW** (Details pane) | Up / Down / PageUp / PageDown | Scroll details |
| | Tab | Switch back to Menu pane |
| | `d` | Toggle raw JSON / structured details |
| | Enter | Apply selected profile and exit |
| **TOO SMALL** (<40 cols or <12 rows) | q / Ctrl-C / Ctrl-D | Quit (all other keys ignored) |
| **All layouts** | q / Ctrl-C / Ctrl-D | Quit without changes |
| | `n` | New profile (inline name prompt) |
| | `D` | Delete selected profile (`y`/`N` inline prompt) |
| | `e` | Edit selected profile in the editor |
| | `i` | Open the legacy import screen |
| | `r` | Open the replace-model form |
| **While typing a prompt** | printable keys | Type the name (q, D, n, d are characters here) |
| | Backspace | Delete the last character |
| | Enter | Submit the prompt |
| | Esc / Ctrl-C / Ctrl-D | Cancel the prompt |

After an apply, the TUI exits and the CLI prints `Profile applied:` and `Backup saved to:`. Blocked applies keep the selector open with the engine message in the footer.

Two in-selector modals:

- **Replace form (`r`)**: OLD and NEW model fields plus an "apply to all profiles" checkbox (Space toggles), with a live dry-run preview of matching routes. Tab cycles fields, Enter advances (the Apply row runs the replace), Esc cancels with zero writes.
- **Import screen (`i`)**: lists legacy files with ` [invalid]` markers. Space or Enter toggles a row, `a` selects all, Enter on the action row imports the selection.

### EDITOR (`e` from the selector, or `edit NAME`)

The editor opens on a route list (agents and categories with their model chains) and drills down to individual entries.

| Screen | Key | Action |
|--------|-----|--------|
| **Route list** | Up / Down | Select agent/category route |
| | Enter | Open the route's model chain |
| | `a` | Add a route (name prompt) |
| | `x` | Delete the route (asks for confirmation) |
| | `R` | Rename the route (prompt) |
| | `s` | Open the settings form |
| | `d` | Open the raw JSON view |
| | `S` | Save the profile |
| **Route editor** | Up / Down | Select a chain entry |
| | Enter | Edit the entry in the model-entry form |
| | `a` | Add a new entry (form opens blank) |
| | `x` | Delete the entry (asks for confirmation) |
| | `,` | Move the entry one slot down |
| | `.` | Move the entry one slot up |
| | `d` | Open the raw JSON view |
| | `S` | Save the profile |
| | Esc | Back to the route list |
| **Confirm** | `y` | Confirm the pending action |
| | `n` / Esc | Decline |
| **Raw view** | Up / Down | Scroll one line |
| | PageUp / PageDown | Scroll ten lines |
| | `d` / `q` / Esc | Close |
| **Global** | q / Ctrl-C | Quit; with unsaved changes a `y`/`n` confirmation appears first |

### FORMS (model-entry and settings)

Two form screens share one key grammar:

- **Model-entry form** fields, in order: `model` (text), `reasoning` (enum), `temperature`, `top_p`, `max_tokens` (numbers). Keys the form does not edit (such as `provider_options`) are preserved on save.
- **Settings form** fields: `model_fallback` (toggle), `enabled` (toggle), `retry_on_errors` (comma-separated text), `max_fallback_attempts`, `cooldown_seconds` (numbers).

| Key | Action |
|-----|--------|
| Up / Down | Move between fields |
| Space | Cycle a toggle (true, false, unset) |
| Left / Right | Cycle the `reasoning` enum (unset, off, minimal, low, medium, high, xhigh, max, auto) |
| Enter | Next field; saves on the last field |
| `S` | Save |
| Esc | Close the form without saving |

Validation on save: `temperature` must be within 0..2, `top_p` within 0..1, `max_tokens` a positive integer, `retry_on_errors` comma-separated integers. Blank numeric fields are simply left unset.

## Plain Mode (piped / non-TTY)

Without a TTY the bare invocation prints a numbered list and reads one input line:

    Available profiles:
      1) default [active]
      2) work
    Select 1-2 or q: 2
    Profile applied: work
    Backup saved to: /home/you/.omo/omo.jsonc.BAK

- `q` or EOF: prints `Exiting without changes`, exit 0
- Valid number: apply, prints `Profile applied:` and `Backup saved to:`, exit 0
- Applying the already-active unchanged profile is a no-op (message only), exit 0
- Invalid or out-of-range input: error to stderr, exit 2
- Blocked apply (invalid profile, write failure): error to stderr, exit 2 or 1

With an empty store and no TTY, the tool prints `No profiles found in ~/.omo/profiles` plus the `import --all-legacy` / `import --current` hint to stderr and exits 1.

## Onboarding (First Run)

On an interactive terminal with no profiles in `~/.omo/profiles/`, the first bare run opens a one-shot menu (also shown as a modal in the TUI):

    No profiles found.
    1) Import current ~/.omo/omo.jsonc as profile 'current'
    2) Import legacy configuration files
    3) Skip
    Choose 1-3 or q:

Option 1 is offered only when `~/.omo/omo.jsonc` already exists, option 2 only when legacy files are discovered in `~/.config/opencode/`; unavailable options are omitted without renumbering (a legacy-only machine shows `Choose 2-3 or q:`). When no source exists at all, the tool says so on stderr and exits 1. After importing, the normal selector starts; skipping or quitting leaves the store untouched (`Exiting without changes`).

## Store, Backup, and Drift Semantics

1. **Store**: Profiles are `~/.omo/profiles/<name>.jsonc` files. The active profile name is recorded in `~/.omo/profiles/.active`.
2. **Apply**: Before writing, an existing `~/.omo/omo.jsonc` is copied to `~/.omo/omo.jsonc.BAK` (a single generation, overwritten on every apply). The rendered document keeps every live section the profile does not define, always keeps `profiles` and `_migrations` from the live file, and always writes `$schema` first.
3. **Resilience**: A missing or corrupt live `omo.jsonc` never blocks an apply; rendering starts fresh and the previous bytes still land in the `.BAK`.
4. **Profile backups**: Overwriting or deleting a profile creates or replaces `<name>.jsonc.BAK` beside it (delete renames the profile to its `.BAK`).
5. **Drift detection**: `list`, `active`, and the selector badges compare the live file against the active profile (ignoring `$schema`, control keys, and keys the profile never defined). Matching content shows ACTIVE (` [active]`); manual edits show CUSTOM (` [custom]`); re-applying the profile restores ACTIVE.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success, clean quit, or no-op |
| 1 | Runtime failure: empty profile store, write errors, `edit` without a TTY |
| 2 | Usage error or invalid input: argparse errors, unknown profile, invalid selection, blocked apply |

## Migration (v2.x to v3.0)

v2 switched files inside `~/.config/opencode/` by copying one `oh-my-*.json` preset over the active one; the module implementing that behavior, `switching.py`, was removed in v3. v3 replaces the model: named profiles are stored under `~/.omo/profiles/` and rendered into `~/.omo/omo.jsonc`.

- **Bring your presets along**: `import --all-legacy` converts every legacy file into a profile (the interactive chooser and first-run onboarding do the same per file). Imports transform v2 shapes (`variant`, `fallback_models`, category `model` fields) into the v3 document format and never modify the source files.
- **`~/.config/opencode/` is read-only** for this tool from now on; it is only consulted as an import source.
- **Upstream relationship**: oh-my-opencode's own `omo config migrate` command migrates a single configuration file. This tool complements it with profile management, switching, and model maintenance on top of the migrated `omo.jsonc`; it does not replace it.
- **v3.1 canonical chains**: Agent chains are now stored canonically, matching oh-my-openagent's `models` format. The `migrate` subcommand converts pre-3.1 profile stores in place (see `migrate` above); profiles written by this version are already canonical.
- The legacy `switch_oh-my-opencode_config.py` command keeps working as an alias of the same CLI.

## Version History

- **v3.1.0** (Current):
  - Canonical `models` chains: agent routes stored in oh-my-openagent's `models` format across engine, editor, TUI, and import paths
  - `migrate` subcommand: convert pre-3.1 profile stores (bare = all profiles, `--profile NAME`, `--dry-run` previews)
  - Leading-comment preservation on every profile write (editor save, replace-model, migrate, import)
  - `replace-model` scans canonical agent `models` chains in addition to legacy `fallback_models`

- **v3.0.0**:
  - Profile-based architecture: named profiles in `~/.omo/profiles/`, render-on-select into `~/.omo/omo.jsonc`
  - Subcommand CLI: `list`, `show`, `active`, `use` (alias `select`), `create`, `edit`, `delete`, `import`, `replace-model`
  - Curses selector with `n`/`D`/`e`/`i`/`r` actions, plus a profile editor with route lists, model-entry forms, and a settings form
  - `replace-model` with per-profile or `--all` targeting and `--dry-run` previews
  - First-run onboarding: capture the current config, import legacy files, or skip
  - Single-generation `.BAK` backups, `.active` marker, and drift detection (ACTIVE / CUSTOM)
  - Non-TTY plain mode with numbered selection and documented exit codes (0/1/2)
  - v2 file switching (`switching.py`) removed; legacy presets importable via `import --all-legacy`

- **v2.0.0**:
  - Full-screen `curses` TUI with structured Details panel showing all agent/category models, fallback chains, and configuration metadata
  - Responsive WIDE/NARROW/TOO_SMALL layout with resize handling
  - Structured parsing of `agents`, `categories`, `runtime_fallback`, and `model_fallback` fields
  - Invalid JSON detection and apply blocking with inline error display
  - Raw JSON overlay accessible with `d` key
  - Python 3.11+ required, packaged with `pyproject.toml` and console-script entry points
  - Non-TTY plain mode with documented exit codes (0/1/2)
  - `pipx`-first installer with `pip --user` fallback and PEP 668 guidance
  - Migration: legacy `switch_oh-my-opencode_config.py` command preserved as generated alias

- **v1.2.0**:
  - Arrow-key navigation with inverted-line highlight
  - Enter applies the highlighted configuration immediately
  - Added support for `oh-my-openagent*.json` naming convention

- **v1.1.0**:
  - Improved version management

- **v1.0.0**:
  - Initial release

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit:

1. **Bug Reports**: Report any issues with the script
2. **Feature Requests**: Suggest new functionality
3. **Pull Requests**: Submit code improvements
4. **Documentation**: Help improve the documentation

### Development

To contribute to this project:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make your changes
4. Test thoroughly:

```bash
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
```

5. Verify the installer still parses:

```bash
bash -n setup.sh
```

6. Commit your changes: `git commit -m 'feat: Add amazing feature'`
7. Push to the branch: `git push origin feature/amazing-feature`
8. Open a Pull Request

## Support

If you encounter any issues or have questions:

1. Review the oh-my-openagent / oh-my-opencode documentation
2. Check the exit codes and backup semantics above
3. Open an issue in the project repository
