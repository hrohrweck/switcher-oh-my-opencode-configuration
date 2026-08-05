# OpenCode Configuration Switcher v2.0.0

A full-screen Python curses TUI for switching between oh-my-opencode / oh-my-openagent configuration files with a structured details panel showing agent models, fallback chains, and configuration metadata.

## Features

- **Full-Screen TUI**: Uses all available terminal space with a polished curses interface
- **Structured Details Panel**: Right-side panel shows primary/fallback models for every Agent and Category, global fallback policy, runtime settings, and additional configuration metadata
- **Responsive Layout**: Wide mode (100+ columns) shows menu and details side by side; narrow mode uses Tab to switch between full-width Menu and Details panes
- **Invalid Detection**: Malformed JSON or unreadable configs show an INVALID badge and are blocked from being applied
- **Raw JSON Overlay**: Press `d` to view the full raw configuration in a scrollable overlay
- **Safe Operations**: Automatic single-generation `.BAK` backup creation before switching
- **Plain Mode**: Non-TTY/piped execution with numbered selection and documented exit codes
- **CURRENT Config Indicator**: Clear visual indication of the currently active configuration with a CURRENT badge

## Requirements

- **Python 3.11+**
- **oh-my-opencode** or **oh-my-openagent** installed with configuration directory at `~/.config/opencode/`
- **Unix-like system** (Linux, macOS). Windows is unsupported (no stdlib curses).

## Installation

### pipx (Recommended)
```bash
pipx install .
```
Or from the repository directory:
```bash
./setup.sh   # prefers pipx, falls back to pip --user
```

### pip --user (fallback)
```bash
python3.11 -m pip install --user .
```

If you encounter a `externally-managed-environment` error (PEP 668), install pipx first or use an isolated virtual environment.

### Commands
After installation, two commands are available:
- `opencode-config-switcher` — canonical command
- `switch_oh-my-opencode_config.py` — legacy alias

### Uninstall
```bash
pipx uninstall opencode-config-switcher
# or
python3.11 -m pip uninstall opencode-config-switcher
```

## Usage

### TUI Mode (interactive terminal)

| Context | Key | Action |
|---------|-----|--------|
| **WIDE** (100+ cols) | Up / Down | Select configuration |
| | PageUp / PageDown | Scroll Details panel |
| | `d` | Open raw JSON overlay |
| | Enter | Apply and exit |
| | q / Ctrl-D / Ctrl-C | Quit without changes |
| **NARROW** (40-99 cols) | Tab | Switch Menu / Details |
| | Up / Down | Navigate active pane |
| | Enter | Apply from either pane |
| | q / Ctrl-D / Ctrl-C | Quit |
| **Overlay** | Up / Down / PgUp/PgDn | Scroll |
| | `d` / `q` | Close overlay |
| **Too Small** (<40 cols or <12 rows) | q / Ctrl-C / Ctrl-D | Quit |

Space does nothing in all contexts.

### Plain Mode (piped / non-TTY)
Prints a numbered list and reads one input line:
- `q` or EOF: prints `Exiting without changes`, exit 0
- Valid number: apply, prints `Configuration applied:` and `Backup saved to:`, exit 0
- Invalid number / out-of-range / invalid config: error to stderr, exit 2
- Copy failure: error to stderr, exit 1

### How It Works

1. **Configuration Discovery**: The script scans `~/.config/opencode/` for all JSON files matching `oh-my-openagent*.json` or `oh-my-opencode*.json`
2. **File Filtering**: The active config is included in the list as the first item and marked CURRENT; `.BAK` files are excluded from selection candidates
3. **Backup & Apply**: When you select a configuration:
   - Current active config is backed up to `.BAK` (overwrites existing backup)
   - Selected file is **copied** (not moved) to the current active config
   - The new configuration becomes immediately active

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
4. Test thoroughly
5. Commit your changes: `git commit -m 'feat: Add amazing feature'`
6. Push to the branch: `git push origin feature/amazing-feature`
7. Open a Pull Request

## Version History

- **v2.0.0** (Current):
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

## Support

If you encounter any issues or have questions:

1. Check the troubleshooting section above
2. Review the oh-my-openagent / oh-my-opencode documentation
3. Open an issue in the project repository

---

*This script is designed to work with the oh-my-openagent and oh-my-opencode configuration management systems. For more information, please refer to the official documentation.*