"""
TikUtils Backend: Analyzer
Handles TikTok metadata fetching and downloading.
"""
import json
import subprocess
import re
import http.cookiejar
import urllib.request
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger("TikUtils.analyzer")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

def fetch_metadata(url: str) -> dict:
    """Fetches video metadata using yt-dlp."""
    log.info(f"Fetching metadata for: {url}")
    cmd = [
        "yt-dlp", "--dump-single-json", "--no-warnings", "--quiet",
        "--no-check-certificates", "--extractor-args", "tiktok:api_hostname=www.tiktok.com", url
    ]

    log.debug(f"Running command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        error_msg = result.stderr.strip().split('\n')[-1] or "Invalid URL"
        log.error(f"yt-dlp failed: {error_msg}")
        raise RuntimeError(error_msg)

    data = json.loads(result.stdout)
    log.debug(f"Raw metadata keys: {list(data.keys())}")
    return data


def download_ytdlp(url: str, format_selector: str, dest_path: str, progress_cb):
    """Downloads a specific stream using yt-dlp."""
    log.info(f"Downloading stream {format_selector} to {dest_path}")
    cmd = [
        "yt-dlp", "-f", format_selector, "-o", dest_path,
        "--no-warnings", "--newline", "--no-check-certificates",
        "--extractor-args", "tiktok:api_hostname=www.tiktok.com", url
    ]

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in iter(process.stdout.readline, ''):
        stripped = line.strip()
        log.debug(f"yt-dlp: {stripped}")
        if '[download]' in stripped and '%' in stripped:
            try:
                pct = float(stripped.split()[1].replace('%', ''))
                progress_cb(pct / 100.0)
            except ValueError:
                pass

    process.wait()
    if process.returncode != 0:
        log.error("yt-dlp download process failed.")
        raise RuntimeError("yt-dlp failed")
    log.info("yt-dlp download complete.")

def download_direct(url: str, dest_path: str, progress_cb):
    """Downloads a raw CDN URL directly using urllib."""
    log.info(f"Downloading direct URL to {dest_path}")
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Referer': 'https://www.tiktok.com/'
    })

    with urllib.request.urlopen(req, timeout=60) as resp, open(dest_path, 'wb') as out:
        total = int(resp.headers.get('Content-Length', 0) or 0)
        log.debug(f"Content-Length: {total} bytes")
        done = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total:
                progress_cb(done / total)
    log.info("Direct download complete.")
