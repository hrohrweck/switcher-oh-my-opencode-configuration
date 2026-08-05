# Full-Screen Configuration Selector TUI

## TL;DR
> **Summary**: Replace the single-file ANSI selector with a Python 3.11+ `curses` application that fills the terminal, keeps configuration selection on the left, and continuously renders a structured model/fallback summary on the right. Package the tool with standard console entry points while preserving apply/backup behavior and repairing the dead non-TTY path.
> **Deliverables**:
> - `src/opencode_config_switcher/` package with config, switching, TUI, CLI, and module entry-point boundaries
> - Responsive wide/narrow full-screen interface with structured Details and raw-JSON overlay
> - Dependency-free `unittest` and PTY regression coverage
> - `pyproject.toml`, pipx-aware installer, legacy command alias, and 2.0.0 migration documentation
> **Effort**: Large
> **Parallel**: YES - 6 waves with isolated worktrees and serialized integration
> **Critical Path**: Wave 1 → Wave 2 → Task 7 → Task 8 → Wave 5 → Task 11

## Context

### Original Request
The configuration selector should use all available screen space with a more polished TUI. A panel to the right of the selection menu should show the primary and fallback models used by each configuration for the various agents, together with other relevant configuration-file information.

### Interview Summary
- Use Python standard-library `curses`, not Textual or a custom ANSI renderer.
- Refactor into a small package instead of extending the 541-line script.
- Adopt Python 3.11+, standard `pyproject.toml` packaging, and console-script installation.
- Render a structured full summary: file/current/validity status, schema, global/runtime fallback policy, additional top-level settings, Agents, and Categories.
- Show every Agent followed by every Category in source-file order, including primary model, optional variant/reasoning metadata, and ordered fallback chains.
- Use split panes at 100+ columns and 18+ rows; otherwise use a full-width Menu/Details pane switched with `Tab`. Below 40 columns or 12 rows, show a resize notice while retaining quit handling.
- Preserve immediate apply-and-exit behavior. Invalid JSON/read failures stay visible but cannot be applied.
- Preserve the raw JSON detail capability through a `d` overlay.
- Establish standard-library `unittest` TDD plus real PTY QA.
- Preserve and repair non-TTY operation as a one-shot plain numbered selector.

### Metis Review (gaps addressed)
- Reclassified the non-TTY parser as new behavior: existing numeric parsing is dead code after `sys.exit(0)` (`switch_oh-my-opencode_config.py:503-536`).
- Preserved effective alphabetical menu ordering but fixed current-config detection to use path equality rather than index (`switch_oh-my-opencode_config.py:203-222`, `switch_oh-my-opencode_config.py:369-392`).
- Explicitly preserved `copy2`, single `.BAK`, apply/no-op exit behavior, and bare version output.
- Added pipx-first installation and PEP 668 failure guidance while retaining the user-approved Python 3.11+ floor.
- Added locale, color/monochrome, display-width, clipping, signal cleanup, overlay, minimum-size, and `TERM` guardrails.
- Defined validity as readable JSON only; no JSON-Schema validation, live reload, backup redesign, or unrelated CLI expansion.
- Verified the upstream schema permits fallback values as a string, arrays of strings, arrays of objects, and mixed arrays; parsing must normalize these shapes without importing a schema validator.

## Work Objectives

### Core Objective
Deliver a safe, responsive full-screen configuration selector whose highlighted item always has an immediately readable model-routing summary, without regressing configuration discovery, apply, backup, quit, version, legacy filename, or Unix terminal-cleanup behavior.

### Deliverables
- Python package under `src/opencode_config_switcher/`:
  - `__init__.py`: single-source version `2.0.0`
  - `__main__.py`: `python -m opencode_config_switcher`
  - `config.py`: paths, discovery, JSON normalization, file/config summaries
  - `switching.py`: no-op, backup, and apply result service
  - `tui.py`: layout/state, formatting, curses renderer/controller, raw overlay
  - `cli.py`: `--version`, preflight, TTY dispatch, plain selector, process exit/output
- `pyproject.toml` with setuptools `src` discovery and both console commands.
- `tests/` with sanitized fixtures, unit tests, PTY harness, real TUI flows, and installation smoke tests.
- Rewritten `setup.sh` and updated `README.md` for pipx/pip-user installation and 2.0.0 migration.
- Removal of the obsolete root implementation after its behavior is covered by package tests; compatibility is retained by the legacy console-script name.

### Definition of Done (verifiable conditions with commands)
- `python3.11 -m unittest discover -s tests -v` exits 0.
- `PYTHONPATH=src python3.11 -m opencode_config_switcher --version` prints exactly `2.0.0` and exits 0.
- `bash -n setup.sh` exits 0.
- A temporary Python 3.11 virtual environment can run `python -m pip install --no-build-isolation --no-deps .`, after which `opencode-config-switcher --version`, `switch_oh-my-opencode_config.py --version`, and `python -m opencode_config_switcher --version` all print exactly `2.0.0`.
- PTY tests prove wide split, narrow pane switching, minimum-size warning, live resize, raw overlay, valid apply/no-op, invalid blocking, Ctrl-C/q cleanup, and `LC_ALL=C` rendering.
- An AST-based test proves third-party runtime imports are absent; `cli.py` decides exit codes and owns user-facing stream output, while `__main__.py` may only raise `SystemExit(main())`.
- `README.md` documents Python 3.11+, Unix-like scope, both commands, new keys/layout, plain-mode exit codes, installation/migration/uninstall, and version 2.0.0.

### Must Have
- Full-screen terminal lifecycle through `curses.wrapper()`.
- Wide and narrow layouts with deterministic thresholds and preserved/clamped state on resize.
- Details for all Agents and Categories in source order.
- Primary model, optional `variant`, `reasoning`, or `reasoningEffort`, and normalized ordered fallback entries.
- Generic Additional Settings summary for top-level keys not consumed by dedicated sections; scalar values display directly, lists show count plus clipped items, and objects show compact clipped JSON.
- Non-fatal invalid/unreadable config records and blocked writes.
- Safe monochrome/limited-terminal fallback and terminal restoration.
- Exact legacy discovery/apply/backup/version contracts unless this plan explicitly changes them.

### Must NOT Have
- No Textual, Rich, pytest, pexpect, wcwidth, JSON-Schema, or other third-party runtime/test dependency.
- No new CLI flags beyond `--version`; no config editing, file watching, mouse input, theme system, or search/filter feature.
- No timestamped/multi-generation backup or atomic-write redesign; preserve `shutil.copy2` and one `.BAK` generation.
- No JSON-Schema validation: `VALID` means readable syntactically valid JSON; malformed model entries produce warnings but do not block an otherwise valid JSON file.
- No CI workflow, Homebrew formula, PyPI release automation, or unrelated repository cleanup.
- No user-local configuration copied into the repository; fixtures must be sanitized synthetic representations of observed schema shapes.
- No `sys.exit()` or user-facing `print()` in `config.py`, `switching.py`, or `tui.py`; `cli.py` decides exit codes and terminal/plain output, and `__main__.py` is limited to `raise SystemExit(main())`.

## Verification Strategy
> ZERO HUMAN INTERVENTION - all verification is agent-executed.

- **Executor interpreter preflight**: At the start of every executor/integration shell, run `export PATH="$(mise where python@3.11.15)/bin:$PATH"` followed by `python3.11 --version`; require exact output `Python 3.11.15` before dispatch. The verified workspace path is `/Users/hrohrweck/.local/share/mise/installs/python/3.11.15`. If mise cannot resolve that toolchain, stop rather than silently using another Python.
- **Test decision**: TDD with Python standard-library `unittest`; implementation tasks add their direct tests in the same commit.
- **Unit seams**: `discover_configs`, `parse_config`, fallback normalization, `apply_config`, `compute_layout`, `display_width`, `truncate_display`, details/menu formatting, state transitions, and plain-selector input.
- **Hermetic filesystem**: every config/apply test uses `TemporaryDirectory` and a temporary HOME/config tree.
- **Terminal integration**: stdlib `pty`/`fcntl`/`termios` harness sets window size with `TIOCSWINSZ`, sends keys/signals, captures raw output, and asserts alternate-screen restoration.
- **Package integration**: temporary virtual environment installs the local project without dependencies and verifies all entry paths.
- **Evidence**: `.omo/evidence/task-{N}-{slug}.{ext}` for every task and final review.

## Execution Strategy

### Parallel Execution Waves
> The package has unavoidable contract dependencies; waves favor non-overlapping file ownership over artificial task count. Tasks in each wave can run concurrently.

**Wave 1 — Contracts and independent foundations (3 tasks)**
- Task 1: package/version/entry-point metadata (`quick`)
- Task 2: configuration summary/discovery parser (`deep`)
- Task 3: stdlib PTY harness (`unspecified-high`)

**Wave 2 — Services and pure presentation (3 tasks, after Wave 1 contracts)**
- Task 4: apply/backup service (`unspecified-low`)
- Task 5: pure TUI layout/state/details formatting (`deep`)
- Task 6: pipx/PEP-668-aware installer (`unspecified-high`)

**Wave 3 — Curses runtime (1 task)**
- Task 7: curses renderer/controller and raw overlay (`visual-engineering`)

**Wave 4 — CLI runtime (1 task)**
- Task 8: CLI dispatch and one-shot plain selector (`unspecified-high`)

**Wave 5 — Cross-cutting executable verification (2 tasks)**
- Task 9: real PTY behavior suite (`unspecified-high`)
- Task 10: installed-command and migration compatibility (`quick`)

**Wave 6 — Documentation and release contract (1 task)**
- Task 11: README/version/migration documentation (`writing`)

**Parallel Git Isolation and Integration**
- Before dispatching any wave with more than one task, create one isolated worktree per task using `superpowers:using-git-worktrees`; each agent owns only its listed files and commits inside that worktree.
- After a wave completes, the integration owner serially cherry-picks task commits in ascending task order into the main execution branch, resolves only integration conflicts, and runs `python3.11 -m unittest discover -s tests -v` before starting the next wave.
- Worker-worktree evidence is diagnostic only and is not committed. After each cherry-pick, the integration owner reruns that task's two listed QA scenarios on the integrated branch and writes the canonical `.omo/evidence/task-{N}-*` files there before starting the next cherry-pick/task wave.
- Agents must never share a Git index/HEAD or commit concurrently in the same worktree. Single-task Waves 3, 4, and 6 run directly on the integrated branch.

### Dependency Matrix

| Task | Blocked By | Blocks |
|---|---|---|
| 1 | — | 6, 8, 10, 11 |
| 2 | — | 4, 5, 7, 8, 9, 11 |
| 3 | — | 9 |
| 4 | 2 | 7, 8, 9, 11 |
| 5 | 2 | 7, 9, 11 |
| 6 | 1 | 10, 11 |
| 7 | 2, 4, 5 | 8, 9, 11 |
| 8 | 1, 2, 4, 7 | 9, 10, 11 |
| 9 | 3, 4, 5, 7, 8 | 11 |
| 10 | 1, 6, 8 | 11 |
| 11 | 1-10 | Final Verification |

### Agent Dispatch Summary

| Wave | Task Count | Categories |
|---|---:|---|
| 1 | 3 | `quick`, `deep`, `unspecified-high` |
| 2 | 3 | `unspecified-low`, `deep`, `unspecified-high` |
| 3 | 1 | `visual-engineering` |
| 4 | 1 | `unspecified-high` |
| 5 | 2 | `unspecified-high`, `quick` |
| 6 | 1 | `writing` |
| Final | 4 | `oracle`, `unspecified-high`, `unspecified-high`, `deep` |

## TODOs
> Implementation + direct tests are one task. Every task is incomplete until its QA scenarios and evidence pass.

- [x] 1. Establish package metadata, version, and entry-point contracts

  **What to do**:
  - Start with failing `unittest` assertions for package name, version, Python floor, empty runtime dependencies, `src` discovery, and both console-script mappings.
  - Add `pyproject.toml` using `setuptools.build_meta`, `setuptools>=61`, and exact PEP 621 metadata: `name = "opencode-config-switcher"`, `description = "Full-screen TUI for switching oh-my-openagent and oh-my-opencode configurations"`, `readme = "README.md"`, `license = {file = "LICENSE"}`, `requires-python = ">=3.11"`, no `[project].dependencies`, and `[tool.setuptools.packages.find] where = ["src"]`.
  - Single-source `2.0.0` as `opencode_config_switcher.__version__`; expose it through setuptools dynamic version metadata.
  - Map `opencode-config-switcher` and `switch_oh-my-opencode_config.py` to `opencode_config_switcher.cli:main`.
  - Add an import-safe `__init__.py` and `src/opencode_config_switcher/__main__.py` containing only the import plus `raise SystemExit(main())`; command execution becomes green only after Task 8, but metadata/version tests must pass here.

  **Must NOT do**:
  - Do not add runtime dependencies, publish configuration, CI, or a replacement CLI implementation.
  - Do not define a second production version source in `pyproject.toml` or installer logic. Tests may assert the expected literal `2.0.0` but must import/read the production source under test.

  **Recommended Agent Profile**:
  - Category: `quick` - Small, bounded packaging contract.
  - Skills: [`git-master`] - Keep package metadata and its direct tests in one atomic semantic commit.
  - Omitted: [`frontend-ui-ux`, `visual-qa`] - No terminal rendering exists in this task.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [6, 8, 10, 11] | Blocked By: []

  **References**:
  - Pattern: `switch_oh-my-opencode_config.py:58-69` - Current duplicated version and executable naming to replace with package metadata.
  - Pattern: `README.md:15-19` - Existing Python/platform contract that Task 11 will migrate.
  - Pattern: `setup.sh:18-25` - Current stale installer version and single-file source assumptions.
  - External: https://github.com/pypa/setuptools/blob/main/docs/userguide/pyproject_config.rst - PEP 621 metadata and `[project.scripts]` syntax.
  - External: https://github.com/pypa/setuptools/blob/main/docs/userguide/package_discovery.rst - `src` package discovery.

  **Acceptance Criteria**:
  - [ ] `PYTHONPATH=src python3.11 -m unittest tests.test_package_metadata -v` exits 0.
  - [ ] `PYTHONPATH=src python3.11 -c 'import opencode_config_switcher as p; assert p.__version__ == "2.0.0"'` exits 0.
  - [ ] A `tomllib` assertion proves the exact name/description/readme/license/Python fields above, `dependencies` is absent/empty, both script mappings target `opencode_config_switcher.cli:main`, build requirement is `setuptools>=61`, and package discovery points to `src`.
  - [ ] No file other than `src/opencode_config_switcher/__init__.py` contains a literal authoritative assignment for version `2.0.0`.

  **QA Scenarios**:
  ```
  Scenario: Package metadata exposes both commands and one version source
    Tool: Bash
    Steps: Run `PYTHONPATH=src python3.11 -m unittest tests.test_package_metadata -v`; save stdout/stderr.
    Expected: All metadata tests pass and report both console entry names mapped to `cli:main`.
    Evidence: .omo/evidence/task-1-package-metadata.txt

  Scenario: Unsupported Python requirement is encoded before installation
    Tool: Bash
    Steps: Run a Python 3.11 `tomllib` check that parses `pyproject.toml` and asserts `project.requires-python` is exactly `>=3.11`; intentionally evaluate a `3.10` version against the requirement in the test helper.
    Expected: Metadata rejects the synthetic 3.10 candidate and accepts 3.11 without invoking pip or the network.
    Evidence: .omo/evidence/task-1-python-floor.txt
  ```

  **Commit**: YES | Message: `build: add Python package metadata` | Files: `pyproject.toml`, `src/opencode_config_switcher/__init__.py`, `src/opencode_config_switcher/__main__.py`, `tests/test_package_metadata.py`

- [x] 2. Build ordered configuration discovery and summary parsing with TDD

  **What to do**:
  - Create sanitized fixtures for: full runtime/agent/category data, sparse optional fields, fallback string, fallback string array, fallback object array, mixed fallback array, malformed fallback entries, trailing-comma JSON, Unicode/CJK names, and both legacy/current filename conventions.
  - Define these exact immutable contracts in `config.py`:
    - `ModelSpec(model: str | None, variant: str | None, reasoning: str | None, reasoning_effort: str | None)`.
    - `RouteSummary(name: str, primary: ModelSpec, fallbacks: tuple[ModelSpec, ...], warnings: tuple[str, ...])`.
    - `RuntimeFallbackSummary(enabled: bool | None, retry_on_errors: tuple[int, ...], max_fallback_attempts: int | None, cooldown_seconds: int | float | None, additional: tuple[tuple[str, object], ...])`; malformed runtime values move to `ConfigSummary.warnings` rather than being coerced.
    - `FileSummary(path: Path, name: str, size_bytes: int | None, modified_ns: int | None, is_current: bool, raw_text: str | None)`.
    - `ConfigSummary(file: FileSummary, is_valid: bool, error: str | None, schema: str | None, model_fallback: bool | None, runtime_fallback: RuntimeFallbackSummary, agents: tuple[RouteSummary, ...], categories: tuple[RouteSummary, ...], additional_settings: tuple[tuple[str, object], ...], warnings: tuple[str, ...])`.
  - Normalize `fallback_models` from all upstream-supported shapes: one string, list of strings, list of objects, or mixed list. Preserve array order and capture `model`, optional `variant`, `reasoning`, and `reasoningEffort`; malformed entries become precise warnings.
  - Parse dedicated `$schema`, `model_fallback`, and `runtime_fallback` fields. Build Additional Settings from every remaining top-level key except `agents`/`categories`; scalars remain literal, lists retain count/items, and objects retain data for compact deterministic JSON formatting in Task 5.
  - Keep a JSON-valid config applyable even when model entries warn; unreadable/invalid JSON produces a visible invalid summary with exact exception text and no parsed routes.
  - Use exact diagnostics: JSON parse error `Invalid JSON in {name} at line {line}, column {column}: {message}`; read error `Cannot read {name}: {ExceptionClass}: {message}`; malformed entry warning `{section}.{route}.fallback_models[{index}]: expected a string or an object containing model`. A valid non-object top level remains JSON-valid but adds warning `Top-level JSON value is {type}; expected object` and has empty structured sections.
  - Preserve active-path preference (`oh-my-openagent.json` if present, otherwise legacy `oh-my-opencode.json`), both glob conventions, canonical active inclusion, name deduplication, and effective alphabetical menu order. Mark current by resolved path equality, never list index.
  - Cache all summaries and the source's decoded raw text during discovery; no renderer or raw overlay may reopen configuration files.

  **Must NOT do**:
  - Do not copy real files from `~/.config/opencode` into tests.
  - Do not validate against the remote JSON Schema or reject syntactically valid JSON because an optional route is malformed.
  - Do not call `sys.exit`, print user output, mutate configuration files, reorder agent/category dictionaries, or add file watching.

  **Recommended Agent Profile**:
  - Category: `deep` - Multiple legacy/schema shapes and error contracts require careful normalization.
  - Skills: [`superpowers:test-driven-development`] - Lock discovery and parse behavior before extraction.
  - Omitted: [`frontend-ui-ux`] - This task creates data contracts, not presentation.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [4, 5, 7, 8, 9, 11] | Blocked By: []

  **References**:
  - Pattern: `switch_oh-my-opencode_config.py:61-89` - Active and backup path preference.
  - Pattern: `switch_oh-my-opencode_config.py:194-222` - Existing discovery/glob/dedup/sort semantics.
  - Pattern: `switch_oh-my-opencode_config.py:225-249` - Current JSON parse and terminal-size coupling to separate.
  - Pattern: `switch_oh-my-opencode_config.py:344-349` - Existing JSON/read error messages to preserve semantically as summary errors.
  - External: https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/oh-my-opencode.schema.json - Observed current fallback and route field shapes; reference only, not a runtime validator.

  **Acceptance Criteria**:
  - [ ] `PYTHONPATH=src python3.11 -m unittest tests.test_config -v` exits 0.
  - [ ] Tests prove alphabetical config ordering and correct `is_current` path equality when the active file is not first.
  - [ ] Tests prove Agents and Categories retain JSON insertion order and all supported fallback shapes normalize to ordered model specs.
  - [ ] Trailing-comma/read-failure fixtures remain discoverable, contain exact useful errors, and have `is_valid == False`.
  - [ ] Missing optional fields render as absent data without warnings; malformed route/fallback entries add warnings but preserve `is_valid == True`.
  - [ ] A test patches file open after discovery and proves summary consumers use cached data without rereading.

  **QA Scenarios**:
  ```
  Scenario: Full configuration becomes an ordered structured summary
    Tool: Bash
    Steps: Run the focused unittest that discovers a temporary active file plus alphabetically earlier presets, then inspect the serialized test assertion output.
    Expected: Menu order is alphabetical; only the resolved active path has CURRENT; agents/categories and mixed fallback chains match fixture order and metadata.
    Evidence: .omo/evidence/task-2-config-summary.txt

  Scenario: Invalid and malformed configurations degrade independently
    Tool: Bash
    Steps: Run focused tests for trailing-comma JSON, unreadable path, and a valid JSON file containing malformed fallback entries.
    Expected: First two records are INVALID with exact errors and no routes; the third remains valid with inline warning data and normalized valid fallbacks.
    Evidence: .omo/evidence/task-2-config-errors.txt
  ```

  **Commit**: YES | Message: `feat(config): parse configuration summaries` | Files: `src/opencode_config_switcher/config.py`, `tests/test_config.py`, `tests/fixtures/*.json`

- [x] 3. Create a bounded standard-library PTY harness

  **What to do**:
  - Build `tests/pty_harness.py` using only `pty`, `subprocess`, `fcntl`, `termios`, `struct`, `select`, `signal`, `os`, and `time.monotonic`.
  - Expose helpers to spawn a child with controlled HOME/TERM/locale and rows/columns, send byte sequences, resize with `TIOCSWINSZ` plus `SIGWINCH`, wait for byte/text markers, collect raw output, assert exit status, and force-clean hung children.
  - Make timeouts deterministic and include captured-output tails in failures.
  - Add a small fixture probe and self-tests proving input delivery, window-size reporting, live resize, normal exit, signal exit, timeout cleanup, and no leaked child process.

  **Must NOT do**:
  - Do not add pexpect or shell out to tmux for automated tests.
  - Do not make the harness depend on the application package or mutate user terminal/config state.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - PTY, signals, and resize synchronization are low-level and timing-sensitive.
  - Skills: [`debugging`] - Use hypothesis-driven investigation if platform PTY behavior diverges.
  - Omitted: [`frontend-ui-ux`] - This task is terminal test infrastructure, not visual design.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [9] | Blocked By: []

  **References**:
  - Pattern: `switch_oh-my-opencode_config.py:15-55` - Current raw input behavior that the PTY must eventually exercise.
  - Pattern: `switch_oh-my-opencode_config.py:151-158` - Current terminal dimension lookup to supersede with controlled PTY sizing.
  - External: https://docs.python.org/3.11/library/pty.html - Standard-library PTY primitives.
  - External: https://docs.python.org/3.11/library/termios.html - Terminal attributes and ioctl constants.

  **Acceptance Criteria**:
  - [ ] `python3.11 -m unittest tests.test_pty_harness -v` exits 0 in the supported Unix-like development environment.
  - [ ] Resize self-test observes both initial and updated rows/columns from the child.
  - [ ] Timeout self-test terminates/reaps the child within the configured bound and includes output tail in the assertion message.
  - [ ] The harness leaves no child processes and never touches the controlling user terminal.

  **QA Scenarios**:
  ```
  Scenario: PTY input and live resize are observable
    Tool: Bash
    Steps: Run the harness self-test with an 80x24 probe, send a marker key, resize to 120x40, and wait for both reported dimensions.
    Expected: Probe receives the key, reports 80x24 then 120x40, exits 0, and raw transcript is retained.
    Evidence: .omo/evidence/task-3-pty-resize.bin

  Scenario: Hung child is cleaned up deterministically
    Tool: Bash
    Steps: Run the timeout self-test against a probe that never exits and set a short bounded timeout.
    Expected: Harness fails the wait predictably, terminates and reaps the child, and reports the captured output tail; no process remains.
    Evidence: .omo/evidence/task-3-pty-timeout.txt
  ```

  **Commit**: YES | Message: `test(tui): add PTY harness` | Files: `tests/pty_harness.py`, `tests/test_pty_harness.py`, `tests/fixtures/pty_probe.py`

- [x] 4. Extract the no-op, backup, validation gate, and apply service

  **What to do**:
  - Write failing tests for active no-op, valid apply with/without an existing active file, backup overwrite, invalid-source blocking, and failures during backup/source copy.
  - Implement exact contracts in `switching.py`: `ApplyStatus` string enum values `APPLIED`, `NOOP`, `BLOCKED`, `FAILED`; `ApplyStage` string enum values `NONE`, `VALIDATION`, `BACKUP`, `COPY`; and immutable `ApplyResult(status: ApplyStatus, source: Path, active: Path, backup: Path, message: str, error: str | None = None, failed_stage: ApplyStage = ApplyStage.NONE)`.
  - If source resolves to active, return no-op success without checking validity, changing mtime, creating backup, or copying bytes.
  - If source is not active and its summary is invalid/unreadable, return a blocked result and perform zero writes.
  - For valid non-active selection, preserve exact legacy semantics: if active exists, `shutil.copy2(active, backup)`; then `shutil.copy2(source, active)`; overwrite the one `.BAK` generation and retain metadata behavior.
  - Report the failed stage and exception text in a recoverable result. Do not introduce temp files, `os.replace`, rollback, or additional backup generations.
  - Use exact messages: `Configuration applied: {source.name}`; `No change: {source.name} is already active`; `Cannot apply invalid configuration: {source.name}: {summary.error}`; `Failed to create backup: {ExceptionClass}: {message}`; `Failed to apply configuration: {ExceptionClass}: {message}`.

  **Must NOT do**:
  - Do not print, exit, initialize curses, alter discovery order, or redesign copy atomicity.
  - Do not block a no-op solely because the already-active file is invalid JSON.

  **Recommended Agent Profile**:
  - Category: `unspecified-low` - Small service with safety-sensitive branch coverage.
  - Skills: [`superpowers:test-driven-development`] - Preserve copy/no-op contracts with red-green-refactor.
  - Omitted: [`frontend-ui-ux`] - No presentation work.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: [7, 8, 9, 11] | Blocked By: [2]

  **References**:
  - Pattern: `switch_oh-my-opencode_config.py:404-431` - Exact active/no-op/backup/copy/error sequence to extract.
  - Pattern: `switch_oh-my-opencode_config.py:64-89` - Active-matched backup filename contract.
  - Pattern: `README.md:108-115` - Documented backup/copy semantics.

  **Acceptance Criteria**:
  - [ ] `PYTHONPATH=src python3.11 -m unittest tests.test_switching -v` exits 0.
  - [ ] Valid apply test proves active bytes equal selected bytes and backup bytes equal prior active bytes.
  - [ ] No-op test proves active mtime/bytes are unchanged and no backup call occurs, including when active JSON is invalid.
  - [ ] Invalid non-active test proves active/backup bytes and mtimes are unchanged.
  - [ ] Mocked first/second `copy2` failures return distinct failed stages and do not escape as unhandled exceptions.

  **QA Scenarios**:
  ```
  Scenario: Valid selection preserves single-generation backup semantics
    Tool: Bash
    Steps: Run the focused tempfile test with distinct active, existing BAK, and selected bytes; apply once and compare all paths.
    Expected: BAK equals pre-apply active, active equals selected, metadata-preserving copy path is exercised, result is APPLIED.
    Evidence: .omo/evidence/task-4-switching-happy.txt

  Scenario: Invalid source and copy failure cannot crash the caller
    Tool: Bash
    Steps: Run focused tests for INVALID summary and mocked failure at backup then source-copy stages.
    Expected: Invalid is BLOCKED with zero writes; failures return FAILED with stage/error and remain catchable by CLI/TUI.
    Evidence: .omo/evidence/task-4-switching-errors.txt
  ```

  **Commit**: YES | Message: `refactor: extract configuration switching service` | Files: `src/opencode_config_switcher/switching.py`, `tests/test_switching.py`

- [x] 5. Define pure responsive layout, state transitions, and structured formatting

  **What to do**:
  - Write failing tests before adding presentation logic to `tui.py`; keep this phase free of `curses.wrapper()` and real window I/O.
  - Define deterministic layout modes:
    - `TOO_SMALL` when columns `< 40` or rows `< 12`.
    - `WIDE` when columns `>= 100` and rows `>= 18`.
    - `NARROW` otherwise.
    - Reserve 3 header rows and 2 footer rows. In WIDE, left width is `min(44, max(30, columns // 3))`, followed by one divider column; Details receives the remainder.
  - Define app state for selected index, menu offset, detail offset, active narrow pane (initially Menu), overlay state/offset, transient status, and quit/apply intents. Clamp all indices after resize/data change.
  - Encode key semantics as pure transitions: WIDE Up/Down selects and resets details; NARROW Menu Up/Down selects; NARROW Details Up/Down scrolls; PageUp/PageDown scroll Details; `Tab` switches narrow panes; `d` toggles raw overlay; overlay `q`/`d` closes; root `q`, Ctrl-C, and Ctrl-D quit; Space is ignored; Enter requests apply from WIDE or either NARROW pane. In `TOO_SMALL`, only resize plus q/Ctrl-C/Ctrl-D are handled; every other key, including Enter, is ignored.
  - Implement `display_width`/`truncate_display` with `unicodedata.combining` and East Asian width; never slice blindly by `len()`.
  - Format cached summaries into deterministic lines: file path/name, CURRENT/VALID/INVALID, size/mtime, schema, model fallback, runtime fallback fields, Agents/Category counts, Additional Settings, every named route, primary model metadata, numbered ordered fallbacks, warnings, and explicit `not configured`/`none configured` labels.
  - Format cached raw text into overlay lines without rereading; long lines truncate with an indicator and vertical scrolling only.

  **Must NOT do**:
  - Do not initialize curses, add color codes, read files, call switching services, or emit terminal output in pure helpers.
  - Do not hide/reorder routes, add horizontal scrolling, or make dimensions configurable.

  **Recommended Agent Profile**:
  - Category: `deep` - Unicode width, state transitions, clipping, and layout invariants interact.
  - Skills: [`superpowers:test-driven-development`] - Pure seams should be exhaustively locked before curses I/O.
  - Omitted: [`visual-qa`] - Real rendering comes in Tasks 7 and 9.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: [7, 9, 11] | Blocked By: [2]

  **References**:
  - Pattern: `switch_oh-my-opencode_config.py:146-181` - Existing width/line helpers whose clamping and ANSI coupling are replaced.
  - Pattern: `switch_oh-my-opencode_config.py:225-349` - Existing raw JSON pager behavior to preserve through cached overlay formatting.
  - Pattern: `switch_oh-my-opencode_config.py:352-401` - Existing row/current/backup information to represent in pure menu/details lines.
  - External: https://github.com/python/cpython/blob/v3.11.14/Lib/curses/textpad.py - `getmaxyx()` dimension convention used by curses internals.

  **Acceptance Criteria**:
  - [ ] `PYTHONPATH=src python3.11 -m unittest tests.test_tui_state tests.test_tui_formatting -v` exits 0.
  - [ ] Boundary tests cover 39x24, 40x12, 99x17, 100x18, 120x40, and resizing across all modes while preserving/clamping state.
  - [ ] State tests cover every key in root and overlay modes, selection boundary behavior, details reset on selection, ignored Space, and pane-specific Up/Down behavior.
  - [ ] TOO_SMALL state tests prove Enter/Tab/d/arrows/page keys are ignored while resize and q/Ctrl-C/Ctrl-D remain active.
  - [ ] CJK, combining-mark, emoji, and overlong model/path tests prove all generated rows fit requested display width.
  - [ ] Formatting tests prove exact section order, source-order routes, numbered fallbacks, generic additional settings, warnings, and literal missing-value labels.

  **QA Scenarios**:
  ```
  Scenario: Wide and narrow layouts preserve the same selected config
    Tool: Bash
    Steps: Run state tests starting at 120x40, select index 2, resize to 80x24, Tab to Details, scroll, then resize back to 120x40.
    Expected: Modes are WIDE→NARROW→WIDE; selection stays 2; scroll/pane state is preserved then clamped; no negative/out-of-range offsets.
    Evidence: .omo/evidence/task-5-layout-state.txt

  Scenario: Unicode and malformed route data cannot overflow formatting
    Tool: Bash
    Steps: Format the Unicode fixture and malformed mixed-fallback fixture at menu/detail widths 30 and 55; validate display widths and warning lines.
    Expected: Every line fits, valid entries retain order/metadata, malformed entries show warnings, and no exception occurs.
    Evidence: .omo/evidence/task-5-formatting-edge.txt
  ```

  **Commit**: YES | Message: `feat(tui): add layout and details models` | Files: `src/opencode_config_switcher/tui.py`, `tests/test_tui_state.py`, `tests/test_tui_formatting.py`

- [x] 6. Rewrite the installer for pipx, pip-user fallback, and PEP 668 guidance

  **What to do**:
  - Write `unittest` subprocess tests around `setup.sh` using temporary HOME/PATH and fake `python3`/`pipx` executables that record arguments and create controlled command shims.
  - Require Python 3.11+ before installation and fail with a concise detected/required version message.
  - Prefer `pipx install --force <repository-root>` when `pipx` is available.
  - Otherwise run `<python> -m pip install --user --upgrade <repository-root>`; if pip fails (including PEP 668), return nonzero and print actionable options: install/use pipx or create an isolated virtual environment. Never add `--break-system-packages` automatically.
  - Verify the installed canonical command reports `2.0.0`; detect the pipx/user bin directory for verification and emit a PATH instruction if it is not reachable.
  - Remove the shell `VERSION` constant and single-file copy/chmod logic.

  **Must NOT do**:
  - Do not download/install pipx automatically, mutate shell profiles, invoke sudo, or bypass externally-managed-environment protection.
  - Do not copy package modules manually into `~/.local/bin` or duplicate version metadata.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Installer behavior spans shell, Python detection, pipx, pip, and controlled failure output.
  - Skills: [`superpowers:test-driven-development`] - Installer branches need hermetic subprocess tests.
  - Omitted: [`git-master`] - No git history operation is needed during implementation.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: [10, 11] | Blocked By: [1]

  **References**:
  - Pattern: `setup.sh:18-25` - Stale duplicated version/source assumptions to remove.
  - Pattern: `setup.sh:55-123` - Existing preflight, PATH messaging, install, and verify flow to preserve conceptually.
  - Pattern: `README.md:21-71` - Current installation paths that Task 11 will replace.

  **Acceptance Criteria**:
  - [ ] `bash -n setup.sh` exits 0.
  - [ ] `python3.11 -m unittest tests.test_setup_script -v` exits 0.
  - [ ] Fake-pipx test records `install --force <absolute-root>`, verifies `2.0.0`, and never calls pip.
  - [ ] No-pipx test records `python -m pip install --user --upgrade <absolute-root>` and verifies the user-bin command.
  - [ ] Python 3.10 and simulated PEP 668 failures exit nonzero with exact actionable guidance and no sudo/`--break-system-packages` text.

  **QA Scenarios**:
  ```
  Scenario: pipx-first installation succeeds without touching user state
    Tool: Bash
    Steps: Run the installer unittest with temp HOME/PATH and a fake pipx that records args and installs a fake version-reporting command.
    Expected: pipx path is selected, canonical command verifies 2.0.0, test HOME contains all artifacts, and pip fallback is untouched.
    Evidence: .omo/evidence/task-6-installer-pipx.txt

  Scenario: Externally managed pip failure is actionable and safe
    Tool: Bash
    Steps: Run the fallback test with no pipx and fake pip returning a PEP 668 error.
    Expected: setup exits nonzero, recommends pipx/isolated venv, does not use sudo or break-system flags, and creates no command.
    Evidence: .omo/evidence/task-6-installer-pep668.txt
  ```

  **Commit**: YES | Message: `build: migrate installer to package workflow` | Files: `setup.sh`, `tests/test_setup_script.py`

- [x] 7. Implement the polished curses renderer, controller, and raw overlay

  **What to do**:
  - Add failing fake-window/controller tests, then implement a thin real-curses shell over Task 5's pure layout/state/formatting contracts.
  - Define exact TUI contracts: `TuiOutcome` string enum values `QUIT`, `APPLIED`, `NOOP`, `TERMINATED`, `FATAL`; immutable `TuiResult(outcome: TuiOutcome, apply_result: ApplyResult | None = None, error_type: str | None = None, error_message: str | None = None, signal_number: int | None = None)`. Expose `run_tui(configs, apply_service) -> TuiResult`; it must not print or exit. Internally run through `curses.wrapper()` and return only after terminal restoration.
  - Before curses initialization, apply `locale.setlocale(LC_ALL, "")`. Use `keypad(True)`, blocking input, best-effort `curs_set(0)`, and cached summaries/raw text. Prefer `curses.ACS_*` borders; when a capability is unavailable or an ACS border write raises `curses.error`, redraw that border with deterministic ASCII `+`, `-`, and `|`.
  - Palette when available: cyan/bold title and section accents, green CURRENT, red/bold INVALID/error, yellow fallback/warning, and `A_REVERSE` selection. Honor `NO_COLOR`; use `has_colors()`/`use_default_colors()` safely and degrade to bold/reverse monochrome.
  - Render exactly 3 header rows, content panes, and 2 dynamic footer rows. Footer keys change for wide, narrow Menu, narrow Details, overlay, too-small, and status/error states.
  - Centralize clipped `safe_addstr`/border writes; never write the lower-right cell or allow `curses.error` from normal clipping/resize to escape.
  - WIDE draws left list plus divider plus structured Details. NARROW draws one focused pane. TOO_SMALL draws current/required dimensions and accepts quit keys.
  - In the raw overlay, `d` or `q` closes; Up/Down moves one line and PageUp/PageDown one viewport. Root `q`/Ctrl-D quits; KeyboardInterrupt from Ctrl-C returns QUIT. Space is intentionally ignored.
  - On `KEY_RESIZE`, call supported curses dimension refresh APIs, recompute layout, clamp state, and redraw without dismissing overlay or losing selection.
  - Enter delegates to Task 4: BLOCKED/FAILED stays in the TUI footer; APPLIED/NOOP returns from curses so Task 8 can print the post-terminal summary.
  - Install temporary SIGHUP/SIGTERM handlers that trigger a catchable termination path so `wrapper()` restores terminal state; restore prior handlers after return. Map them to `TERMINATED` with signal number. Catch KeyboardInterrupt after wrapper cleanup as `QUIT`; convert other escaped exceptions after cleanup to `FATAL` with class/message. Do not claim cleanup for SIGKILL.

  **Must NOT do**:
  - Do not reopen configs, implement horizontal scrolling, print success text inside the alternate screen, swallow arbitrary programming exceptions, or access process exit APIs.
  - Do not add custom themes, mouse support, search/filter, editing, or non-curses ANSI rendering.

  **Recommended Agent Profile**:
  - Category: `visual-engineering` - Full-screen TUI hierarchy, polish, clipping, and interaction fidelity.
  - Skills: [`frontend-ui-ux`, `superpowers:test-driven-development`] - Design disciplined terminal presentation and lock controller behavior first.
  - Omitted: [`visual-qa`] - Task 9 owns real terminal visual QA after runtime integration.

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: [8, 9, 11] | Blocked By: [2, 4, 5]

  **References**:
  - Pattern: `switch_oh-my-opencode_config.py:91-123` - Current colors and UTF-8 boxes to replace with curses capabilities/ACS.
  - Pattern: `switch_oh-my-opencode_config.py:225-349` - Existing raw preview navigation and errors to retain as overlay semantics.
  - Pattern: `switch_oh-my-opencode_config.py:352-403` - Existing title, commands, menu, current, and backup status content.
  - Pattern: `switch_oh-my-opencode_config.py:468-501` - Existing selection/apply/detail event loop behavior.
  - External: https://github.com/python/cpython/blob/v3.11.14/Doc/howto/curses.rst - `wrapper`, windows/pads, keypad, and color-pair guidance.

  **Acceptance Criteria**:
  - [ ] `PYTHONPATH=src python3.11 -m unittest tests.test_tui_rendering tests.test_tui_controller -v` exits 0.
  - [ ] Fake-window tests prove WIDE/NARROW/TOO_SMALL frames, dynamic footer, CURRENT/INVALID badges, complete Details, overlay open/scroll/close, and status rendering.
  - [ ] Monochrome, `NO_COLOR`, `LC_ALL=C`, unavailable/failing ACS, unsupported cursor hiding, and boundary clipping tests render without uncaught `curses.error`; failing ACS redraws exact `+ - |` borders.
  - [ ] Controller tests prove valid APPLIED/NOOP returns only after wrapper scope, while BLOCKED/FAILED remains interactive with exact status.
  - [ ] Resize tests preserve selection/overlay/pane state and clamp all offsets.
  - [ ] Signal-handler tests restore previous handlers and route SIGHUP/SIGTERM through wrapper cleanup.

  **QA Scenarios**:
  ```
  Scenario: Wide polished frame renders complete structured information
    Tool: Bash
    Steps: Run fake-screen rendering at 120x40 with full fixture, colors enabled, selected non-current config, and long fallback chains; serialize cells/attributes.
    Expected: Header/footer use reserved rows; left width is 40; selected row is reverse; CURRENT/INVALID/warning colors match palette; Details sections and fallbacks appear in order with no out-of-bounds writes.
    Evidence: .omo/evidence/task-7-wide-frame.txt

  Scenario: Resize, invalid apply, and signal cleanup remain recoverable
    Tool: Bash
    Steps: Drive controller fake from 80x24 Details overlay to 120x40, close overlay, select invalid config, press Enter, then inject SIGHUP termination.
    Expected: State survives resize; invalid apply shows footer and performs no write; wrapper cleanup path runs; original signal handlers are restored.
    Evidence: .omo/evidence/task-7-recovery.txt
  ```

  **Commit**: YES | Message: `feat(tui): implement full-screen curses selector` | Files: `src/opencode_config_switcher/tui.py`, `tests/test_tui_rendering.py`, `tests/test_tui_controller.py`

- [x] 8. Implement CLI dispatch, public output contracts, and one-shot plain mode

  **What to do**:
  - Write failing stream-injection and subprocess tests, then implement `cli.py` as the only boundary that writes user-facing stdout/stderr or chooses an exit code.
  - Keep `--version` as the only recognized behavior flag and print exactly bare `2.0.0`; preserve legacy tolerance by ignoring other argv rather than adding argparse usage/errors.
  - Resolve `~/.config/opencode`, discover/cache summaries, and handle missing directory/no configs before curses initialization with stderr and exit 1.
  - Use curses only when stdin and stdout are TTYs and `TERM` is neither empty nor `dumb`; otherwise use ANSI-free one-shot plain mode.
  - Plain mode prints exact header `Available configurations:`, rows `  {index}) {name}{markers}` where markers are ordered ` [current] [invalid]`, then prompt `Select 1-{count} or q: `. It reads one line and implements:
    - `q`, EOF, Ctrl-C/KeyboardInterrupt → exit 0, no write.
    - valid 1-based current selection → no-op message, exit 0, even if active JSON is invalid.
    - valid 1-based non-current valid selection → apply, success + backup path, exit 0.
    - non-integer/out-of-range/invalid non-current selection → exact stderr, exit 2, no reprompt/write.
    - copy/preflight/runtime failure → exact stderr, exit 1.
  - Map TUI QUIT to 0; after wrapper returns APPLIED/NOOP, print the same plain success/no-op summary and exit 0; fatal TUI error prints after restoration and exits 1.
  - Emit exact public messages:
    - Missing dir: `Configuration directory not found: {path}` to stderr, exit 1.
    - No configs: `No configuration files found in {path}` to stderr, exit 1.
    - Invalid selection: `Invalid selection: {value!r}; expected 1-{count} or q` to stderr, exit 2.
    - Invalid config: Task 4's BLOCKED message to stderr, exit 2 in plain mode.
    - Applied: Task 4's APPLIED message then `Backup saved to: {backup}` on stdout, exit 0.
    - No-op: Task 4's NOOP message on stdout, exit 0.
    - Root q/Ctrl-C/Ctrl-D: `Exiting without changes` on stdout, exit 0.
    - SIGHUP/SIGTERM: `Interrupted by signal {signal_name}` on stderr, exit `128 + signal_number` (129/143).
    - Fatal TUI exception: `TUI error: {error_type}: {error_message}` on stderr, exit 1.
  - Ensure `__main__.py` raises `SystemExit(main())` and setuptools entry points receive `main()`'s integer return.

  **Must NOT do**:
  - Do not initialize curses for missing/no-config/plain paths, emit ANSI in plain mode, reprompt piped input, add config-dir flags/environment overrides, or move stream ownership into service modules.
  - Do not change alphabetical numbering between TUI and plain mode.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Coordinates public compatibility, streams, exit codes, TTY dispatch, and services.
  - Skills: [`superpowers:test-driven-development`] - Public CLI behavior must be locked before implementation.
  - Omitted: [`frontend-ui-ux`] - Task 7 owns visual rendering.

  **Parallelization**: Can Parallel: NO | Wave 4 | Blocks: [9, 10, 11] | Blocked By: [1, 2, 4, 7]

  **References**:
  - Pattern: `switch_oh-my-opencode_config.py:444-467` - Current version, directory, and empty-list preflight behavior.
  - Pattern: `switch_oh-my-opencode_config.py:468-501` - Current TTY apply/no-op/quit outcomes and post-apply messages.
  - Pattern: `switch_oh-my-opencode_config.py:503-536` - Intended but dead non-TTY number path; replace with the explicit one-shot contract.
  - Pattern: `README.md:87-106` - Public version/key contracts Task 11 will update.

  **Acceptance Criteria**:
  - [ ] `PYTHONPATH=src python3.11 -m unittest tests.test_cli -v` exits 0.
  - [ ] `PYTHONPATH=src python3.11 -m opencode_config_switcher --version` prints exactly `2.0.0` to stdout, nothing to stderr, exit 0.
  - [ ] TTY dispatch test requires both streams TTY and usable TERM; every other combination selects plain mode without calling curses.
  - [ ] Plain tests prove the exact header/rows/prompt/message templates above, 1-based alphabetical selection, apply/no-op bytes and output, q/EOF/Ctrl-C exit 0, invalid exit 2, and failures exit 1.
  - [ ] TUI-result tests map QUIT/APPLIED/NOOP/FATAL/TERMINATED to exact streams and exit codes, including SIGHUP 129 and SIGTERM 143.
  - [ ] Missing directory/no configs exit 1 before curses/plain prompt.
  - [ ] AST ownership test proves only `cli.py`/`__main__.py` references process exit and only `cli.py` emits user-facing stream text.

  **QA Scenarios**:
  ```
  Scenario: Piped numeric selection applies the same alphabetical item shown
    Tool: Bash
    Steps: With temp HOME/config fixtures, run `printf '2\n' | PYTHONPATH=src python3.11 -m opencode_config_switcher`; compare active/BAK bytes and capture streams/status.
    Expected: Plain list is ANSI-free; item 2 applies; active/BAK bytes match expected; success summary is stdout; exit 0.
    Evidence: .omo/evidence/task-8-plain-apply.txt

  Scenario: Invalid input and invalid JSON never write
    Tool: Bash
    Steps: Run plain subprocesses with `99`, `abc`, and the number of an invalid non-current fixture; snapshot active/BAK bytes and mtimes before/after.
    Expected: Each exits 2 with precise stderr, no reprompt, no ANSI, and zero file changes.
    Evidence: .omo/evidence/task-8-plain-errors.txt
  ```

  **Commit**: YES | Message: `feat(cli): add terminal and plain selectors` | Files: `src/opencode_config_switcher/cli.py`, `src/opencode_config_switcher/__main__.py`, `tests/test_cli.py`

- [x] 9. Prove real curses behavior through PTY and visual regression scenarios

  **What to do**:
  - Use Task 3's harness and temporary HOME/config trees to run `PYTHONPATH=src python3.11 -m opencode_config_switcher` under `TERM=xterm-256color`.
  - Add deterministic integration tests for:
    - 120x40 WIDE startup with Menu and Details content.
    - 80x24 NARROW startup, Menu→Details `Tab`, route scrolling, and return.
    - 39-column and 10-row TOO_SMALL notices with working quit.
    - Live resize 80x24→120x40→80x24 preserving selection and overlay state.
    - `d` raw overlay open, line/page scrolling, resize, and `d`/`q` close.
    - valid apply-and-exit plus active/BAK byte comparisons and post-curses stdout summary.
    - invalid selection Enter blocked, process still interactive, exact error visible, and no writes.
    - active invalid no-op, q, Ctrl-C, Ctrl-D, SIGHUP, SIGTERM, uncaught injected renderer failure, and terminal restoration. Implement fault injection through `tests/fixtures/failing_tui_entry.py`, which imports the package, replaces the renderer callable through its documented dependency-injection seam, and calls `cli.main()`; do not add a production flag/environment backdoor.
    - `LC_ALL=C`, `NO_COLOR=1`, Unicode/CJK fixture names, and monochrome capability fallback. Add `tests/fixtures/ascii_tui_entry.py`, which monkeypatches Task 7's border-capability selector (not a production flag) before invoking the real curses loop; assert PTY output/screen normalization contains ASCII `+ - |` borders.
  - Derive expected alternate-screen enter/exit bytes from terminfo (`smcup`/`rmcup`) and assert ordered restoration when capabilities exist; otherwise assert child tty attributes match the pre-run snapshot.
  - Normalize raw transcripts only for assertions/evidence; keep original `.bin` captures.
  - Run `visual-qa` against objective wide and narrow terminal captures after tests pass. If it finds a production defect, mark Task 9 failed, reopen the owning Task 7 (`tui.py`) or Task 8 (`cli.py`), make and integrate a new owner-task fix commit, rerun that owner's direct tests, then rerun Task 9 from the start. Task 9 itself remains test/evidence-only.

  **Must NOT do**:
  - Do not weaken unit tests to accommodate PTY timing, add sleeps without bounded marker waits, depend on the user's real config directory, or introduce pexpect.
  - Do not treat raw escape-sequence presence alone as proof of correct screen state; assert visible markers and filesystem outcomes too.
  - Do not edit `src/` production files from Task 9; route every production correction through the owning task as specified above.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Cross-process terminal control, filesystem effects, signals, and visual evidence.
  - Skills: [`visual-qa`, `debugging`] - Validate real TUI fidelity and diagnose timing/platform failures systematically.
  - Omitted: [`frontend-ui-ux`] - Design is approved; this task verifies rather than redesigns.

  **Parallelization**: Can Parallel: YES | Wave 5 | Blocks: [11] | Blocked By: [3, 4, 5, 7, 8]

  **References**:
  - Test helper: `tests/pty_harness.py` (Task 3) - Required spawn/input/resize/signal/capture API.
  - Behavior: `switch_oh-my-opencode_config.py:468-501` - Existing quit/apply/details loop outcomes to preserve where approved.
  - External: https://docs.python.org/3.11/library/curses.html - Terminal capabilities and resize/key constants.
  - External: https://docs.python.org/3.11/library/pty.html - PTY process behavior.

  **Acceptance Criteria**:
  - [ ] `PYTHONPATH=src python3.11 -m unittest tests.test_tui_pty -v` exits 0 without retries.
  - [ ] Wide/narrow/too-small/live-resize tests assert mode-specific visible content and preserved selection/state.
  - [ ] Apply/no-op/invalid tests assert exit status, output order relative to terminal restoration, and exact active/BAK bytes/mtimes.
  - [ ] q/Ctrl-C/Ctrl-D exit 0 with `Exiting without changes`; SIGHUP exits 129, SIGTERM exits 143, and injected exception exits 1 with the exact Task 8 messages; every case restores tty/alternate-screen state.
  - [ ] `LC_ALL=C`, `NO_COLOR`, monochrome, forced ACS-capability failure through `ascii_tui_entry.py`, and Unicode scenarios do not crash or overflow; forced failure renders ASCII `+ - |` borders in a real PTY.
  - [ ] `visual-qa` passes both 120x40 and 80x24 captures, including CJK/long-model precision.

  **QA Scenarios**:
  ```
  Scenario: Real terminal adapts from narrow to wide and applies safely
    Tool: Bash
    Steps: Run `PYTHONPATH=src python3.11 -m unittest tests.test_tui_pty.RealTuiPtyTests.test_narrow_overlay_resize_then_apply -v`; inspect the test-owned temp paths and captured transcript.
    Expected: Selection survives; wide panes appear after resize; terminal restores before success text; active/BAK bytes are correct; exit 0.
    Evidence: .omo/evidence/task-9-resize-apply.bin

  Scenario: Invalid config and abnormal exits restore terminal
    Tool: Bash
    Steps: Run `PYTHONPATH=src python3.11 -m unittest tests.test_tui_pty.RealTuiPtyTests.test_invalid_block_and_all_cleanup_paths -v`; the test launches separate children for q, Ctrl-C, Ctrl-D, SIGHUP, SIGTERM, and `tests/fixtures/failing_tui_entry.py`.
    Expected: Invalid file never writes; q/Ctrl-C/Ctrl-D exit 0, SIGHUP 129, SIGTERM 143, injected failure 1; exact messages follow cleanup and every child restores tty/terminfo state.
    Evidence: .omo/evidence/task-9-invalid-cleanup.txt
  ```

  **Commit**: YES | Message: `test(tui): cover real terminal interaction` | Files: `tests/test_tui_pty.py`, `tests/pty_harness.py`, `tests/fixtures/failing_tui_entry.py`, `tests/fixtures/ascii_tui_entry.py`, `tests/fixtures/*.json`

- [x] 10. Verify installed commands and complete the single-file-to-package migration

  **What to do**:
  - Add an installation test that creates a temporary Python 3.11 venv, runs `python -m ensurepip --upgrade`, and asserts bundled `setuptools` is at least 61 before proceeding. Set `PIP_NO_INDEX=1` and install the repository with `python -m pip install --no-build-isolation --no-deps .`; if the local bootstrap cannot satisfy setuptools>=61, fail with the exact prerequisite rather than contacting a package index.
  - Inside the venv, prove `opencode-config-switcher --version`, `switch_oh-my-opencode_config.py --version`, and `python -m opencode_config_switcher --version` each print exactly `2.0.0`, nothing to stderr, exit 0.
  - Run a temp-HOME plain selection through each console script and verify both reach the same package code and apply result.
  - Query installed metadata/files to prove exact project metadata from Task 1: name `opencode-config-switcher`, version `2.0.0`, Python `>=3.11`, description, `README.md`, `LICENSE`, no runtime dependencies, source package, and both generated scripts.
  - Uninstall inside the venv and prove both scripts and package metadata disappear.
  - After package/alias parity is green, delete the obsolete root `switch_oh-my-opencode_config.py` implementation. Do not leave a second implementation or source-path hack; legacy compatibility is the generated console entry.

  **Must NOT do**:
  - Do not access package indexes/network, install into the developer/user environment, retain duplicate business logic, or promise direct execution of the deleted source file.
  - Do not change the chosen package name, command aliases, or 2.0.0 contract during cleanup.

  **Recommended Agent Profile**:
  - Category: `quick` - Bounded package-install smoke and deletion after parity.
  - Skills: [`superpowers:verification-before-completion`, `git-master`] - Evidence before deleting the legacy implementation; keep migration atomic.
  - Omitted: [`visual-qa`] - Task 9 covers visual runtime verification.

  **Parallelization**: Can Parallel: YES | Wave 5 | Blocks: [11] | Blocked By: [1, 6, 8]

  **References**:
  - Migration source: `switch_oh-my-opencode_config.py:1-541` - Delete only after all approved behavior exists in package/tests.
  - Installer: `setup.sh:92-118` - Old copied-command verification replaced by package command verification.
  - External: https://github.com/pypa/setuptools/blob/main/docs/userguide/entry_point.rst - Installed console-script behavior.

  **Acceptance Criteria**:
  - [ ] `python3.11 -m unittest tests.test_installation -v` exits 0 with `PIP_NO_INDEX=1`, successful `ensurepip`, verified local setuptools>=61, and a temporary venv/HOME.
  - [ ] All three entry paths print byte-exact `2.0.0` and execute equivalent plain selection behavior.
  - [ ] Installed metadata has no runtime dependencies and advertises Python >=3.11.
  - [ ] Uninstall removes package and both commands from the temporary venv.
  - [ ] Root `switch_oh-my-opencode_config.py` no longer exists, and repository search finds no duplicate discovery/apply/UI implementation.

  **QA Scenarios**:
  ```
  Scenario: Offline clean install exposes both command names
    Tool: Bash
    Steps: Run installation unittest with a new venv, PIP_NO_INDEX=1, local no-build-isolation install, three version invocations, and one temp-HOME plain apply per console alias.
    Expected: Install uses no network; versions/output match; both aliases execute package code and produce identical file results.
    Evidence: .omo/evidence/task-10-offline-install.txt

  Scenario: Uninstall and source cleanup leave no ghost executable
    Tool: Bash
    Steps: Uninstall in temp venv; assert both bin paths and metadata absent; search repository for root script and copied legacy function definitions.
    Expected: Venv is clean, root implementation is deleted, and only package modules own behavior.
    Evidence: .omo/evidence/task-10-uninstall-cleanup.txt
  ```

  **Commit**: YES | Message: `build: complete package migration` | Files: `tests/test_installation.py`, `switch_oh-my-opencode_config.py` (delete)

- [ ] 11. Document the 2.0.0 TUI, installation, compatibility, and verification contract

  **What to do**:
  - Update README overview/features with full-screen WIDE/NARROW behavior, live structured Details, all Agents/Categories, invalid badges, and raw overlay.
  - Replace requirements with Python 3.11+ and Unix-like/curses scope; state that Windows is unsupported without a separate curses port.
  - Replace copy-based installation with setup.sh (pipx preferred, pip-user fallback), direct pipx/local package alternatives, PATH guidance, upgrade, and `pipx uninstall`/`pip uninstall` instructions.
  - Document canonical `opencode-config-switcher` and legacy `switch_oh-my-opencode_config.py`; remove direct-source execution instructions.
  - Replace the key table with exact contexts:
    - WIDE: Up/Down select, PageUp/PageDown Details, `d` overlay, Enter apply, q/Ctrl-D/Ctrl-C quit.
    - NARROW: `Tab` Menu/Details, pane-specific Up/Down, page keys, same apply/quit behavior.
    - Overlay: Up/Down, PageUp/PageDown, `d`/q close.
    - Space intentionally does nothing.
  - Document CURRENT/INVALID behavior, validity definition (JSON syntax/readability only), malformed-entry warnings, additional settings summary, startup snapshot/no live reload, alphabetical menu order, immediate apply-and-exit, one `.BAK`, and invalid apply blocking.
  - Document plain one-shot mode and exit codes 0/1/2 with examples.
  - Add migration notes: Python floor, generated legacy command replacing copied file, old setup behavior, 2.0.0 upgrade/uninstall, and no backup format change.
  - Update Development with `unittest`, focused PTY, package install, and setup syntax commands; add 2.0.0 version history and remove stale 1.2-current wording.
  - Add a README contract test that rejects stale claims/commands and requires all approved commands, keys, exit codes, and version/platform strings.
  - Restrict fenced shell blocks to this exact allowlist, each mapped in the test to its owning verification: `./setup.sh`; `pipx install .`; `python3.11 -m pip install --user .`; `opencode-config-switcher`; `opencode-config-switcher --version`; `switch_oh-my-opencode_config.py --version`; `python3.11 -m opencode_config_switcher --version`; `python3.11 -m pip uninstall opencode-config-switcher`; `python3.11 -m unittest discover -s tests -v`; `PYTHONPATH=src python3.11 -m unittest tests.test_tui_pty -v`; `bash -n setup.sh`. Document `pipx uninstall opencode-config-switcher` only as inline prose because no hermetic task executes a real pipx uninstall. Convert configuration-copy/edit/restore examples to prose or inline code so the test never executes arbitrary README commands.

  **Must NOT do**:
  - Do not claim Windows support, JSON-Schema validation, live reload, atomic apply, CI, PyPI publication, or keys/features outside this plan.
  - Do not include user-local config/model values or unverifiable screenshots.

  **Recommended Agent Profile**:
  - Category: `writing` - User-facing installation, interaction, and migration documentation.
  - Skills: [`git-master`] - Commit the documentation and its direct contract test atomically in repository style.
  - Omitted: [`frontend-ui-ux`] - No implementation or visual redesign is required.

  **Parallelization**: Can Parallel: NO | Wave 6 | Blocks: [Final Verification] | Blocked By: [1-10]

  **References**:
  - Pattern: `README.md:5-19` - Existing feature/requirements section to replace.
  - Pattern: `README.md:21-106` - Existing install, invocation, and key documentation to migrate.
  - Pattern: `README.md:108-198` - Discovery/apply/troubleshooting semantics to correct and retain.
  - Pattern: `README.md:262-294` - Development and version history to update.
  - Installer: `setup.sh` after Task 6 - Authoritative install behavior.
  - Package: `pyproject.toml` after Tasks 1/10 - Authoritative commands/version/Python contract.

  **Acceptance Criteria**:
  - [ ] `python3.11 -m unittest tests.test_readme_contract -v` exits 0.
  - [ ] README contains `2.0.0`, `Python 3.11+`, `opencode-config-switcher`, the legacy alias, pipx and pip-user paths, uninstall/migration, WIDE/NARROW/overlay keys, plain exit codes, and all verification commands.
  - [ ] README contains none of: `Python 3.6+`, copied-script install instructions, direct root-script execution, Space-to-apply, Windows support, schema-validation claim, or 1.2.0 marked Current.
  - [ ] `ReadmeContractTests.test_fenced_commands_match_allowlist` proves every fenced shell block exactly matches the allowlist above and maps to Task 6, Task 9, Task 10, or the final suite; no command is classified as merely environmental.

  **QA Scenarios**:
  ```
  Scenario: Fresh user can install and understand both layouts
    Tool: Bash
    Steps: Run `python3.11 -m unittest tests.test_readme_contract.ReadmeContractTests.test_fenced_commands_match_allowlist tests.test_readme_contract.ReadmeContractTests.test_keys_and_contract_strings -v`; compare its command-to-owning-test mapping and key constants.
    Expected: Every fenced command is in the exact allowlist and mapped to an existing automated check; keys/commands/version match code exactly; no stale copy-based instructions remain.
    Evidence: .omo/evidence/task-11-readme-contract.txt

  Scenario: Migration guidance covers failure and rollback paths
    Tool: Bash
    Steps: Run `python3.11 -m unittest tests.test_readme_contract.ReadmeContractTests.test_migration_and_failure_contract -v`.
    Expected: All required migration/error terms are present and no unsupported workaround such as sudo or --break-system-packages is recommended.
    Evidence: .omo/evidence/task-11-migration-docs.txt
  ```

  **Commit**: YES | Message: `docs: document full-screen configuration selector` | Files: `README.md`, `tests/test_readme_contract.py`

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> Four review agents run in parallel. ALL must APPROVE. Present consolidated results to the user and get explicit `okay` before completing.
> **Do not auto-proceed after verification. Wait for the user's explicit approval before marking work complete.**
> **Never mark F1-F4 checked before the user's okay.** Rejection or feedback → fix → rerun all affected verification → present again → wait for okay.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  - Read this plan and the final diff; verify every Must Have, Must NOT Have, task acceptance criterion, behavior contract, and evidence path.
  - Output `APPROVE` or `REJECT` with exact file/line violations.

- [ ] F2. **Code Quality Review** — `unspecified-high`
  - Run all tests and inspect package boundaries, exception ownership, curses clipping, cleanup, standard-library-only imports, and Python 3.11 typing.
  - Output `APPROVE` or `REJECT` with reproducible findings.

- [ ] F3. **Real Manual QA** — `unspecified-high` + `visual-qa`
  - Execute the installed command in PTYs at wide, narrow, minimum, and resized dimensions; capture screenshots/terminal recordings and verify navigation, Details, overlay, apply, invalid blocking, and restoration.
  - Output `APPROVE` or `REJECT`; save consolidated evidence under `.omo/evidence/final-qa/`.

- [ ] F4. **Scope Fidelity Check** — `deep`
  - Compare final files against Original Request, approved defaults, and Must NOT Have; reject scope creep or missing migration/documentation behavior.
  - Output `APPROVE` or `REJECT` with exact discrepancies.

## Commit Strategy
- Follow the repository's semantic commit style (`feat:`, `fix:`, `refactor:`, `build:`, `test:`, `docs:`), observed in recent history.
- Each implementation task commits its implementation and direct tests together; no test-only follow-up for behavior introduced by the same task.
- Parallel tasks commit only inside separate isolated worktrees; the integration owner serially cherry-picks completed commits in task-number order and runs the full suite between waves. Never run concurrent `git add`/`git commit` against one index/HEAD.
- Tasks that touch the same file are ordered by the dependency matrix; do not parallelize overlapping writes or resolve source conflicts inside worker worktrees.
- Expected atomic sequence: package metadata → config parser → PTY harness → switching service → pure TUI model → installer → curses runtime → CLI runtime → PTY regression → install compatibility → docs.

## Success Criteria
- The selector visibly occupies the terminal and never clips/crashes across tested dimensions/locales.
- Highlight changes immediately update a complete, scrollable right-side structured summary in wide mode.
- Narrow terminals retain access to the same Menu and Details content through `Tab`.
- Current and invalid statuses are correct independently of list position.
- Valid apply/no-op/backup behavior is byte-for-byte compatible; invalid JSON cannot overwrite active config.
- Plain non-TTY mode has deterministic one-shot input and documented exit codes.
- Terminal modes are restored after quit, apply, exceptions, Ctrl-C, SIGHUP, and SIGTERM to the extent those signals are catchable.
- Both installed command names and module execution report version `2.0.0`.
- All tests and final review agents approve, and the user explicitly accepts the consolidated verification result.
