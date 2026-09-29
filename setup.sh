#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════════════════════
# TikUtils Installer
# usage: chmod +x setup.sh && ./setup.sh
# ═══════════════════════════════════════════════════════════════════════════════

APP_ID="net.buwryy.TikUtils"

if [[ -t 1 ]]; then
    B='\033[1m'; BLUE='\033[34m'; GREEN='\033[32m'; YELLOW='\033[33m'
    RED='\033[31m'; DIM='\033[2m'; NC='\033[0m'
    SPIN_FRAMES=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
else
    B=''; BLUE=''; GREEN=''; YELLOW=''; RED=''; DIM=''; NC=''
    SPIN_FRAMES=()
fi

spin_pid=""

start_spin() {
    [[ ${#SPIN_FRAMES[@]} -eq 0 ]] && return
    local msg="$1"
    (
        local i=0
        while true; do
            printf "\r  ${BLUE}${SPIN_FRAMES[$((i % ${#SPIN_FRAMES[@]}))]}${NC} %s  " "$msg"
            ((i += 1))
            sleep 0.12
        done
    ) &
    spin_pid=$!
    disown $spin_pid 2>/dev/null || true
}

stop_spin() {
    if [[ -n "$spin_pid" ]]; then
        kill "$spin_pid" 2>/dev/null || true
        wait "$spin_pid" 2>/dev/null || true
        spin_pid=""
        if [[ -t 1 ]]; then
            printf "\r\033[K"
        fi
    fi
}

run_progress() {
    local label="$1"; shift
    local log status
    log=$(mktemp)
    start_spin "$label..."
    if "$@" >"$log" 2>&1; then
        stop_spin
        ok "$label"
        rm -f "$log"
    else
        status=$?
        stop_spin
        cat "$log" >&2
        rm -f "$log"
        die "$label failed (exit $status)"
    fi
}

trap 'stop_spin' EXIT

say()  { printf "${BLUE}  →${NC} %s\n" "$*"; }
ok()   { stop_spin; printf "${GREEN}  ✓${NC} %s\n" "$*"; }
warn() { stop_spin; printf "${YELLOW}  !${NC} %s\n" "$*"; }
die()  { stop_spin; printf "${RED}  ✗${NC} %s\n" "$*" >&2; exit 1; }
section() { stop_spin; printf "\n${BLUE}${B}%s${NC}\n" "$*"; }

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "missing $1 — install it first"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

bin_dir="$HOME/.local/bin"
install_dir="$HOME/.local/share/$APP_ID/installation"

section "dependencies"
need_cmd python3
need_cmd mkdir
need_cmd cp

# Detect package manager for system-level deps
detect_deps() {
    local to_install=()
    
    # Check ffmpeg
    command -v ffmpeg &> /dev/null || to_install+=("ffmpeg")
    
    # Check GTK4/Adw bindings
    if ! python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')" &> /dev/null; then
        if command -v apt-get &> /dev/null; then
            to_install+=("python3-gi" "python3-gi-cairo" "gir1.2-gtk-4.0" "gir1.2-adw-1")
        elif command -v pacman &> /dev/null; then
            to_install+=("python-gobject" "gtk4" "libadwaita")
        elif command -v dnf &> /dev/null; then
            to_install+=("python3-gobject" "gtk4" "libadwaita")
        elif command -v zypper &> /dev/null; then
            to_install+=("python3-gobject" "gtk4" "libadwaita-1-0" "typelib-1_0-Adw-1")
        else
            warn "unsupported package manager. please install gtk4/libadwaita python bindings manually."
            return
        fi
    fi

    if [ ${#to_install[@]} -gt 0 ]; then
        say "installing system dependencies: ${to_install[*]}"
        if command -v sudo &> /dev/null; then
            if command -v apt-get &> /dev/null; then
                run_progress "apt update + install" sudo apt-get update && sudo apt-get install -y "${to_install[@]}"
            elif command -v pacman &> /dev/null; then
                run_progress "pacman install" sudo pacman -Sy --needed --noconfirm "${to_install[@]}"
            elif command -v dnf &> /dev/null; then
                run_progress "dnf install" sudo dnf install -y "${to_install[@]}"
            elif command -v zypper &> /dev/null; then
                run_progress "zypper install" sudo zypper install -y "${to_install[@]}"
            fi
        else
            die "sudo required for dependency installation"
        fi
    else
        ok "dependencies satisfied"
    fi
}

detect_deps

section "directory structure"
run_progress "creating dirs" mkdir -p "$bin_dir" "$install_dir" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps" "$HOME/.local/share/$APP_ID/logs"

section "installing files"
run_progress "copying src" cp -r "$SCRIPT_DIR/src" "$install_dir/"

if [[ -f "$SCRIPT_DIR/VERSION" ]]; then
    cp "$SCRIPT_DIR/VERSION" "$install_dir/src/VERSION"
    ok "version file installed"
else
    warn "VERSION file missing"
fi

cat > "$bin_dir/tikutils" << LAUNCHER
#!/usr/bin/env bash
export PATH="\$HOME/.local/bin:\$PATH"
cd "$install_dir/src"
exec python3 app.py "\$@"
LAUNCHER
chmod +x "$bin_dir/tikutils"
ok "binary linked at $bin_dir/tikutils"

section "desktop integration"
if [[ -f "$SCRIPT_DIR/assets/icon.svg" ]]; then
    cp "$SCRIPT_DIR/assets/icon.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
    chmod 644 "$HOME/.local/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
    ok "icon installed"
fi

if [[ -f "$SCRIPT_DIR/net.buwryy.TikUtils.desktop" ]]; then
    cp "$SCRIPT_DIR/net.buwryy.TikUtils.desktop" "$HOME/.local/share/applications/${APP_ID}.desktop"
    sed -i "s|@BINDIR@|$bin_dir|g" "$HOME/.local/share/applications/${APP_ID}.desktop"
    chmod 644 "$HOME/.local/share/applications/${APP_ID}.desktop"
    
    run_progress "updating desktop db" update-desktop-database "$HOME/.local/share/applications"
    run_progress "updating icon cache" gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor"
    ok "desktop entry configured"
fi

section "cleanup"
find "$HOME/.local/share/$APP_ID" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
ok "caches cleared"

printf "\n${BLUE}${B}done${NC}\n"
printf "  executable: ${DIM}%s${NC}\n" "$bin_dir/tikutils"
printf "  data:       ${DIM}%s${NC}\n" "$install_dir"
printf "\n${B}setup finished!!${NC}\n"

if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
  warn "NOTE: $bin_dir is not in your PATH"
  info "add this to your shell rc:"
  echo '  export PATH="$HOME/.local/bin:$PATH"'
fi