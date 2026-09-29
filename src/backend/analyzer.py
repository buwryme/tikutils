"""Read public TikTok video metadata and download its exposed streams."""
import functools
import html
import json
import logging
import re
import subprocess
import time
import traceback
import urllib.parse
import urllib.request

log = logging.getLogger("TikUtils.analyzer")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "Chrome/149.0.0.0 Safari/537.36")


def fetch_metadata(url: str) -> dict:
    """Fetch and normalize the public hydration payload for one TikTok video."""
    raw, canonical_url = _fetch_page_payload(url)
    video_id = str(raw.get('id') or (raw.get('video') or {}).get('id') or '')
    mobile_raw = _fetch_mobile_item(video_id) if video_id else {}
    stats = raw.get('stats') or raw.get('statistics') or {}
    video = raw.get('video') or {}
    author = raw.get('author') or raw.get('authorInfo') or {}
    formats = _formats(video)
    upload_source = _inspect_upload_source(video)
    vq_score = _vq_score(raw, mobile_raw)
    # Patched/repacked MP4s commonly lose both a usable bitrate and TikTok's
    # VQ score. Treat that pair as a strong desktop-upload heuristic if the
    # bounded header probe did not yield a source classification.
    has_zero_bitrate = any(
        entry.get('Bitrate') == 0 or entry.get('bit_rate') == 0
        for entry in (video.get('bitrateInfo') or video.get('bit_rate') or [])
        if isinstance(entry, dict)
    ) or (_int(video.get('bitrate')) == 0)
    if upload_source is None and has_zero_bitrate and vq_score is None:
        upload_source = 'Desktop'
    info = {
        'id': str(raw.get('id') or video.get('id') or ''),
        'webpage_url': canonical_url,
        'description': raw.get('desc') or raw.get('description') or '',
        'uploader': author.get('uniqueId') or author.get('unique_id') or '',
        'uploader_id': author.get('id') or author.get('uid'),
        'channel': author.get('nickname') or '',
        'track': (raw.get('music') or {}).get('title') or 'original sound',
        'artist': (raw.get('music') or {}).get('authorName') or '',
        'timestamp': _int(raw.get('createTime') or raw.get('create_time')),
        'duration': _int(video.get('duration')),
        'view_count': _int(_first_value(stats.get('playCount'), stats.get('play_count'), stats.get('view_count'))),
        'like_count': _int(_first_value(stats.get('diggCount'), stats.get('digg_count'), stats.get('like_count'))),
        'comment_count': _int(_first_value(stats.get('commentCount'), stats.get('comment_count'))),
        'repost_count': _int(_first_value(stats.get('shareCount'), stats.get('share_count'))),
        'bookmark_count': _int(_first_value(stats.get('collectCount'), stats.get('collect_count'), stats.get('favoriteCount'))),
        'width': _int(video.get('width')),
        'height': _int(video.get('height')),
        'formats': formats,
        'thumbnail': video.get('originCover') or video.get('cover'),
        'region_code': raw.get('locationCreated'),
        'upload_source': upload_source,
        'vq_score': vq_score,
        'categories': _categories(raw),
        '_tiktok_raw': raw,
        '_tiktok_mobile_raw': mobile_raw,
        '_tiktok_public_status': {
            'prohibited': raw.get('isProhibited'),
            'reviewing': raw.get('isReviewing'),
            'taken_down': raw.get('takeDown'),
            'warnings': raw.get('warnInfo'),
        },
        '_tiktok_follower_count': _int((raw.get('authorStats') or {}).get('followerCount')),
        '_tiktok_download_available': _download_available(raw),
    }
    info['_tikutils'] = normalize_metadata(info)
    log.debug("TikTok raw metadata keys: %s", sorted(raw.keys()))
    return info


def _fetch_page_payload(url: str) -> tuple[dict, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise RuntimeError("Enter a valid TikTok video URL")
    host = parsed.hostname.lower()
    if host != 'tiktok.com' and not host.endswith('.tiktok.com'):
        raise RuntimeError("URL must be from TikTok")

    request = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Referer': 'https://www.tiktok.com/',
        'Accept': 'text/html,application/xhtml+xml',
    })
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            page = response.read().decode('utf-8', errors='replace')
            canonical_url = response.geturl()
    except Exception as exc:
        raise RuntimeError(f"TikTok request failed: {exc}") from exc

    # vt.tiktok.com and vm.tiktok.com links normally redirect to the canonical
    # video page.  Some redirect chains finish on a TikTok page without the ID
    # in its URL, in which case the hydrated item is still usable below.
    final_host = (urllib.parse.urlparse(canonical_url).hostname or '').lower()
    if final_host != 'tiktok.com' and not final_host.endswith('.tiktok.com'):
        raise RuntimeError("TikTok short link redirected outside TikTok")
    video_id = _video_id(canonical_url) or _video_id(url)
    matches = re.findall(
        r'<script[^>]+id=["\'](?:SIGI_STATE|__UNIVERSAL_DATA_FOR_REHYDRATION__)["\'][^>]*>(.*?)</script>',
        page, re.S | re.I)
    for content in matches:
        try:
            payload = json.loads(html.unescape(content))
        except json.JSONDecodeError:
            continue
        candidates = []
        _walk_items(payload, candidates)
        if video_id:
            item = next((obj for obj in candidates if str(obj.get('id')) == video_id), None)
        else:
            item = candidates[0] if candidates else None
        if item:
            return item, canonical_url
    raise RuntimeError("TikTok did not return public metadata for this video")


def _video_id(url: str):
    match = re.search(r'/video/(\d+)', urllib.parse.urlparse(url).path)
    return match.group(1) if match else None


def _walk_items(value, found):
    if isinstance(value, dict):
        if value.get('id') is not None and isinstance(value.get('stats'), dict) and isinstance(value.get('video'), dict):
            found.append(value)
        for child in value.values():
            _walk_items(child, found)
    elif isinstance(value, list):
        for child in value:
            _walk_items(child, found)


def _formats(video: dict) -> list:
    formats = []
    for stream in video.get('bitrateInfo') or video.get('bit_rate') or []:
        addr = stream.get('PlayAddr') or stream.get('play_addr') or {}
        urls = addr.get('UrlList') or addr.get('url_list') or []
        if isinstance(urls, str):
            urls = [urls]
        if not urls:
            continue
        width = _int(addr.get('Width') or addr.get('width') or video.get('width'))
        height = _int(addr.get('Height') or addr.get('height') or video.get('height'))
        bitrate = _int(stream.get('Bitrate') or stream.get('bit_rate')) or _int(video.get('bitrate')) or 0
        fps = _int(stream.get('BitrateFPS') or stream.get('FPS') or stream.get('fps'))
        gear = stream.get('GearName') or stream.get('gear_name') or stream.get('format_id') or 'stream'
        size = _int(addr.get('DataSize') or addr.get('data_size'))
        for index, stream_url in enumerate(urls):
            formats.append({
                'format_id': f"{gear}-{index}", 'url': stream_url,
                'width': width, 'height': height, 'fps': fps,
                'tbr': bitrate / 1000, 'filesize': size,
                'vcodec': stream.get('CodecType') or stream.get('codec_type') or video.get('codecType') or 'h264',
                'acodec': 'aac', 'ext': 'mp4',
            })
    # Some TikTok page variants expose only the top-level play address.
    if not formats:
        addr = video.get('PlayAddrStruct') or video.get('play_addr') or {}
        urls = addr.get('UrlList') or addr.get('url_list') or []
        if isinstance(urls, str):
            urls = [urls]
        for index, stream_url in enumerate(urls):
            formats.append({
                'format_id': f"play-{index}", 'url': stream_url,
                'width': _int(addr.get('Width') or video.get('width')),
                'height': _int(addr.get('Height') or video.get('height')),
                'fps': None, 'tbr': (_int(video.get('bitrate')) or 0) / 1000,
                'filesize': _int(addr.get('DataSize') or video.get('size')),
                'vcodec': video.get('codecType') or 'h264', 'acodec': 'aac', 'ext': 'mp4',
            })
    return formats


def _fetch_mobile_item(video_id: str) -> dict:
    """Try TikTok's unsigned mobile detail endpoint; it may reject unsigned calls."""
    endpoint = 'https://api16-normal-c-useast1a.tiktokv.com/aweme/v1/multi/aweme/detail/'
    query = urllib.parse.urlencode({
        'device_platform': 'android', 'aid': '1233', 'app_name': 'musical_ly',
        'version_code': '350103', 'version_name': '35.1.3', 'channel': 'googleplay',
        'os': 'android', 'os_version': '13', 'device_type': 'SM-S908E',
        'language': 'en', 'region': 'GB',
    })
    body = urllib.parse.urlencode({
        'aweme_ids': json.dumps([video_id]), 'request_source': '0',
    }).encode()
    request = urllib.request.Request(
        f'{endpoint}?{query}', data=body, headers={
            'User-Agent': 'com.zhiliaoapp.musically/2023501030 (Linux; U; Android 13; en_GB; SM-S908E; Build/TP1A.220624.014)',
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
        })
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            payload = response.read()
        if not payload:
            return {}
        data = json.loads(payload)
        found = []
        _walk_mobile_items(data, found)
        return next((item for item in found if str(item.get('aweme_id') or item.get('id')) == video_id),
                    found[0] if found else {})
    except Exception as exc:
        log.debug("TikTok mobile detail endpoint unavailable: %s", exc)
        return {}


def _walk_mobile_items(value, found):
    if isinstance(value, dict):
        if value.get('video') and (value.get('statistics') or value.get('stats')):
            found.append(value)
        for child in value.values():
            _walk_mobile_items(child, found)
    elif isinstance(value, list):
        for child in value:
            _walk_mobile_items(child, found)


def _categories(raw: dict) -> list:
    labels = raw.get('diversificationLabels') or raw.get('video_label') or raw.get('videoLabel') or []
    tags = raw.get('video_tag') or raw.get('videoTag') or []
    categories = []
    for value in [*(labels if isinstance(labels, list) else [labels]), *(tags if isinstance(tags, list) else [tags])]:
        if isinstance(value, str):
            categories.append(value)
        elif isinstance(value, dict):
            label = value.get('name') or value.get('label') or value.get('title')
            if label:
                categories.append(label)
    return list(dict.fromkeys(categories))


def _vq_score(raw: dict, mobile_raw: dict | None = None):
    """Return TikTok's VQScore when the item payload contains a computed score."""
    candidates = [raw, raw.get('video') or {}, mobile_raw or {},
                  (mobile_raw or {}).get('video') or {}]
    for item in candidates:
        value = _first_value(item.get('VQScore'), item.get('vq_score'), item.get('vqScore'))
        if value is None or str(value).strip() in ('', '0', '0.0'):
            continue
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            continue
    return None


def _download_available(raw: dict) -> bool:
    video = raw.get('video') or {}
    control = raw.get('video_control') or raw.get('videoControl') or {}
    allow = _first_value(control.get('allow_download'), control.get('allowDownload'))
    return bool(video.get('downloadAddr') or video.get('download_addr')) and allow is not False


def _inspect_upload_source(video: dict) -> str | None:
    """Inspect a bounded MP4 prefix for retained encoder tags.

    TikTok commonly strips or replaces these tags during transcoding. This
    deliberately avoids guessing from bitrate or resolution alone.
    """
    urls = []
    for candidate in (video.get('downloadAddr'), video.get('download_addr'),
                      video.get('playAddr'), video.get('play_addr')):
        if isinstance(candidate, str):
            urls.append(candidate)
    for stream in video.get('bitrateInfo') or video.get('bit_rate') or []:
        address = stream.get('PlayAddr') or stream.get('play_addr') or {}
        urls.extend(address.get('UrlList') or address.get('url_list') or [])
    # The web play URL is sometimes an HTML 404, while the same signed path
    # served through TikTok's mobile API host redirects to the actual MP4 CDN.
    api_urls = [url.replace('https://www.tiktok.com/aweme/v1/play/',
                             'https://api16-normal-c-useast1a.tiktokv.com/aweme/v1/play/')
                for url in urls if '://www.tiktok.com/aweme/v1/play/' in url]
    urls = list(dict.fromkeys(api_urls + urls))
    if not urls:
        log.error("MP4 upload-source probe has no media URLs in TikTok video object")
        return None
    limit = 256 * 1024
    media_prefix = None
    failures = []
    for index, url in enumerate(urls, start=1):
        parsed = urllib.parse.urlparse(url)
        endpoint = f"{parsed.hostname or 'unknown'}{parsed.path[:80]}"
        request = urllib.request.Request(url, headers={
            'User-Agent': UA,
            'Referer': 'https://www.tiktok.com/',
            'Accept': 'video/mp4,*/*',
            'Range': f'bytes=0-{limit - 1}',
        })
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                # Bound reads even if a CDN ignores the Range header.
                prefix = response.read(limit)
                log.debug("MP4 probe candidate %d/%d: host=%s status=%s type=%s bytes=%d range=%s",
                          index, len(urls), response.url.split('/')[2], response.status,
                          response.headers.get('Content-Type'), len(prefix),
                          response.headers.get('Content-Range'))
                if b'ftyp' in prefix[:64]:
                    media_prefix = prefix
                    log.info("MP4 probe obtained %d-byte header from candidate %d/%d",
                             len(prefix), index, len(urls))
                    break
                failures.append(f"candidate {index}: {endpoint} returned non-MP4 bytes "
                                f"(type={response.headers.get('Content-Type')}, bytes={len(prefix)})")
        except Exception as exc:
            failures.append(f"candidate {index}: {endpoint}: {type(exc).__name__}: {exc}\n"
                            f"{traceback.format_exc()}")
            log.debug("MP4 range candidate %d/%d failed at %s: %s", index,
                      len(urls), endpoint, exc, exc_info=True)
    if media_prefix is None:
        log.error("MP4 upload-source probe exhausted %d candidates; trace follows:\n%s",
                  len(urls), "\n".join(failures))
        return None
    if not media_prefix:
        return None
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams',
             '-show_format', '-probesize', str(limit), '-analyzeduration', '0', 'pipe:0'],
            input=media_prefix, capture_output=True, text=False, timeout=5, check=False)
        probe = json.loads(result.stdout.decode('utf-8', errors='replace') or '{}')
        log.debug("ffprobe header result: returncode=%d streams=%d stderr=%s",
                  result.returncode, len(probe.get('streams') or []),
                  result.stderr.decode('utf-8', errors='replace')[:500])
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        log.exception("ffprobe failed while reading bounded TikTok MP4 header: %s", exc)
        return None
    return _classify_upload_source(probe)


def _classify_upload_source(probe: dict) -> str:
    """Rough Phone/Desktop guess from surviving container and codec signals.

    TikTok's transcode overwrites many original encoder identifiers. These
    weighted features therefore describe likelihood, not proof. The signal
    weights are intentionally broad: explicit mobile encoder strings dominate;
    audio profile/rate and retained high-bitrate multi-track output are weaker
    clues. Generic muxer tags such as Lavf are ignored.
    """
    streams = probe.get('streams') or []
    video_streams = [stream for stream in streams if stream.get('codec_type') == 'video']
    audio_streams = [stream for stream in streams if stream.get('codec_type') == 'audio']
    video = max(video_streams, key=lambda s: (_int(s.get('width')) or 0) * (_int(s.get('height')) or 0), default={})
    all_tags = [str(value).lower() for stream in streams
                for value in (stream.get('tags') or {}).values()]
    all_tags.extend(str(value).lower() for value in
                    (probe.get('format', {}).get('tags') or {}).values())
    signature = ' '.join(all_tags)
    phone_score = 0.0
    desktop_score = 0.0

    if any(token in signature for token in ('com.apple.coremedia', 'videotoolbox', 'apple avc',
                                             'android.media.mediacodec', 'omx.qcom', 'omx.exynos')):
        phone_score += 6
    if 'patched by ' in signature:
        # TikTok sometimes preserves a desktop patcher's MP4 comment in an
        # otherwise high-quality upload. This is a weak processing-path clue.
        desktop_score += 2

    if any('he-aac' in str(stream.get('profile', '')).lower() for stream in audio_streams):
        phone_score += 3
    audio_rates = [_int(stream.get('sample_rate')) or 0 for stream in audio_streams]
    if audio_rates and max(audio_rates) >= 88200:
        desktop_score += 2
    elif audio_rates and max(audio_rates) <= 48000:
        phone_score += 1
    if len(audio_streams) > 1:
        desktop_score += 2

    width, height = _int(video.get('width')) or 0, _int(video.get('height')) or 0
    short_edge = min(width, height) if width and height else 0
    video_bitrate = _int(video.get('bit_rate')) or 0
    if short_edge >= 1000:
        desktop_score += 1
    elif short_edge and short_edge <= 576:
        phone_score += 1
    if video_bitrate >= 5_000_000:
        desktop_score += 1

    # With weak or tied signals, use the output profile as a last-resort prior.
    # This allows the analyzer to provide a best guess instead of reverting to
    # a blank field, while accepting that TikTok can transcode either source.
    if phone_score == desktop_score:
        return 'Desktop' if short_edge >= 900 or video_bitrate >= 5_000_000 else 'Phone'
    return 'Phone' if phone_score > desktop_score else 'Desktop'


def _first_value(*values):
    return next((value for value in values if value is not None and value != ''), None)


def _int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def normalize_metadata(data: dict) -> dict:
    formats = [f for f in data.get('formats', []) if f.get('width') and f.get('height')]
    best = max(formats, key=stream_rank, default=None)
    width, height = _original_dimensions(data)
    code = _first_value(data.get('region_code'), data.get('region'))
    browser_quality = _quality_label(data.get('_tiktok_raw') or {})
    mobile_quality = _quality_label(data.get('_tiktok_mobile_raw') or {}) or browser_quality
    return {
        'views': data.get('view_count'), 'likes': data.get('like_count'),
        'comments': data.get('comment_count'), 'favorites': data.get('bookmark_count'),
        'shares': data.get('repost_count'),
        'downloads': "Count not public; download available" if data.get('_tiktok_download_available') else "Count not public",
        'video_id': data.get('id'),
        'upload_source': data.get('upload_source'),
        'vq_score': data.get('vq_score') or _vq_score(
            data.get('_tiktok_raw') or {}, data.get('_tiktok_mobile_raw') or {}),
        'region': country_display(code), 'shadowban': _estimate_shadowban(data),
        'original_width': width, 'original_height': height,
        'browser_quality': browser_quality,
        'mobile_quality': mobile_quality, 'categories': data.get('categories') or [],
        'best_format_id': (best or {}).get('format_id'),
    }


def _original_dimensions(data: dict) -> tuple[int | None, int | None]:
    """Use TikTok's original-reference quality metadata before playback size.

    TikTok's per-rendition MVMAF field has an `ori` set of reference scales.
    Its highest `vNNN` level gives the best public indication of the source's
    short edge; combine it with the item's aspect ratio to recover dimensions.
    """
    raw = data.get('_tiktok_raw') or {}
    video = raw.get('video') or {}
    width = _int(video.get('width'))
    height = _int(video.get('height'))
    ratio = (width / height) if width and height else None
    if ratio:
        original_level = _original_reference_level(video)
        if original_level:
            if width <= height:
                return original_level, round(original_level / ratio)
            return round(original_level * ratio), original_level

    # Fallback to TikTok's higher-quality download URL hint when it exists.
    download_url = video.get('downloadAddr') or video.get('download_addr') or ''
    query = urllib.parse.parse_qs(urllib.parse.urlparse(download_url).query)
    definition = next(iter(query.get('definition', [])), '')
    match = re.fullmatch(r'(\d+)p', str(definition), re.I)
    if ratio and match:
        advertised = int(match.group(1))
        if width <= height:
            return advertised, round(advertised / ratio)
        return round(advertised * ratio), advertised
    formats = [f for f in data.get('formats', []) if f.get('width') and f.get('height')]
    best = max(formats, key=stream_rank, default=None)
    return (width or (best or {}).get('width'), height or (best or {}).get('height'))


def _original_reference_level(video: dict) -> int | None:
    levels = []
    for entry in video.get('bitrateInfo') or video.get('bit_rate') or []:
        value = entry.get('MVMAF') or entry.get('mvmaf')
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                continue
        if not isinstance(value, dict):
            continue
        # Some API variants nest this JSON once more in VideoExtra.
        extra = entry.get('VideoExtra') or entry.get('video_extra')
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except json.JSONDecodeError:
                extra = {}
            nested = extra.get('mvmaf') if isinstance(extra, dict) else None
            if isinstance(nested, str):
                try:
                    nested = json.loads(nested)
                except json.JSONDecodeError:
                    nested = None
            if isinstance(nested, dict):
                value = nested
        for version in value.values():
            if not isinstance(version, dict):
                continue
            original = version.get('ori')
            if isinstance(original, dict):
                levels.extend(int(match.group(1)) for key in original
                              if (match := re.fullmatch(r'v(\d+)', str(key))))
    return max(levels, default=None)


def stream_rank(f: dict) -> tuple:
    return (f.get('tbr') or f.get('vbr') or 0,
            (f.get('width') or 0) * (f.get('height') or 0), f.get('fps') or 0)


def _quality_label(raw: dict):
    video = raw.get('video') or {}
    entries = video.get('bitrateInfo') or video.get('bit_rate') or []
    if not entries:
        return None
    best = max(entries, key=lambda item: item.get('Bitrate') or item.get('bit_rate') or 0)
    addr = best.get('PlayAddr') or best.get('play_addr') or {}
    w = addr.get('Width') or addr.get('width') or video.get('width')
    h = addr.get('Height') or addr.get('height') or video.get('height')
    fps = best.get('BitrateFPS') or best.get('FPS') or best.get('fps')
    bitrate = best.get('Bitrate') or best.get('bit_rate')
    if bitrate is None:
        bitrate = (best.get('tbr') or best.get('vbr') or 0) * 1000
    if not w or not h:
        return None
    resolution = video.get('definition') or video.get('Definition')
    if not resolution:
        resolution = f"{min(int(w), int(h))}p"
    if str(resolution).isdigit():
        resolution = f"{resolution}p"
    quality = str(resolution)
    if fps:
        quality += f" at {fps} FPS"
    if bitrate and _int(bitrate) and _int(bitrate) > 0:
        quality += f", {bitrate / 1_000_000:.1f}Mbps"
    return quality


def _public_restriction_status(data: dict):
    state = data.get('_tiktok_public_status') or {}
    if state.get('prohibited') is True or state.get('taken_down') in (1, True):
        return "Yes (TikTok marks this video prohibited/taken down)"
    if state.get('reviewing') is True or state.get('warnings'):
        return "Under review or warning (not a shadowban determination)"
    if state.get('prohibited') is False and state.get('reviewing') is False and state.get('taken_down') in (0, False):
        return "No public restriction flag (not a shadowban determination)"
    return None


def _estimate_shadowban(data: dict):
    """Estimate reach suppression from public flags and single-post signals.

    This is a heuristic, not TikTok's private recommendation eligibility result.
    A single public video's performance cannot prove a shadowban.
    """
    state = data.get('_tiktok_public_status') or {}
    if state.get('prohibited') is True or state.get('taken_down') in (1, True):
        return "Yes"
    if state.get('reviewing') is True or state.get('warnings'):
        return "Yes"

    views = _int(data.get('view_count'))
    followers = _int(data.get('_tiktok_follower_count'))
    posted = _int(data.get('timestamp'))
    age_days = max(0, (time.time() - posted) / 86400) if posted else None
    if views is None or views < 1:
        return "No"

    # Estimate only when the public page also supplies creator size and enough
    # time has passed for views to accumulate. Low reach alone is not decisive.
    if followers and followers >= 100 and age_days is not None and age_days >= 7:
        reach = views / followers
        interactions = sum(_int(data.get(key)) or 0 for key in
                           ('like_count', 'comment_count', 'repost_count', 'bookmark_count'))
        engagement = interactions / views
        if reach < 0.01 and engagement < 0.01:
            return "Yes"
        if reach < 0.002:
            return "Yes"
    return "No"


@functools.lru_cache(maxsize=1)
def _geonames_countries() -> dict:
    """Load country names from GeoNames' current ISO country table, cached in process."""
    request = urllib.request.Request(
        'https://download.geonames.org/export/dump/countryInfo.txt',
        headers={'User-Agent': 'TikUtils/1.0'})
    with urllib.request.urlopen(request, timeout=8) as response:
        content = response.read().decode('utf-8', errors='replace')
    countries = {}
    for line in content.splitlines():
        if not line or line.startswith('#'):
            continue
        columns = line.split('\t')
        if len(columns) > 4:
            countries[columns[0].upper()] = columns[4]
    return countries


def country_display(code):
    if not code or not isinstance(code, str) or len(code) != 2 or not code.isalpha():
        return code or None
    code = code.upper()
    flag = ''.join(chr(127397 + ord(letter)) for letter in code)
    try:
        name = _geonames_countries().get(code)
    except Exception as exc:
        log.debug("Country lookup unavailable: %s", exc)
        name = None
    return f"{flag} {name}" if name else code


def download_stream(url: str, dest_path: str, progress_cb):
    """Download one signed TikTok stream URL directly."""
    request = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Referer': 'https://www.tiktok.com/',
    })
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(dest_path, 'wb') as out:
            total = int(response.headers.get('Content-Length', 0) or 0)
            done = 0
            while chunk := response.read(65536):
                out.write(chunk)
                done += len(chunk)
                if total:
                    progress_cb(done / total)
        log.info("TikTok stream download complete")
    except Exception as exc:
        log.error("TikTok stream download failed: %s", exc)
        raise RuntimeError(f"TikTok download failed: {exc}") from exc
