<div align="center">

<img src="assets/icon.svg" width="128" height="128" alt="TikUtils Icon">

# TikUtils

### Minimal TikTok Analytics & Lossless Stream Patcher app

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20GNOME-333333?style=for-the-badge&logo=linux&logoColor=white)](#installation)
[![GTK](https://img.shields.io/badge/GTK-4.0%20%7C%20Libadwaita-orange?style=for-the-badge&logo=gnome&logoColor=white)](#features)
[![Version](https://img.shields.io/badge/Version-dev--003-purple?style=for-the-badge)](VERSION)

*A native GNOME application for analyzing TikTok video streams, downloading streams, and patching MP4 containers to bypass server-side re-encoding.*

</div>

---

## ⚡ Quick Start

Get up and running in seconds. No manual dependency hunting required.

```bash
git clone https://github.com/buwryme/tikutils.git
cd tikutils
chmod +x setup.sh; ./setup.sh
```

The setup script automatically installs the application, configures desktop integration, verifies dependencies (`yt-dlp`, `ffmpeg`), and places the launcher in your PATH. Run `tikutils` from your terminal or find it in your app grid.

---

## ✨ Features

<div align="center">

| 🔍 **Stream Analyzer** | 🎬 **Lossless Patcher** | 📥 **Smart Downloader** |
| :--- | :--- | :--- |
| Extract full metadata, stats, and all available stream variants from any TikTok URL | Patch MP4 sample tables to trigger TikTok's passthrough mode, preserving HEVC Main 10 quality | Download origin sources or specific streams with XDG portal integration and progress tracking |

</div>

### 🔍 Deep Stream Analysis
-   Parses TikTok's internal API and web rehydration JSON to resolve true origin URLs
-   Displays all available video streams with codec, bitrate, resolution, and file size
-   Deduplicates identical streams by signature matching
-   Real-time metadata extraction including views, likes, comments, favorites, and shares

### 🎬 Lossless Upload Pipeline
-   **Re-encode** to HEVC Main 10 @ 20K bitrate with yuv420p10le pixel format (Best for playback)
-   **Patch MP4 structure**: Reorder moov before mdat, strip timecode tracks, normalize handler names
-   **Inflate audio stsz** by configurable factor to create deliberate sample table mismatch
-   **Inject custom udta metadata** (artist, composer, album, copyright, grouping)
-   **Append trailing dummy bytes** to confuse strict validators
-   Clean-room reverse engineered posting method — no watermarks, no quality loss

### 📥 Intelligent Downloads
-   Resolves unwatermarked origin files via TikTok's CDN when available
-   Falls back to yt-dlp format selection for standard streams
-   Native XDG Desktop Portal save dialogs — never hardcodes a download path
-   Header-integrated progress bar with activity pulsing for ffmpeg operations
-   Toast notifications for every state: downloading, saved, cancelled, failed

### 🖥️ Native GNOME Experience
-   Built with **GTK4** and **Libadwaita** — follows GNOME HIG 100%
-   Adaptive layout with `Adw.Clamp` and `Adw.NavigationView` slide transitions
-   Dynamic separators that size to content, not terminal width
-   Nerd Font icons throughout for consistent visual language
-   Very verbose timestamped logging to `~/.local/share/net.buwryy.TikUtils/logs/`

---

## 📦 Installation

### Prerequisites

| Dependency | Purpose | Install (Arch) | Install (Fedora) | Install (Ubuntu/Debian) |
|---|---|---|---|---|
| `yt-dlp` | Metadata & stream extraction | `pacman -S yt-dlp` | `dnf install yt-dlp` | `apt install yt-dlp` |
| `ffmpeg` | Video encoding & remuxing | `pacman -S ffmpeg` | `dnf install ffmpeg` | `apt install ffmpeg` |
| GTK4 + Libadwaita | UI toolkit | `pacman -S gtk4 libadwaita python-gobject` | `dnf install gtk4-devel libadwaita-devel python3-gobject` | `apt install libadwaita-1-dev python3-gi gir1.2-gtk-4.0 gir1.2-adw-1` |

### Setup:

```bash
git clone https://github.com/buwryme/tikutils.git
cd tikutils
chmod +x setup.sh; ./setup.sh
```

---

## 🏗️ Project Structure

```
tikutils/
├── setup.sh                      # One-command installer
├── VERSION                       # Version string (dev-003)
├── LICENSE                       # MIT License
├── net.buwryy.TikUtils.desktop   # Desktop entry
├── assets/
│   └── icon.svg                  # Adwaita-compliant app icon
└── src/
    ├── app.py                    # GTK4/Libadwaita application
    └── backend/
        ├── __init__.py
        ├── analyzer.py           # TikTok metadata & origin resolution
        └── patcher.py            # MP4 parsing, stsz inflation, ffmpeg encoding
```

---

## 🧠 How the Patcher Works

TikTok's ingest pipeline validates MP4 sample table consistency before deciding whether to transcode. TikUtils exploits this validation:

1.  **Encode** to HEVC Main 10 with TikTok-optimal parameters (15M bitrate, 20M maxrate, yuv420p10le)
2.  **Strip** the timecode track (`tmcd`) and track references (`tref`) that ffmpeg adds by default
3.  **Normalize** handler names to `VideoHandler` / `SoundHandler`
4.  **Inflate** the audio `stsz` (sample size table) by N× while leaving `stts` (time-to-sample) at its original count — creating a deliberate mismatch
5.  **Replace** `udta` metadata with configurable tags
6.  **Append** trailing malformed data to confuse strict validators

Strict transcoders choke on the mismatch and fall back to **passthrough**. Lenient players (TikTok's mobile decoder) ignore the mismatch and play the actual frames. The result: your uploaded video retains full quality with zero server-side re-encoding.

> ⚠️ *This is speculation based on clean-room reverse engineering of a known posting method. Behavior may change as TikTok updates their ingest pipeline.*

---

## ⚙️ Configuration

Settings are stored at `~/.local/share/net.buwryy.TikUtils/settings.json` and managed entirely through the in-app preferences UI.

| Setting | Default | Description |
|---|---|---|
| Artist / Composer / Album / Comment / Copyright / Grouping | `buwryy` | udta metadata fields embedded in the patched MP4 |
| Inflation Factor | `10` | Multiplier for audio stsz padding (higher = more aggressive mismatch) |
| Trailing Dummy Bytes | `1024` | Size of malformed data appended to file end |
| Re-encode Video | `true` | Encode to HEVC Main 10 before patching (disable to patch existing HEVC files) |

Logs are written to `~/.local/share/net.buwryy.TikUtils/logs/` with full DEBUG verbosity. Terminal output shows INFO-level messages only.

---

## 📜 License

This project is licensed under the **MIT License**. See [LICENSE](LICENSE) for details.

Clean-room reverse engineering of a known posting method. For legal inquiries, contact [@buwryy on Discord](https://discord.com).

---

<div align="center">

**Made with ♥ by [buwryme](https://github.com/buwryme)**

*If TikUtils helped you preserve video quality, consider starring the repo ⭐*

</div>
