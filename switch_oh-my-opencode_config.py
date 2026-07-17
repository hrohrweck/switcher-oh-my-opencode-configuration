#!/usr/bin/env python3
"""
OpenCode Configuration Switcher
Interactive script for switching oh-my-opencode configuration files.
"""

import os
import sys
import shutil
import json
from pathlib import Path
from typing import List, Dict


def supports_raw_input() -> bool:
    """Check if raw terminal input (single-key reading) is available."""
    try:
        import tty
        import termios
        if not sys.stdin.isatty():
            return False
        fd = sys.stdin.fileno()
        termios.tcgetattr(fd)  # raises if not a real terminal
        return True
    except Exception:
        return False


def get_key() -> str:
    """Read a single key from the terminal and return a normalized token."""
    import tty
    import termios
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == '\x1b':
            ch += sys.stdin.read(2)
            if ch == '\x1b[A':
                return 'up'
            elif ch == '\x1b[B':
                return 'down'
            elif ch == '\x1b[C' or ch == '\x1b[D':
                return ''
            return ''
        if ch in ('\r', '\n', ' '):
            return 'enter'
        if ch == '\x03':
            return 'ctrlc'
        if ch == '\x04':
            return 'ctrld'
        return ch.lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


# Version
__version__ = "1.2.0"

# Configuration directory
CONFIG_DIR = Path.home() / ".config" / "opencode"

# Legacy opencode config paths (deprecated)
CURRENT_CONFIG = CONFIG_DIR / "oh-my-opencode.json"
BACKUP_CONFIG = CONFIG_DIR / "oh-my-opencode.json.BAK"

# New openagent config paths (preferred)
CURRENT_CONFIG_AGENT = CONFIG_DIR / "oh-my-openagent.json"
BACKUP_CONFIG_AGENT = CONFIG_DIR / "oh-my-openagent.json.BAK"


def get_active_config() -> Path:
    """Determine the active configuration file.
    
    Prefers oh-my-openagent.json (new name) over oh-my-opencode.json (deprecated).
    Returns the path that exists, or the new preferred path if neither exists.
    """
    if CURRENT_CONFIG_AGENT.exists():
        return CURRENT_CONFIG_AGENT
    return CURRENT_CONFIG


def get_backup_config() -> Path:
    """Get the backup path matching the active configuration."""
    active = get_active_config()
    if active == CURRENT_CONFIG_AGENT:
        return BACKUP_CONFIG_AGENT
    return BACKUP_CONFIG

# ANSI Color codes
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    BOLD = '\033[1m'
    NC = '\033[0m'  # No Color

# UTF-8 box drawing characters (with ASCII fallback)
class BoxChars:
    def __init__(self):
        self.utf8_supported = self._check_utf8_support()
        if self.utf8_supported:
            self.HLINE = '─'
            self.VLINE = '│'
            self.TL = '┌'
            self.TR = '┐'
            self.BL = '└'
            self.BR = '┘'
        else:
            self.HLINE = '-'
            self.VLINE = '|'
            self.TL = '+'
            self.TR = '+'
            self.BL = '+'
            self.BR = '+'

    def _check_utf8_support(self) -> bool:
        """Check if terminal supports UTF-8"""
        locale = os.environ.get('LC_ALL', '') + os.environ.get('LC_CTYPE', '') + os.environ.get('LANG', '')
        return 'UTF-8' in locale or 'utf8' in locale or 'UTF8' in locale


def log_info(msg: str):
    """Print info message"""
    print(f"{Colors.BLUE}[INFO]{Colors.NC} {msg}")


def log_success(msg: str):
    """Print success message"""
    print(f"{Colors.GREEN}[SUCCESS]{Colors.NC} {msg}")


def log_warning(msg: str):
    """Print warning message"""
    print(f"{Colors.YELLOW}[WARNING]{Colors.NC} {msg}")


def log_error(msg: str):
    """Print error message"""
    print(f"{Colors.RED}[ERROR]{Colors.NC} {msg}", file=sys.stderr)


def repeat_char(char: str, count: int) -> str:
    """Repeat a character count times"""
    return char * count


def get_terminal_width() -> int:
    """Get terminal width with fallback"""
    try:
        import shutil
        width = shutil.get_terminal_size().columns
        return min(max(width, 60), 92)
    except:
        return 80


def clear_screen():
    """Clear the terminal screen"""
    os.system('clear' if os.name == 'posix' else 'cls')


def print_box_header(title: str, subtitle: str, box: BoxChars):
    """Print a formatted box header"""
    width = get_terminal_width()
    inner = width - 2

    print(f"{box.TL}{repeat_char(box.HLINE, inner)}{box.TR}")
    print(f"{box.VLINE} {title:<{inner - 1}}{box.VLINE}")
    print(f"{box.VLINE} {subtitle:<{inner - 1}}{box.VLINE}")
    print(f"{box.BL}{repeat_char(box.HLINE, inner)}{box.BR}")


def print_sep(box: BoxChars):
    """Print a separator line"""
    width = get_terminal_width()
    sep = repeat_char(box.HLINE, width)
    print(f"{Colors.CYAN}{sep}{Colors.NC}")


def print_version():
    """Print version information"""
    print(f"{Colors.CYAN}Version: {__version__}{Colors.NC}")


def print_header(msg: str):
    """Print a formatted header"""
    print(f"\n{Colors.CYAN}{Colors.BOLD}{msg}{Colors.NC}")


def get_available_configs() -> List[Path]:
    """Get list of available JSON configuration files including current config"""
    if not CONFIG_DIR.exists():
        log_error(f"Configuration directory not found: {CONFIG_DIR}")
        sys.exit(1)

    config_files = []
    active_config = get_active_config()

    # First, add the current config if it exists (will be item #1)
    if active_config.exists():
        config_files.append(active_config)

    # Collect config files from both naming conventions
    seen = {f.name for f in config_files}
    
    # Legacy oh-my-opencode files
    for file in CONFIG_DIR.glob("oh-my-opencode*.json"):
        if file.name not in seen and file.name != "oh-my-opencode.json" and not file.name.endswith(".BAK"):
            config_files.append(file)
            seen.add(file.name)
    
    # New oh-my-openagent files
    for file in CONFIG_DIR.glob("oh-my-openagent*.json"):
        if file.name not in seen and file.name != "oh-my-openagent.json" and not file.name.endswith(".BAK"):
            config_files.append(file)
            seen.add(file.name)

    return sorted(config_files)


def display_config_preview(config_path: Path, box: BoxChars):
    """Display preview of configuration file contents with pagination"""
    try:
        with open(config_path, 'r') as f:
            data = json.load(f)

        # Convert JSON to formatted string for display
        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        lines = json_str.split('\n')
        total_lines = len(lines)

        # Get terminal dimensions
        try:
            terminal_size = shutil.get_terminal_size()
            term_width = terminal_size.columns
            term_height = terminal_size.lines
        except:
            term_width = 80
            term_height = 24

        # Calculate page size (leave space for header and footer)
        header_lines = 5  # Title, separator, file info, size, content label
        footer_lines = 2  # Separator and help text
        page_size = max(5, term_height - header_lines - footer_lines)

        # Calculate total pages
        total_pages = (total_lines + page_size - 1) // page_size

        # Pagination loop
        current_page = 0

        while True:
            clear_screen()

            # Print header
            print(f"{Colors.BOLD}{Colors.CYAN}--- File: {config_path.name} ---{Colors.NC}")
            print_sep(box)

            # Display file info
            print(f"\n{Colors.BOLD}File:{Colors.NC} {config_path}")
            print(f"{Colors.BOLD}Size:{Colors.NC} {config_path.stat().st_size} bytes")
            print(f"{Colors.BOLD}Total Lines:{Colors.NC} {total_lines}")

            # Calculate current page line range
            start_line = current_page * page_size
            end_line = min(start_line + page_size, total_lines)

            # Display page info
            print(f"\n{Colors.BOLD}[Page {current_page + 1}/{total_pages}]{Colors.NC} "
                  f"{Colors.BOLD}Lines {start_line + 1}-{end_line} of {total_lines}{Colors.NC}")
            print_sep(box)

            # Display content for current page
            print(f"\n{Colors.BOLD}Content:{Colors.NC}")
            for i in range(start_line, end_line):
                print(lines[i])

            print_sep(box)

            # Display navigation help
            if total_pages > 1:
                print(f"\n{Colors.BOLD}Navigation:{Colors.NC}")
                if current_page < total_pages - 1:
                    print(f"  {Colors.GREEN}Space/Enter{Colors.NC} - Next page")
                if current_page > 0:
                    print(f"  {Colors.GREEN}b{Colors.NC} - Previous page")
                print(f"  {Colors.GREEN}q{Colors.NC} - Quit to menu")
            else:
                print(f"\n{Colors.YELLOW}Press q to return to menu{Colors.NC}")

            # Get user input for navigation
            try:
                # Use sys.stdin.read for single character input
                import tty
                import termios

                key = get_key()

                # Handle key input
                if key in ('q', 'ctrlc', 'ctrld'):  # q, Ctrl-C, or Ctrl-D
                    break
                elif key == 'enter':  # Enter or Space
                    if current_page < total_pages - 1:
                        current_page += 1
                    else:
                        break  # Exit if on last page
                elif key == 'b':  # Previous page
                    if current_page > 0:
                        current_page -= 1
                elif key == 'up':  # Up arrow
                    if current_page > 0:
                        current_page -= 1
                elif key == 'down':  # Down arrow
                    if current_page < total_pages - 1:
                        current_page += 1

            except (ImportError, OSError, termios.error):
                # Fallback for systems without tty/termios (e.g., Windows)
                # or when not running in a terminal
                if total_pages > 1:
                    prompt = f"\n{Colors.BOLD}Enter (next page)"
                    if current_page > 0:
                        prompt += f", b (previous page)"
                    prompt += f", or q (quit):{Colors.NC} "
                else:
                    prompt = f"\n{Colors.BOLD}Press q to return to menu:{Colors.NC} "

                user_input = input(prompt).strip().lower()

                if user_input == 'q':
                    break
                elif user_input == 'b' and current_page > 0:
                    current_page -= 1
                elif user_input == '' or user_input == ' ':  # Enter or Space
                    if current_page < total_pages - 1:
                        current_page += 1
                    else:
                        break

    except json.JSONDecodeError as e:
        log_error(f"Invalid JSON in {config_path.name}: {e}")
        input("\nPress Enter to continue...")
    except Exception as e:
        log_error(f"Error reading {config_path.name}: {e}")
        input("\nPress Enter to continue...")


def display_menu(configs: List[Path], box: BoxChars, selected_index: int):
    """Display the configuration selection menu"""
    clear_screen()

    print_box_header(
        "OpenCode Configuration Switcher v1.2.0",
        "Commands: Up/Down=move | Enter=apply | d=details | q=quit",
        box
    )

    print_header("\nAvailable Configurations")
    print_sep(box)

    # Print header with column alignment
    print(f"  {'#':<3} {'Filename'}")
    print_sep(box)

    for idx, config in enumerate(configs, 1):
        is_highlighted = (idx - 1 == selected_index)

        if is_highlighted:
            # Full-width reverse-video line — suppress ALL Colors.*
            width = get_terminal_width()
            lead = "  "
            avail = width - len(lead)
            text = f"{idx}) {config.name}"
            if idx == 1 and config == get_active_config():
                text += " (current)"
            # Truncate or pad to fill the row
            if len(text) > avail:
                text = text[:avail]
            else:
                text = text + ' ' * (avail - len(text))
            print(f"{lead}\033[7m{text}\033[27m")
        else:
            # Non-highlighted row — keep colors, drop [ ] marker
            if idx == 1 and config == get_active_config():
                config_name = f"{Colors.GREEN}{config.name}{Colors.NC} {Colors.YELLOW}(current){Colors.NC}"
            else:
                config_name = f"{Colors.CYAN}{config.name}{Colors.NC}"
            print(f"  {Colors.BOLD}{idx}){Colors.NC} {config_name}")

    print_sep(box)

    # Show backup status
    backup_config = get_backup_config()
    if backup_config.exists():
        print(f"\n{Colors.BOLD}Backup exists:{Colors.NC} {backup_config}")
    else:
        print(f"\n{Colors.YELLOW}No backup file (will be created on first switch){Colors.NC}")


def apply_config(source: Path) -> bool:
    """Apply the selected configuration"""
    active_config = get_active_config()
    backup_config = get_backup_config()

    # Special case: if user selects the current config (item #1), do nothing
    if source == active_config:
        log_info(f"No change - already using this configuration")
        log_info(f"Current file: {active_config}")
        return True

    try:
        # Step 1: Backup current config
        if active_config.exists():
            log_info(f"Creating backup: {backup_config}")
            shutil.copy2(active_config, backup_config)
            log_success("Backup created successfully")

        # Step 2: Copy selected config to current
        log_info(f"Copying {source.name} -> {active_config.name}")
        shutil.copy2(source, active_config)

        log_success(f"Configuration switched to: {source.name}")
        return True

    except Exception as e:
        log_error(f"Failed to apply configuration: {e}")
        return False


def show_current_config_details(box: BoxChars):
    """Show details of current configuration"""
    active_config = get_active_config()
    if not active_config.exists():
        log_warning("No current configuration file exists")
        return

    display_config_preview(active_config, box)


def main():
    """Main execution loop"""
    # Check for --version flag
    if len(sys.argv) > 1 and sys.argv[1] == "--version":
        print(__version__)
        sys.exit(0)

    box = BoxChars()

    # Check if config directory exists
    if not CONFIG_DIR.exists():
        log_error(f"Configuration directory not found: {CONFIG_DIR}")
        log_info("Please ensure oh-my-opencode is installed")
        sys.exit(1)

    # Get available configurations
    configs = get_available_configs()

    if not configs:
        log_error(f"No configuration files found in {CONFIG_DIR}")
        log_info("Expected files: oh-my-openagent-*.json or oh-my-opencode-*.json")
        log_info("(excluding the current active config file)")
        sys.exit(1)

    # Main menu loop
    selected_index = 0

    while True:
        display_menu(configs, box, selected_index)

        if supports_raw_input():
            # Arrow-key navigation mode (tty available)
            key = get_key()

            if key in ('q', 'ctrlc', 'ctrld'):
                log_info("Exiting without changes")
                sys.exit(0)
            elif key == 'up':
                selected_index = max(0, selected_index - 1)
            elif key == 'down':
                selected_index = min(len(configs) - 1, selected_index + 1)
            elif key == 'enter':
                # Apply the highlighted config immediately (one-step)
                print()
                if apply_config(configs[selected_index]):
                    if configs[selected_index] == get_active_config():
                        print(f"\n{Colors.GREEN}{Colors.BOLD}No configuration change needed{Colors.NC}")
                        sys.exit(0)
                    else:
                        print(f"\n{Colors.GREEN}{Colors.BOLD}Configuration applied successfully!{Colors.NC}")
                        print(f"{Colors.CYAN}Backup saved to: {get_backup_config()}{Colors.NC}")
                        sys.exit(0)
                else:
                    print(f"\n{Colors.RED}Failed to apply configuration{Colors.NC}")
                    input("\nPress Enter to continue...")
            elif key == 'd':
                display_config_preview(configs[selected_index], box)
            # Any other key (including '' and stray chars) is ignored — loop redraws

        else:
            # Non-tty fallback: numeric input mode
            try:
                user_input = input(f"\n{Colors.BOLD}Enter number to apply, or q to quit:{Colors.NC} ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n\n{Colors.YELLOW}Interrupted by user{Colors.NC}")
                sys.exit(0)

            if user_input.lower() == 'q':
                log_info("Exiting without changes")
                sys.exit(0)

            try:
                num = int(user_input)
                if 1 <= num <= len(configs):
                    selected_index = num - 1
                    print()
                    if apply_config(configs[selected_index]):
                    if configs[selected_index] == get_active_config():
                        print(f"\n{Colors.GREEN}{Colors.BOLD}No configuration change needed{Colors.NC}")
                        sys.exit(0)
                    else:
                        print(f"\n{Colors.GREEN}{Colors.BOLD}Configuration applied successfully!{Colors.NC}")
                        print(f"{Colors.CYAN}Backup saved to: {get_backup_config()}{Colors.NC}")
                        sys.exit(0)
                    else:
                        print(f"\n{Colors.RED}Failed to apply configuration{Colors.NC}")
                        input("\nPress Enter to continue...")
                else:
                    log_error(f"Invalid selection: {num} (must be 1-{len(configs)})")
                    input("Press Enter to continue...")
            except ValueError:
                log_error(f"Invalid input: {user_input}")
                input("Press Enter to continue...")



if __name__ == "__main__":
    main()
