#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════════════════════
# TikUtils Setup Script
# ═══════════════════════════════════════════════════════════════════════════════

APP_ID="net.buwryy.TikUtils"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

bin_dir="$HOME/.local/bin"
install_dir="$HOME/.local/share/$APP_ID/installation"
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

echo ""
info "Creating directory structure..."
mkdir -p "$bin_dir" "$install_dir"
mkdir -p "$HOME/.local/share/applications"
mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$HOME/.local/share/$APP_ID/logs"
success "Directories created"

echo ""
info "═══════════════════════════════════════"
info "Checking & Installing Dependencies"
info "═══════════════════════════════════════"

# Detect package manager and install missing dependencies
install_deps() {
    local pkgs_ytdlp=""
    local pkgs_ffmpeg=""
    local pkgs_gtk=""

    if command -v apt-get &> /dev/null; then
        info "Detected apt-get (Debian/Ubuntu)"
        pkgs_ytdlp="yt-dlp"
        pkgs_ffmpeg="ffmpeg"
        pkgs_gtk="python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1"

        local to_install=()
        command -v yt-dlp &> /dev/null || to_install+=($pkgs_ytdlp)
        command -v ffmpeg &> /dev/null || to_install+=($pkgs_ffmpeg)
        python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')" &> /dev/null || to_install+=($pkgs_gtk)

        if [ ${#to_install[@]} -gt 0 ]; then
            info "Installing: ${to_install[*]}"
            sudo apt-get update && sudo apt-get install -y "${to_install[@]}"
        fi

    elif command -v pacman &> /dev/null; then
        info "Detected pacman (Arch Linux)"
        pkgs_ytdlp="yt-dlp"
        pkgs_ffmpeg="ffmpeg"
        pkgs_gtk="python-gobject gtk4 libadwaita"

        local to_install=()
        command -v yt-dlp &> /dev/null || to_install+=($pkgs_ytdlp)
        command -v ffmpeg &> /dev/null || to_install+=($pkgs_ffmpeg)
        python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')" &> /dev/null || to_install+=($pkgs_gtk)

        if [ ${#to_install[@]} -gt 0 ]; then
            info "Installing: ${to_install[*]}"
            sudo pacman -Sy --needed --noconfirm "${to_install[@]}"
        fi

    elif command -v dnf &> /dev/null; then
        info "Detected dnf (Fedora)"
        pkgs_ytdlp="yt-dlp"
        pkgs_ffmpeg="ffmpeg"
        pkgs_gtk="python3-gobject gtk4 libadwaita"

        local to_install=()
        command -v yt-dlp &> /dev/null || to_install+=($pkgs_ytdlp)
        command -v ffmpeg &> /dev/null || to_install+=($pkgs_ffmpeg)
        python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')" &> /dev/null || to_install+=($pkgs_gtk)

        if [ ${#to_install[@]} -gt 0 ]; then
            info "Installing: ${to_install[*]}"
            sudo dnf install -y "${to_install[@]}"
        fi

    elif command -v zypper &> /dev/null; then
        info "Detected zypper (openSUSE)"
        pkgs_ytdlp="yt-dlp"
        pkgs_ffmpeg="ffmpeg"
        pkgs_gtk="python3-gobject gtk4 libadwaita-1-0 typelib-1_0-Adw-1"

        local to_install=()
        command -v yt-dlp &> /dev/null || to_install+=($pkgs_ytdlp)
        command -v ffmpeg &> /dev/null || to_install+=($pkgs_ffmpeg)
        python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')" &> /dev/null || to_install+=($pkgs_gtk)

        if [ ${#to_install[@]} -gt 0 ]; then
            info "Installing: ${to_install[*]}"
            sudo zypper install -y "${to_install[@]}"
        fi
    else
        warn "No supported package manager found (apt, pacman, dnf, zypper)."
        warn "Please ensure yt-dlp, ffmpeg, and GTK4/Libadwaita Python bindings are installed manually."
    fi
}

install_deps

# Final verification
echo ""
for cmd in yt-dlp ffmpeg; do
    if command -v $cmd &> /dev/null; then
        success "$cmd is installed"
    else
        error "$cmd is still missing. Installation may have failed."
    fi
done

if python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')" &> /dev/null; then
    success "Python GTK4 & Libadwaita bindings verified"
else
    error "Python GTK4/Libadwaita bindings are still missing."
fi

echo ""
info "═══════════════════════════════════════"
info "Installing Application Files"
info "═══════════════════════════════════════"

cp -r "$script_dir/src" "$install_dir/"
if [[ -f "$script_dir/VERSION" ]]; then
    cp "$script_dir/VERSION" "$install_dir/src/VERSION"
    success "Version file installed"
else
    warn "VERSION file not found, app will report 'unknown'"
fi
success "Application files installed"

cat > "$bin_dir/tikutils" << LAUNCHER
#!/usr/bin/env bash
export PATH="\$HOME/.local/bin:\$PATH"
cd "$install_dir/src"
exec python3 app.py "\$@"
LAUNCHER
chmod +x "$bin_dir/tikutils"
success "TikUtils installed to $bin_dir/tikutils"

echo ""
info "═══════════════════════════════════════"
info "Desktop Integration"
info "═══════════════════════════════════════"

cp "$script_dir/assets/icon.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
chmod 644 "$HOME/.local/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
success "Icon installed"

# Copy the desktop file and dynamically replace the placeholder with the absolute path
cp "$script_dir/net.buwryy.TikUtils.desktop" "$HOME/.local/share/applications/${APP_ID}.desktop"
sed -i "s|@BINDIR@|$bin_dir|g" "$HOME/.local/share/applications/${APP_ID}.desktop"
chmod 644 "$HOME/.local/share/applications/${APP_ID}.desktop"

update-desktop-database "$HOME/.local/share/applications" &> /dev/null || true
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" &> /dev/null || true
success "Desktop entry installed"

echo ""
echo -e "${GREEN}TikUtils setup complete!${NC}"
echo "To run: tikutils"

if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
  warn "NOTE: $bin_dir is not in your PATH!"
  info "Add this line to your ~/.bashrc or ~/.zshrc:"
  echo '  export PATH="$HOME/.local/bin:$PATH"'
fi
