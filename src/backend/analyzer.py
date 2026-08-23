"""
TikUtils Backend: Analyzer
Handles TikTok metadata fetching, origin resolution, and downloading.
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

def resolve_origin(url: str, data: dict) -> dict:
    """Attempts to find the original, unwatermarked source URL."""
    log.info("Attempting to resolve origin URL...")

    # 1. Check yt-dlp formats for explicit origin/download tags
    for f in data.get('formats', []):
        fid = str(f.get('format_id', '')).lower()
        note = str(f.get('format_note', '')).lower()
        if any(k in fid or k in note for k in ('download', 'origin', 'source', 'original')):
            log.info(f"Origin resolved via yt-dlp format: {f.get('format_id')}")
            return {'type': 'format', 'value': f.get('format_id')}

    # 2. Fallback to parsing TikTok's web rehydration JSON
    page_url = fetch_page_origin(url)
    if page_url:
        log.info("Origin resolved via web JSON (downloadAddr)")
        return {'type': 'direct', 'value': page_url}

    log.warning("Origin URL could not be resolved for this video.")
    return {'type': None, 'value': None}

def fetch_page_origin(url: str) -> str | None:
    """Scrapes the TikTok webpage to extract downloadAddr from __UNIVERSAL_DATA_FOR_REHYDRATION__."""
    log.debug("Fetching page HTML for origin extraction...")
    try:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.addheaders = [('User-Agent', UA)]

        # Bootstrap ttwid cookie
        try:
            opener.open('https://www.tiktok.com/', timeout=10).read()
            log.debug("Successfully bootstrapped ttwid cookie.")
        except Exception as e:
            log.debug(f"Cookie bootstrap failed (non-fatal): {e}")

        html = opener.open(url, timeout=15).read().decode('utf-8', 'ignore')
        log.debug(f"Downloaded {len(html)} bytes of HTML.")

        m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            log.warning("Rehydration JSON script tag not found in HTML.")
            return None

        payload = json.loads(m.group(1))
        scope = payload.get('__DEFAULT_SCOPE__', {})
        detail = scope.get('webapp.video-detail', {}) or {}
        item = detail.get('itemStruct', {}) or {}
        video = item.get('video', {}) or {}
        addr = video.get('downloadAddr')

        if addr:
            log.debug(f"Extracted raw downloadAddr: {addr[:100]}...")
        return addr or None

    except Exception as e:
        log.error(f"Exception during page origin fetch: {e}")
        return None

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
