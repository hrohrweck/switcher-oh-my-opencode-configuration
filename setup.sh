#!/bin/bash
#
# setup.sh - Installer for switch_oh-my-opencode_config.py
# This script installs the configuration switcher to ~/.local/bin/
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Version
VERSION="1.0.1"

# Configuration
SCRIPT_NAME="switch_oh-my-opencode_config.py"
INSTALL_DIR="$HOME/.local/bin"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_FILE="$SOURCE_DIR/$SCRIPT_NAME"
DEST_FILE="$INSTALL_DIR/$SCRIPT_NAME"

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_box_header() {
    local title="$1"
    local width=60
    echo -e "${CYAN}${BOLD}"
    echo "┌$(printf '─%.0s' $(seq 1 $width))┐"
    echo "│ $title$(printf ' %.0s' $(seq 1 $((width - ${#title} - 2))))│"
    echo "└$(printf '─%.0s' $(seq 1 $width))┘"
    echo -e "${NC}"
}

# Main installation
main() {
    print_box_header "OpenCode Configuration Switcher Installer v$VERSION"

    # Check if source file exists
    if [ ! -f "$SOURCE_FILE" ]; then
        log_error "Source file not found: $SOURCE_FILE"
        log_info "Please run this script from the project directory"
        exit 1
    fi

    log_info "Source file found: $SOURCE_FILE"

    # Create install directory if it doesn't exist
    if [ ! -d "$INSTALL_DIR" ]; then
        log_info "Creating installation directory: $INSTALL_DIR"
        mkdir -p "$INSTALL_DIR" || {
            log_error "Failed to create installation directory"
            exit 1
        }
        log_success "Installation directory created"
    else
        log_info "Installation directory exists: $INSTALL_DIR"
    fi

    # Check if install directory is in PATH
    if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
        log_warning "$INSTALL_DIR is not in your PATH"
        log_info "You may need to add it to your PATH:"
        echo ""
        echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo ""
        log_info "Add this to your ~/.bashrc or ~/.zshrc"
    else
        log_success "$INSTALL_DIR is in your PATH"
    fi

    # Copy the script
    log_info "Installing $SCRIPT_NAME to $INSTALL_DIR"
    if cp "$SOURCE_FILE" "$DEST_FILE"; then
        log_success "Script copied successfully"
    else
        log_error "Failed to copy script"
        exit 1
    fi

    # Make it executable
    log_info "Making script executable"
    if chmod +x "$DEST_FILE"; then
        log_success "Script is now executable"
    else
        log_error "Failed to make script executable"
        exit 1
    fi

    # Verify installation
    if [ -x "$DEST_FILE" ]; then
        echo ""
        log_success "Installation completed successfully!"
        echo ""
        echo -e "${CYAN}${BOLD}Installed:${NC} $DEST_FILE"
        echo -e "${CYAN}${BOLD}Command:${NC}  $SCRIPT_NAME"
        echo ""
        log_info "You can now run: $SCRIPT_NAME"
    else
        log_error "Installation verification failed"
        exit 1
    fi
}

# Run main function
main
