# OpenCode Configuration Switcher

An interactive Python script for switching between oh-my-opencode configuration files with a user-friendly terminal interface.

## Features

- **Interactive Menu System**: Colorized terminal UI with numbered selection
- **Configuration Preview**: View detailed contents of configuration files before applying (`d#` command)
- **Safe Operations**: Automatic backup creation before switching configurations
- **UTF-8 Support**: Smart box drawing characters with ASCII fallback for compatibility
- **Keyboard Navigation**: Intuitive controls with space/enter for navigation
- **Current Config Indicator**: Clear visual indication of the currently active configuration
- **Backup Management**: Automatic backup file management with restore capability

## Requirements

- **Python 3.6+** (3.7+ recommended for best performance)
- **oh-my-opencode** installed with configuration directory at `~/.config/opencode/`
- **Unix-like system** (Linux, macOS, or WSL)

## Installation

### Method 1: Using the setup script (Recommended)

1. Clone or download this repository:
```bash
git clone <repository-url>
cd switcher-oh-my-opencode-configuration
```

2. Run the setup script:
```bash
./setup.sh
```

The script will:
- Install the script to `~/.local/bin/`
- Make it executable
- Check if the directory is in your PATH
- Verify the installation

3. Ensure `~/.local/bin` is in your PATH:
```bash
# Add to your shell profile (choose one)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
# or for zsh:
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
# Then reload:
source ~/.bashrc  # or source ~/.zshrc
```

### Method 2: Manual Installation

1. Clone or copy this repository:
```bash
git clone <repository-url>
cd switcher-oh-my-opencode-configuration
```

2. Make the script executable:
```bash
chmod +x switch_oh-my-opencode_config.py
```

3. Copy to your local bin directory:
```bash
mkdir -p ~/.local/bin
cp switch_oh-my-opencode_config.py ~/.local/bin/
```

4. Ensure `~/.local/bin` is in your PATH (as shown above)

## Usage

### Basic Usage

Run the script:
```bash
switch_oh-my-opencode_config.py
```

Or with explicit Python:
```bash
python3 ~/.local/bin/switch_oh-my-opencode_config.py
```

### Version Check

Check the script version:
```bash
switch_oh-my-opencode_config.py --version
```

### Interactive Menu Interface

The script provides an interactive menu with the following commands:

| Command | Description |
|---------|-------------|
| `1-9` | Select a configuration file by number |
| `d#` | View detailed file contents (e.g., `d1`, `d2`, `d3`) |
| `q` | Quit without making any changes |
| `Enter` | Apply the selected configuration |
| `Space` | Navigate through pages in detail view |
| `b` | Go to previous page in detail view |
| `Arrow Keys` | Navigate through pages in detail view |

### How It Works

1. **Configuration Discovery**: The script scans `~/.config/opencode/` for all JSON files matching `oh-my-opencode*.json`
2. **File Filtering**: It excludes `oh-my-opencode.json` (current active config) and `.BAK` files from the selection list
3. **Safe Switching**: When you select and apply a configuration:
   - Current `oh-my-opencode.json` is backed up to `oh-my-opencode.json.BAK` (overwrites existing backup)
   - Selected file is **copied** (not moved) to `oh-my-opencode.json`
   - The new configuration becomes immediately active

## Configuration Directory Structure

```
~/.config/opencode/
├── oh-my-opencode.json              # Current active configuration (DO NOT EDIT DIRECTLY)
├── oh-my-opencode.json.BAK          # Backup of previous configuration
├── oh-my-opencode-default.json      # Default configuration preset
├── oh-my-opencode_OpenAI-GLM4.7.json # OpenAI + GLM4.7 configuration
├── oh-my-opencode_OpenAI-Only.json  # OpenAI-only configuration
├── oh-my-opencode_Custom.json       # Your custom configuration
└── ... (other configuration files)
```

## Creating New Configuration Presets

### Method 1: From Current Configuration

1. Copy your current configuration to a new file:
```bash
cp ~/.config/opencode/oh-my-opencode.json ~/.config/opencode/oh-my-opencode-myconfig.json
```

2. Edit the new file with your desired settings:
```bash
nano ~/.config/opencode/oh-my-opencode-myconfig.json
# or use your preferred editor
vim ~/.config/opencode/oh-my-opencode-myconfig.json
```

3. Run the switcher to select your new configuration from the menu

### Method 2: From Template

1. Copy a template configuration:
```bash
cp ~/.config/opencode/oh-my-opencode-default.json ~/.config/opencode/oh-my-opencode-mytemplate.json
```

2. Modify the template to create your custom configuration

## Restoring from Backup

If you need to restore the previous configuration:

```bash
# Check available backups
ls -la ~/.config/opencode/oh-my-opencode*.BAK

# Restore from backup
cp ~/.config/opencode/oh-my-opencode.json.BAK ~/.config/opencode/oh-my-opencode.json
```

## Troubleshooting

### Common Issues

1. **"Configuration directory not found"**
   - Ensure oh-my-opencode is installed
   - Check the configuration directory exists: `ls -la ~/.config/opencode/`

2. **"No configuration files found"**
   - Verify you have configuration files in `~/.config/opencode/`
   - Files should match pattern: `oh-my-opencode*.json` (excluding current and backup)

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
CONFIG_FILE="oh-my-opencode-custom.json"
CONFIG_PATH="$HOME/.config/opencode/$CONFIG_FILE"

if [ -f "$CONFIG_PATH" ]; then
    cp "$CONFIG_PATH" "$HOME/.config/opencode/oh-my-opencode.json"
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
        ls -la ~/.config/opencode/oh-my-opencode*.json | grep -v "\.BAK$"
        ;;
    "backup")
        cp ~/.config/opencode/oh-my-opencode.json ~/.config/opencode/oh-my-opencode-backup-$(date +%Y%m%d-%H%M%S).json
        echo "Backup created"
        ;;
    "clean")
        find ~/.config/opencode/ -name "oh-my-opencode*.BAK" -delete
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

- **v1.0.1** (Current):
  - Added version variable and --version flag
  - Improved version management
  - Enhanced documentation

- **v1.0.0**:
  - Initial release
  - Basic configuration switching functionality
  - Interactive menu system
  - Backup management

## Support

If you encounter any issues or have questions:

1. Check the troubleshooting section above
2. Review the oh-my-opencode documentation
3. Open an issue in the project repository

---

*This script is designed to work with the oh-my-opencode configuration management system. For more information about oh-my-opencode, please refer to the official documentation.*