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
from typing import List, Dict, Optional

# Version
__version__ = "1.0.1"

# Configuration directory
CONFIG_DIR = Path.home() / ".config" / "opencode"
CURRENT_CONFIG = CONFIG_DIR / "oh-my-opencode.json"
BACKUP_CONFIG = CONFIG_DIR / "oh-my-opencode.json.BAK"

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

    # First, add the current config if it exists (will be item #1)
    if CURRENT_CONFIG.exists():
        config_files.append(CURRENT_CONFIG)

    # Then add all other config files (excluding current and backup)
    for file in CONFIG_DIR.glob("oh-my-opencode*.json"):
        # Exclude the current config (already added) and backup file
        if file.name != "oh-my-opencode.json" and not file.name.endswith(".BAK"):
            config_files.append(file)

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

                def get_key():
                    fd = sys.stdin.fileno()
                    old_settings = termios.tcgetattr(fd)
                    try:
                        tty.setraw(sys.stdin.fileno())
                        ch = sys.stdin.read(1)
                        if ch == '\x1b':  # Escape sequence
                            # Read the rest of the sequence
                            ch += sys.stdin.read(2)
                        return ch
                    finally:
                        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

                key = get_key()

                # Handle key input
                if key.lower() == 'q' or key == '\x1b':  # q or Escape
                    break
                elif key == ' ' or key == '\n' or key == '\r':  # Space or Enter
                    if current_page < total_pages - 1:
                        current_page += 1
                    else:
                        break  # Exit if on last page
                elif key.lower() == 'b':  # Previous page
                    if current_page > 0:
                        current_page -= 1
                elif key == '\x1b[A':  # Up arrow
                    if current_page > 0:
                        current_page -= 1
                elif key == '\x1b[B':  # Down arrow
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


def display_menu(configs: List[Path], box: BoxChars):
    """Display the configuration selection menu"""
    clear_screen()

    print_box_header(
        "OpenCode Configuration Switcher v1.0.1",
        "Commands: 1-9=select | d#=details | q=quit | Enter=apply",
        box
    )

    print_header("\nAvailable Configurations")
    print_sep(box)

    # Print numbered list
    for idx, config in enumerate(configs, 1):
        # Add (current) label to the first item
        if idx == 1 and config == CURRENT_CONFIG:
            print(f"  {Colors.BOLD}{idx}{Colors.NC}) {Colors.GREEN}{config.name}{Colors.NC} {Colors.YELLOW}(current){Colors.NC}")
        else:
            print(f"  {Colors.BOLD}{idx}{Colors.NC}) {Colors.CYAN}{config.name}{Colors.NC}")

    print_sep(box)

    # Show backup status
    if BACKUP_CONFIG.exists():
        print(f"\n{Colors.BOLD}Backup exists:{Colors.NC} {BACKUP_CONFIG}")
    else:
        print(f"\n{Colors.YELLOW}No backup file (will be created on first switch){Colors.NC}")


def apply_config(source: Path) -> bool:
    """Apply the selected configuration"""
    # Special case: if user selects the current config (item #1), do nothing
    if source == CURRENT_CONFIG:
        log_info(f"No change - already using this configuration")
        log_info(f"Current file: {CURRENT_CONFIG}")
        return True

    try:
        # Step 1: Backup current config
        if CURRENT_CONFIG.exists():
            log_info(f"Creating backup: {BACKUP_CONFIG}")
            shutil.copy2(CURRENT_CONFIG, BACKUP_CONFIG)
            log_success("Backup created successfully")

        # Step 2: Copy selected config to current
        log_info(f"Copying {source.name} -> oh-my-opencode.json")
        shutil.copy2(source, CURRENT_CONFIG)

        log_success(f"Configuration switched to: {source.name}")
        return True

    except Exception as e:
        log_error(f"Failed to apply configuration: {e}")
        return False


def show_current_config_details(box: BoxChars):
    """Show details of current configuration"""
    if not CURRENT_CONFIG.exists():
        log_warning("No current configuration file exists")
        return

    display_config_preview(CURRENT_CONFIG, box)


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
        log_info("Expected files: oh-my-opencode-*.json (excluding oh-my-opencode.json)")
        sys.exit(1)

    # Main menu loop
    selected_index = None

    while True:
        display_menu(configs, box)

        # Get user input
        try:
            user_input = input(f"\n{Colors.BOLD}Enter selection:{Colors.NC} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n\n{Colors.YELLOW}Interrupted by user{Colors.NC}")
            sys.exit(0)

        # Handle empty input (apply selection)
        if not user_input:
            if selected_index is not None:
                print()  # Blank line for readability
                if apply_config(configs[selected_index]):
                    # Check if we actually applied a new config or just showed "no change"
                    if configs[selected_index] == CURRENT_CONFIG:
                        print(f"\n{Colors.GREEN}{Colors.BOLD}No configuration change needed{Colors.NC}")
                        sys.exit(0)
                    else:
                        print(f"\n{Colors.GREEN}{Colors.BOLD}Configuration applied successfully!{Colors.NC}")
                        print(f"{Colors.CYAN}Backup saved to: {BACKUP_CONFIG}{Colors.NC}")
                        sys.exit(0)
                else:
                    print(f"\n{Colors.RED}Failed to apply configuration{Colors.NC}")
                    input("\nPress Enter to continue...")
            else:
                log_warning("No configuration selected. Please select a number first.")
                input("Press Enter to continue...")
            continue

        # Handle quit
        if user_input.lower() == 'q':
            log_info("Exiting without changes")
            sys.exit(0)

        # Handle detail view (d#)
        if user_input.lower().startswith('d'):
            try:
                detail_num = int(user_input[1:])
                if 1 <= detail_num <= len(configs):
                    display_config_preview(configs[detail_num - 1], box)
                else:
                    log_error(f"Invalid detail number: {detail_num}")
                    input("Press Enter to continue...")
            except ValueError:
                log_error(f"Invalid detail command: {user_input}")
                input("Press Enter to continue...")
            continue

        # Handle number selection
        try:
            num = int(user_input)
            if 1 <= num <= len(configs):
                selected_index = num - 1
                selected = configs[selected_index]
                # Special message for current config selection
                if selected == CURRENT_CONFIG:
                    log_success(f"Selected: {selected.name} (current configuration)")
                    log_info("Press Enter to confirm (no change), or select another number")
                else:
                    log_success(f"Selected: {selected.name}")
                    log_info("Press Enter to apply, or select another number")
            else:
                log_error(f"Invalid selection: {num} (must be 1-{len(configs)})")
                input("Press Enter to continue...")
        except ValueError:
            log_error(f"Invalid input: {user_input}")
            log_info("Valid commands: number, d#, q, or Enter to apply")
            input("Press Enter to continue...")


if __name__ == "__main__":
    main()
