# OpenCode Configuration Switcher v2.0.0

A full-screen Python curses TUI for switching between oh-my-opencode / oh-my-openagent configuration files with a structured details panel showing agent models, fallback chains, and configuration metadata.

## Features

- **Full-Screen TUI**: Uses all available terminal space with a polished curses interface
- **Structured Details Panel**: Right-side panel shows primary/fallback models for every Agent and Category, global fallback policy, runtime settings, and additional configuration metadata
- **Responsive Layout**: Wide mode (100+ columns) shows menu and details side by side; narrow mode uses Tab to switch between full-width Menu and Details panes
- **Invalid Detection**: Malformed JSON or unreadable configs are clearly marked and blocked from being applied
- **Raw JSON Overlay**: Press `d` to view the full raw configuration in a scrollable overlay
- **Safe Operations**: Automatic single-generation `.BAK` backup creation before switching
- **Plain Mode**: Non-TTY/piped execution with numbered selection and documented exit codes
- **Current Config Indicator**: Clear visual indication of the currently active configuration

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
- `q` or EOF: exit 0, no changes
- Valid number: apply and exit 0
- Invalid number / out-of-range / invalid config: exit 2
- Copy failure: exit 1

### How It Works

1. **Configuration Discovery**: The script scans `~/.config/opencode/` for all JSON files matching `oh-my-openagent*.json` or `oh-my-opencode*.json`
2. **File Filtering**: It excludes the current active config (`oh-my-openagent.json` or `oh-my-opencode.json`) and `.BAK` files from the selection list
3. **Backup & Apply**: When you select a configuration:
   - Current active config is backed up to `.BAK` (overwrites existing backup)
   - Selected file is **copied** (not moved) to the current active config
   - The new configuration becomes immediately active

## Configuration Directory Structure

```
~/.config/opencode/
├── oh-my-openagent.json              # Current active configuration (preferred, new name)
├── oh-my-openagent.json.BAK          # Backup of previous configuration
├── oh-my-openagent-default.json      # Default configuration preset
├── oh-my-openagent_OpenAI-GLM4.7.json # OpenAI + GLM4.7 configuration
├── oh-my-openagent_OpenAI-Only.json  # OpenAI-only configuration
├── oh-my-openagent_Custom.json       # Your custom configuration
├── oh-my-opencode.json               # Legacy current active configuration (deprecated)
└── oh-my-opencode-*.json             # Legacy configuration presets (still supported)
```

## Creating New Configuration Presets

### Method 1: From Current Configuration

1. Copy your current configuration to a new file:
```bash
cp ~/.config/opencode/oh-my-openagent.json ~/.config/opencode/oh-my-openagent-myconfig.json

# Edit your custom configuration
nano ~/.config/opencode/oh-my-openagent-myconfig.json
# or
vim ~/.config/opencode/oh-my-openagent-myconfig.json
```

3. Run the switcher to select your new configuration from the menu

### Method 2: From Template

1. Copy a template configuration:
```bash
cp ~/.config/opencode/oh-my-openagent-default.json ~/.config/opencode/oh-my-openagent-mytemplate.json
```

2. Modify the template to create your custom configuration

## Restoring from Backup

If you need to restore the previous configuration:

```bash
# Check available backups
ls -la ~/.config/opencode/oh-my-openagent*.BAK ~/.config/opencode/oh-my-opencode*.BAK

# To restore from backup
cp ~/.config/opencode/oh-my-openagent.json.BAK ~/.config/opencode/oh-my-openagent.json
```

## Troubleshooting

### Common Issues

1. **"Configuration directory not found"**
   - Ensure oh-my-opencode is installed
   - Check the configuration directory exists: `ls -la ~/.config/opencode/`

2. **"No configuration files found"**
   - Verify you have configuration files in `~/.config/opencode/`
   - Files should match pattern: `oh-my-openagent-*.json` or `oh-my-opencode-*.json` (excluding current and backup)

3. **"Permission denied"**
   - Ensure the script is executable: `chmod +x switch_oh-my-opencode_config.py`
   - Check write permissions on `~/.config/opencode/`

4. **Script not found in PATH**
   - Verify `~/.local/bin` is in your PATH
   - Try running with full path: `python3 ~/.local/bin/switch_oh-my-opencode_config.py`

### Debug Mode

For troubleshooting, you can add debug information by examining the script's environment:

```bash
# Check Python version
python3 --version

# Check if required modules are available
python3 -c "import json, shutil, sys; print('All modules available')"
```

## Advanced Usage

### Integration with Shell Scripts

The script can be integrated into shell scripts for automation:

```bash
#!/bin/bash
# Example: Apply a specific configuration programmatically
CONFIG_FILE="oh-my-openagent-custom.json"
CONFIG_PATH="$HOME/.config/opencode/$CONFIG_FILE"

if [ -f "$CONFIG_PATH" ]; then
    cp "$CONFIG_PATH" "$HOME/.config/opencode/oh-my-openagent.json"
    echo "Configuration applied: $CONFIG_FILE"
else
    echo "Configuration file not found: $CONFIG_PATH"
    exit 1
fi
```

### Configuration Management Script

Create a management script for your configurations:

```bash
#!/bin/bash
# config-manager.sh

case "$1" in
    "list")
        echo "Available configurations:"
        ls -la ~/.config/opencode/oh-my-openagent*.json ~/.config/opencode/oh-my-opencode*.json | grep -v "\.BAK$"
        ;;
    "backup")
        cp ~/.config/opencode/oh-my-openagent.json ~/.config/opencode/oh-my-openagent-backup-$(date +%Y%m%d-%H%M%S).json
        echo "Backup created"
        ;;
    "clean")
        find ~/.config/opencode/ -name "oh-my-openagent*.BAK" -delete -o -name "oh-my-opencode*.BAK" -delete
        echo "Cleaned up backup files"
        ;;
    *)
        echo "Usage: $0 {list|backup|clean}"
        exit 1
        ;;
esac
```

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