<div align="center">

<img src="assets/icon.svg" width="128" height="128" alt="TikUtils Icon">

# tikutils

gtk4/libadwaita tiktok analytics & lossless stream patcher for linux

[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Version](https://img.shields.io/badge/Version-v1.2.1-purple?style=flat-square)](VERSION)
[![Platform](https://img.shields.io/badge/Platform-Linux-linux?style=flat-square&logo=linux&logoColor=white)](#installation)
[![GTK](https://img.shields.io/badge/GTK-4.0%20%7C%20Libadwaita-orange?style=flat-square&logo=gnome&logoColor=white)](#features)
![Patcher Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fbuwryy.net%2Fapi%2Fpatcher-status&style=flat-square)

</div>

---

Reverse engineered from Editing News' patcher and made open source.

## requirements
- python 3.10+
- ffmpeg (in `$PATH`)
- gtk4 & libadwaita

## usage

```bash
git clone https://github.com/buwryme/tikutils.git
cd tikutils
chmod +x setup.sh; ./setup.sh
```

run `tikutils` from your terminal or app grid.

## features

*   **analyzer:** fetch public video metadata and stream URLs directly from TikTok.
*   **patcher:** re-encodes to **h.264/h.265** and patches mp4 structure to force tiktok passthrough.
*   **downloader:** native save dialogs with progress tracking.
*   **maximum format:** supports any resolution and FPS! check out [this 4K60FPS test video](https://www.tiktok.com/@buwryy/video/7678998280461765910)

> [!IMPORTANT]
> playback for viewers is best up to 1080p60fps. higher resolutions/bitrates may cause streaming issues

## how it works

the default method is **slsr** (silent tail, stereo duplicate):

1.  requires an MP4 with one unencrypted AAC-LC stereo track.
2.  appends a short silent AAC tail and duplicates the audio track.
3.  keeps the video samples untouched and appends a rebuilt movie box.
4.  extends the track, movie, and edit-list durations by the tail length.

the original movie box is renamed to a free box, so the existing sample offsets stay valid. the older header-only method is available with `method = "mvhd"`, and the audio-inflation method with `method = "legacy"`.

## configuration

patcher tuning (method, inflation rate, codec-specific dummy sizes, metadata, and trailing payload) is fetched from a public `.toml` gist. if the fetch fails, the built-in copy is used. encode settings remain available in the app.

the patcher core is a git submodule of [tiktok-lossless-upload](https://github.com/buwryme/tiktok-lossless-upload). it tracks the latest `main` commit; `setup.sh` runs `git submodule update --remote`, sparse-checks out only `tiktok_lossless_patch.py`, and exposes it as `src/backend/patcher_core.py`.

---

for inquiries contact **@buwryy** on discord

<div align="center">

**made with ♥ by [buwryme](https://github.com/buwryme)**

</div>
