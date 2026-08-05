#!/bin/bash
#
# setup.sh - Installer for opencode-config-switcher
# Requires Python 3.11+. Installs via pipx (preferred) or pip --user.
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

print_box_header() {
    local title="$1"
    local width=60
    echo -e "${CYAN}${BOLD}"
    echo "┌$(printf '─%.0s' $(seq 1 $width))┐"
    echo "│ $title$(printf ' %.0s' $(seq 1 $((width - ${#title} - 2))))│"
    echo "└$(printf '─%.0s' $(seq 1 $width))┘"
    echo -e "${NC}"
}

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Python version check ──────────────────────────────────────────

PYTHON=""
for candidate in python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || true)
        if [ "$ver" = "(3, 11)" ] || [ "$ver" = "(3, 12)" ] || [ "$ver" = "(3, 13)" ] || [ "$ver" = "(3, 14)" ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    detected=$("${candidate:-python3}" --version 2>&1 || echo "none")
    log_error "Python 3.11+ is required. Detected: $detected"
    log_info "Install Python 3.11+ via https://python.org or your package manager"
    exit 1
fi

log_info "Using Python: $($PYTHON --version)"

# ── Install ────────────────────────────────────────────────────────

print_box_header "openCode Configuration Switcher Installer"

if command -v pipx &>/dev/null; then
    log_info "Installing with pipx..."
    pipx install --force "$SOURCE_DIR"
    log_success "pipx installation complete"
else
    log_info "pipx not found — using pip --user"
    if ! "$PYTHON" -m pip install --user --upgrade "$SOURCE_DIR" 2>/tmp/opencode-setup-pip-err.$$; then
        pip_err=$(cat /tmp/opencode-setup-pip-err.$$ 2>/dev/null || true)
        rm -f /tmp/opencode-setup-pip-err.$$
        if echo "$pip_err" | grep -q "externally-managed-environment"; then
            log_error "This Python environment is externally managed (PEP 668)."
            echo ""
            echo "  Options:"
            echo "    1. Install pipx and re-run this script:"
            echo "       brew install pipx   # macOS"
            echo "       pipx ensurepath"
            echo ""
            echo "    2. Create and use an isolated virtual environment for the command."
            echo ""
            echo "  The script will not add --break-system-packages automatically."
        else
            log_error "pip install failed:"
            echo "$pip_err"
        fi
        exit 1
    fi
    rm -f /tmp/opencode-setup-pip-err.$$
    log_success "pip install complete"
fi

# ── Verify ─────────────────────────────────────────────────────────

EXPECTED="2.0.0"
ACTUAL=$(opencode-config-switcher --version 2>/dev/null || echo "")
if [ "$ACTUAL" = "$EXPECTED" ]; then
    log_success "Verified: opencode-config-switcher $ACTUAL"
else
    log_error "Version mismatch: expected $EXPECTED, got ${ACTUAL:-error}"
    exit 1
fi

echo ""
log_success "Installation complete!"
echo ""
echo "  Commands:"
echo "    opencode-config-switcher"
echo "    switch_oh-my-opencode_config.py  (legacy alias)"
echo ""

# PATH check
BIN_DIR=""
if command -v pipx &>/dev/null; then
    BIN_DIR="$HOME/.local/bin"
else
    BIN_DIR=$("$PYTHON" -m site --user-base 2>/dev/null || echo "$HOME/.local")/bin
fi

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    log_warning "$BIN_DIR is not in your PATH"
    echo ""
    echo "  Add to your shell profile:"
    echo "    export PATH=\"$BIN_DIR:\$PATH\""
    echo ""
fi
