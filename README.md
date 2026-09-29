<div align="center">

<img src="assets/icon.svg" width="128" height="128" alt="TikUtils Icon">

# tikutils

gtk4/libadwaita tiktok analytics & lossless stream patcher for linux

[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Version](https://img.shields.io/badge/Version-v1.1.3-purple?style=flat-square)](VERSION)
[![Platform](https://img.shields.io/badge/Platform-Linux-linux?style=flat-square&logo=linux&logoColor=white)](#installation)
[![GTK](https://img.shields.io/badge/GTK-4.0%20%7C%20Libadwaita-orange?style=flat-square&logo=gnome&logoColor=white)](#features)

</div>

---

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

1.  encodes video using constant rate factor (crf) for consistent quality.
2.  duplicates the audio track and inflates its `stsz` table with dummy samples to create a structural mismatch.
3.  strips `tmcd`/`tref` tracks and normalizes handler names.
4.  injects dual `meta` boxes with custom metadata and an unknown `name` box.
5.  appends a `VOID` box and repeating trailing pattern bytes.

tiktok's transcoders choke on the mismatch and skip re-encoding. mobile decoders ignore the dummy track and play normally.

---

> works as of sep 2026. use responsibly.

for inquiries contact **@buwryy** on discord

<div align="center">

**made with ♥ by [buwryme](https://github.com/buwryme)**

</div>
