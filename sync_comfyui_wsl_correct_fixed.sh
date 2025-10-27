#!/bin/bash
# ComfyUI Sync - Correct WSL rsync approach
# Uses --rsync-path="wsl -e rsync" with WSL-style paths

# Don't exit on error - handle errors manually

# Configuration
REMOTE_HOST="yuji@zbox-efh5kc5oso"
LOCAL_BASE="/home/yuji/Code/comfyui-runpod/workspace"
REMOTE_BASE="/mnt/c/Users/yuji/UmeAiRT/ComfyUI_windows_portable/ComfyUI"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Create local directories
create_local_dirs() {
    log_info "Creating local directories..."
    
    mkdir -p "$LOCAL_BASE/workflows"
    mkdir -p "$LOCAL_BASE/input"
    mkdir -p "$LOCAL_BASE/output"
    
    log_success "Local directories created"
}

# Sync workflows - correct WSL rsync approach
sync_workflows() {
    log_info "Syncing workflows (Windows → Linux via WSL rsync)..."
    
    if rsync -avz --progress --rsync-path="wsl -e rsync" "$REMOTE_HOST:$REMOTE_BASE/user/default/workflows/" "$LOCAL_BASE/workflows/"; then
        log_success "Workflows synced successfully"
        return 0
    else
        log_warning "Failed to sync workflows"
        return 1
    fi
}

# Sync input - correct WSL rsync approach
sync_input() {
    log_info "Syncing input (Windows → Linux via WSL rsync)..."
    
    if rsync -avz --progress --rsync-path="wsl -e rsync" "$REMOTE_HOST:$REMOTE_BASE/input/" "$LOCAL_BASE/input/"; then
        log_success "Input synced successfully"
        return 0
    else
        log_warning "Failed to sync input"
        return 1
    fi
}

# Sync output - correct WSL rsync approach
sync_output() {
    log_info "Syncing output (Windows → Linux via WSL rsync)..."
    
    if rsync -avz --progress --rsync-path="wsl -e rsync" "$REMOTE_HOST:$REMOTE_BASE/output/" "$LOCAL_BASE/output/"; then
        log_success "Output synced successfully"
        return 0
    else
        log_warning "Failed to sync output"
        return 1
    fi
}

# Main sync function
main_sync() {
    log_info "Starting ComfyUI directory sync (Windows → Linux via WSL rsync)..."
    echo "=============================================================="
    
    create_local_dirs
    
    local success_count=0
    
    # Sync workflows
    log_info "=== SYNCING WORKFLOWS ==="
    if sync_workflows; then
        ((success_count++))
    fi
    echo ""
    
    # Sync input
    log_info "=== SYNCING INPUT ==="
    sleep 2  # Brief pause between syncs
    if sync_input; then
        ((success_count++))
    fi
    echo ""
    
    # Sync output
    log_info "=== SYNCING OUTPUT ==="
    sleep 2  # Brief pause between syncs
    if sync_output; then
        ((success_count++))
    fi
    echo ""
    
    echo "=============================================================="
    if [ $success_count -eq 3 ]; then
        log_success "All directories synced successfully ($success_count/3)"
    else
        log_warning "Partial sync completed ($success_count/3 directories)"
    fi
}

# Show help
show_help() {
    echo "ComfyUI Sync (Windows → Linux via WSL rsync)"
    echo "============================================"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -h, --help     Show this help message"
    echo ""
    echo "This script uses --rsync-path=\"wsl -e rsync\" to run the remote"
    echo "rsync inside WSL on Windows, using WSL-style paths (/mnt/c/...)."
    echo ""
}

# Parse command line arguments
case "${1:-}" in
    -h|--help)
        show_help
        exit 0
        ;;
    "")
        main_sync
        ;;
    *)
        log_error "Unknown option: $1"
        show_help
        exit 1
        ;;
esac












