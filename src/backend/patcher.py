"""
TikUtils backend: MP4 patcher.

Reverse engineered from Editing News' patcher and made open source.
Rebuilds MP4 containers so TikTok treats uploads as pre-optimized.
"""

import json
import logging
import os
import struct
import subprocess
import urllib.request
from pathlib import Path

log = logging.getLogger("TikUtils.patcher")

GIST_RAW_URL = (
    "https://gist.githubusercontent.com/buwryme/"
    "e7dd8bca2094995943a855f0af985960/raw/tiktok_patcher.toml"
)

BUILTIN_TOML = r'''# Reverse engineered from Editing News' patcher and made open source.
[patcher]
watermark = "Patched by https://github.com/buwryme/tiktok-lossless-upload"
sample_multiplier_numerator = 20
sample_multiplier_denominator = 3
strip_udta = true
faststart = true
free_box = true
default_dummy_size = 8
default_dummy_payload = "0000000400000000"

[patcher.dummy_sizes]
avc1 = 8
avc2 = 8
avc3 = 8
avc4 = 8
hvc1 = 8
hev1 = 8
dvh1 = 8
dvhe = 8
dvav = 8
dva1 = 8
vp08 = 8
vp09 = 8
av01 = 8
mp4v = 8
s263 = 8
h263 = 8
jpeg = 8
mjpa = 8
mjpb = 8
ap4h = 8
ap4x = 8

[patcher.dummy_payloads]
avc1 = "0000000400000000"
avc2 = "0000000400000000"
avc3 = "0000000400000000"
avc4 = "0000000400000000"
hvc1 = "0000000400000000"
hev1 = "0000000400000000"
dvh1 = "0000000400000000"
dvhe = "0000000400000000"
dvav = "0000000400000000"
dva1 = "0000000400000000"
vp08 = "0000000000000000"
vp09 = "0000000000000000"
av01 = "0000000000000000"
mp4v = "0000000400000000"
s263 = "0000000400000000"
h263 = "0000000400000000"
jpeg = "0000000400000000"
mjpa = "0000000400000000"
mjpb = "0000000400000000"
ap4h = "0000000400000000"
ap4x = "0000000400000000"
'''


ENCODE_DEFAULTS = {
    "re_encode": True,
    "crf": 18,
    "codec": "h264",
}
DEFAULTS = ENCODE_DEFAULTS

class ConfigManager:
    """Persist the small set of user-facing encode settings."""

    def __init__(self):
        self.config_dir = Path.home() / ".local" / "share" / "net.buwryy.TikUtils"
        self.config_path = self.config_dir / "settings.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.default_config = dict(ENCODE_DEFAULTS)
        self.config = dict(self.default_config)
        self.load()

    def load(self):
        if not self.config_path.exists():
            return
        try:
            with open(self.config_path, "r") as handle:
                saved = json.load(handle)
            for key, value in saved.items():
                if key in self.default_config:
                    self.config[key] = value
            log.debug("loaded config from %s", self.config_path)
        except Exception as exc:
            log.warning("failed to load config: %s", exc)

    def save(self, new_config):
        filtered = {
            key: value for key, value in new_config.items()
            if key in self.default_config
        }
        with open(self.config_path, "w") as handle:
            json.dump(filtered, handle, indent=2)
        self.config = filtered
        log.info("configuration saved to disk")

    def get_defaults(self):
        return dict(self.default_config)


_PATCHER_CONFIG = None


def _parse_toml(text):
    """Small TOML subset parser used when tomllib is unavailable."""
    try:
        import tomllib
        return tomllib.loads(text)
    except Exception:
        pass
    data = {}
    section = data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = data
            for part in line[1:-1].strip().split("."):
                part = part.strip()
                if part:
                    cur = cur.setdefault(part, {})
            section = cur
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except Exception:
                value = value.strip('"')
        elif value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        else:
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass
        section[key] = value
    return data


def _builtin_patcher_config():
    return _parse_toml(BUILTIN_TOML)["patcher"]


def load_patcher_config(reload=False):
    """Load hidden patcher settings from the public gist, falling back to builtin."""
    global _PATCHER_CONFIG
    if _PATCHER_CONFIG is not None and not reload:
        return _PATCHER_CONFIG
    cfg = _builtin_patcher_config()
    try:
        with urllib.request.urlopen(GIST_RAW_URL, timeout=4) as response:
            remote = _parse_toml(response.read().decode("utf-8"))["patcher"]
        for key, value in remote.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    except Exception:
        pass
    _PATCHER_CONFIG = cfg
    return cfg


def get_patcher_config():
    return load_patcher_config()


def get_default_config():
    return dict(ENCODE_DEFAULTS)


def _u32(data, offset):
    return struct.unpack(">I", data[offset:offset + 4])[0]


def _u64(data, offset):
    return struct.unpack(">Q", data[offset:offset + 8])[0]


def _p32(value):
    return struct.pack(">I", value & 0xFFFFFFFF)


def _p64(value):
    return struct.pack(">Q", value & 0xFFFFFFFFFFFFFFFF)


def _box(box_type, payload):
    return _p32(8 + len(payload)) + box_type + payload


def _fullbox(box_type, version, flags, payload):
    return _box(box_type, _p32((version << 24) | (flags & 0x00FFFFFF)) + payload)


def _boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size = _u32(data, pos)
        box_type = data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                break
            size = _u64(data, pos + 8)
            header = 16
        elif size == 0:
            size = end - pos
        if size < 8 or pos + size > end:
            break
        yield {
            "offset": pos,
            "size": size,
            "type": box_type,
            "header": header,
            "end": pos + size,
            "payload": data[pos + header:pos + size],
        }
        pos += size


def _find(boxes, box_type):
    return next((box for box in boxes if box["type"] == box_type), None)


def _handler_type(trak):
    for box in _boxes(trak["payload"]):
        if box["type"] != b"mdia":
            continue
        for child in _boxes(box["payload"]):
            if child["type"] == b"hdlr":
                return child["payload"][8:12]
    return b""


def _detect_video_codec(moov):
    for box in _boxes(moov["payload"]):
        if box["type"] != b"trak" or _handler_type(box) != b"vide":
            continue
        for mdia in _boxes(box["payload"]):
            if mdia["type"] != b"mdia":
                continue
            for minf in _boxes(mdia["payload"]):
                if minf["type"] != b"minf":
                    continue
                for stbl in _boxes(minf["payload"]):
                    if stbl["type"] != b"stbl":
                        continue
                    for stsd in _boxes(stbl["payload"]):
                        if stsd["type"] == b"stsd" and len(stsd["payload"]) >= 16:
                            return stsd["payload"][12:16]
    return b"avc1"


def _mdhd_duration(mdhd):
    payload = mdhd["payload"]
    if payload[0] == 1:
        return _u64(payload, 24)
    return _u32(payload, 16)


def _mdhd_timescale(mdhd):
    payload = mdhd["payload"]
    if payload[0] == 1:
        return _u32(payload, 20)
    return _u32(payload, 12)


def _patch_mdhd(mdhd, duration):
    payload = bytearray(mdhd["payload"])
    if payload[0] == 1:
        payload[24:32] = _p64(duration)
    else:
        if duration > 0xFFFFFFFF:
            raise ValueError("video duration is too large for a version 0 mdhd")
        payload[16:20] = _p32(duration)
    return _box(b"mdhd", bytes(payload))


def _stts_entries(stts):
    payload = stts["payload"]
    count = _u32(payload, 4)
    return [
        (_u32(payload, 8 + 8 * i), _u32(payload, 12 + 8 * i))
        for i in range(count)
    ]


def _stsc_entries(stsc):
    payload = stsc["payload"]
    count = _u32(payload, 4)
    return [
        struct.unpack(">III", payload[8 + 12 * i:20 + 12 * i])
        for i in range(count)
    ]


def _stsz_info(stsz):
    payload = stsz["payload"]
    uniform = _u32(payload, 4)
    count = _u32(payload, 8)
    if uniform:
        sizes = [uniform] * count
    else:
        sizes = [_u32(payload, 12 + 4 * i) for i in range(count)]
    return uniform, sizes


def _stco_entries(stco):
    payload = stco["payload"]
    count = _u32(payload, 4)
    if stco["type"] == b"stco":
        return [_u32(payload, 8 + 4 * i) for i in range(count)]
    return [_u64(payload, 8 + 8 * i) for i in range(count)]


def _build_stts(real_count, delta):
    payload = _p32(0) + _p32(2)
    payload += struct.pack(">II", max(0, real_count - 1), delta)
    payload += struct.pack(">II", 1, 1)
    return _box(b"stts", payload)


def _build_stsc(entries, first_chunk):
    payload = _p32(0) + _p32(len(entries) + 1)
    for first, per_chunk, description in entries:
        payload += struct.pack(">III", first, per_chunk, description)
    payload += struct.pack(">III", first_chunk, 1, 1)
    return _box(b"stsc", payload)


def _build_stsz(sizes):
    payload = _p32(0) + _p32(0) + _p32(len(sizes))
    for size in sizes:
        payload += _p32(size)
    return _box(b"stsz", payload)


def _build_stco(values, is_64=False):
    payload = _p32(0) + _p32(len(values))
    for value in values:
        payload += _p64(value) if is_64 else _p32(value)
    return _box(b"co64" if is_64 else b"stco", payload)


def _shift_stco(stco, delta):
    values = [value + delta for value in _stco_entries(stco)]
    return _build_stco(values, stco["type"] == b"co64")


def _build_watermark(text):
    data = _box(b"data", _p32(1) + _p32(0) + text.encode("utf-8") + b"\x00")
    ilst = _box(b"ilst", _box(b"\xa9too", data))
    hdlr = _fullbox(
        b"hdlr", 0, 0, _p32(0) + b"mdir" + b"\x00" * 12 + b"\x00"
    )
    meta = _fullbox(b"meta", 0, 0, hdlr + ilst)
    return _box(b"udta", meta)


def _build_ftyp(codec):
    brand = {
        b"avc1": b"avc1",
        b"avc2": b"avc1",
        b"avc3": b"avc1",
        b"avc4": b"avc1",
        b"hvc1": b"hvc1",
        b"hev1": b"hvc1",
        b"dvh1": b"hvc1",
        b"dvhe": b"hvc1",
        b"dvav": b"avc1",
        b"dva1": b"avc1",
        b"vp08": b"vp08",
        b"vp09": b"vp09",
        b"av01": b"av01",
        b"mp4v": b"mp4v",
    }.get(codec, b"avc1")
    return _box(
        b"ftyp", b"isom" + _p32(0x200) + b"isom" + b"iso2" + brand + b"mp41"
    )


def _build_mdat(payload):
    size = 8 + len(payload)
    if size <= 0xFFFFFFFF:
        return _p32(size) + b"mdat" + payload
    return _p32(1) + b"mdat" + _p64(16 + len(payload)) + payload


def _patch_btrt_in_stsd(stsd, bitrate):
    data = bytearray(stsd)
    pos = 0
    while True:
        pos = data.find(b"btrt", pos)
        if pos < 0:
            break
        start = pos - 4
        if start >= 0 and _u32(data, start) == 20:
            data[start + 12:start + 16] = _p32(bitrate)
            data[start + 16:start + 20] = _p32(bitrate)
        pos += 4
    return bytes(data)


def _rebuild_stbl(stbl, delta, safe_offset, video, stats):
    output = []
    for box in _boxes(stbl["payload"]):
        if box["type"] in (b"stco", b"co64"):
            if video:
                values = [value + delta for value in _stco_entries(box)]
                values += [safe_offset] * stats["fake_count"]
                output.append(_build_stco(values, box["type"] == b"co64"))
            else:
                output.append(_shift_stco(box, delta))
        elif video and box["type"] == b"stts":
            output.append(_build_stts(stats["real_count"], stats["sample_delta"]))
        elif video and box["type"] == b"stsc":
            output.append(_build_stsc(stats["stsc"], stats["old_chunks"] + 1))
        elif video and box["type"] == b"stsz":
            output.append(_build_stsz(stats["sizes"]))
        elif video and box["type"] == b"stsd":
            output.append(_box(
                b"stsd", _patch_btrt_in_stsd(box["payload"], stats["bitrate"])
            ))
        else:
            output.append(stbl["payload"][box["offset"]:box["end"]])
    return _box(b"stbl", b"".join(output))


def _rebuild_minf(minf, delta, safe_offset, video, stats):
    output = []
    for box in _boxes(minf["payload"]):
        if box["type"] == b"stbl":
            output.append(_rebuild_stbl(box, delta, safe_offset, video, stats))
        else:
            output.append(minf["payload"][box["offset"]:box["end"]])
    return _box(b"minf", b"".join(output))


def _rebuild_mdia(mdia, delta, safe_offset, video, stats):
    output = []
    for box in _boxes(mdia["payload"]):
        if box["type"] == b"mdhd" and video:
            output.append(_patch_mdhd(box, stats["total_duration"]))
        elif box["type"] == b"minf":
            output.append(_rebuild_minf(box, delta, safe_offset, video, stats))
        else:
            output.append(mdia["payload"][box["offset"]:box["end"]])
    return _box(b"mdia", b"".join(output))


def _rebuild_trak(trak, delta, safe_offset, video, stats):
    output = []
    for box in _boxes(trak["payload"]):
        if box["type"] == b"udta" and stats["strip_udta"]:
            continue
        if box["type"] == b"mdia":
            output.append(_rebuild_mdia(box, delta, safe_offset, video, stats))
        else:
            output.append(trak["payload"][box["offset"]:box["end"]])
    if video:
        output.append(_build_watermark(stats["watermark"]))
    return _box(b"trak", b"".join(output))


def _rebuild_moov(moov, delta, safe_offset, stats):
    output = []
    for box in _boxes(moov["payload"]):
        if box["type"] == b"udta" and stats["strip_udta"]:
            continue
        if box["type"] == b"trak":
            output.append(_rebuild_trak(
                box, delta, safe_offset, _handler_type(box) == b"vide", stats
            ))
        else:
            output.append(moov["payload"][box["offset"]:box["end"]])
    return _box(b"moov", b"".join(output))


def patch_bytes(data, config=None):
    """Patch an MP4 byte string and return the rebuilt MP4 bytes."""
    cfg = config or load_patcher_config()
    top = list(_boxes(data))
    moov = _find(top, b"moov")
    mdat = _find(top, b"mdat")
    if moov is None or mdat is None:
        raise ValueError("input is missing a moov or mdat box")

    codec = _detect_video_codec(moov)
    codec_key = codec.decode("latin-1", "replace")
    dummy_sizes = cfg.get("dummy_sizes", {})
    dummy_payloads = cfg.get("dummy_payloads", {})
    dummy_size = int(dummy_sizes.get(
        codec_key, cfg.get("default_dummy_size", 8)
    ))
    payload_hex = dummy_payloads.get(
        codec_key, cfg.get("default_dummy_payload", "0000000400000000")
    )
    try:
        dummy_payload = bytes.fromhex(payload_hex)
    except ValueError:
        dummy_payload = b"\x00" * dummy_size
    if len(dummy_payload) < dummy_size:
        dummy_payload += b"\x00" * (dummy_size - len(dummy_payload))
    dummy_payload = dummy_payload[:dummy_size]

    real_count = 0
    sample_delta = 0
    total_duration = 0
    timescale = 0
    stsc = []
    sizes = []
    old_chunks = 0
    found = False

    for trak in _boxes(moov["payload"]):
        if trak["type"] != b"trak" or _handler_type(trak) != b"vide":
            continue
        for mdia in _boxes(trak["payload"]):
            if mdia["type"] != b"mdia":
                continue
            for child in _boxes(mdia["payload"]):
                if child["type"] == b"mdhd":
                    timescale = _mdhd_timescale(child)
                    total_duration = _mdhd_duration(child)
                if child["type"] != b"minf":
                    continue
                for stbl in _boxes(child["payload"]):
                    if stbl["type"] != b"stbl":
                        continue
                    for box in _boxes(stbl["payload"]):
                        if box["type"] == b"stts":
                            entries = _stts_entries(box)
                            real_count = sum(count for count, _ in entries)
                            sample_delta = entries[0][1] if entries else 0
                            total_duration = sum(
                                count * delta for count, delta in entries
                            )
                        elif box["type"] == b"stsc":
                            stsc = _stsc_entries(box)
                        elif box["type"] == b"stsz":
                            _, sizes = _stsz_info(box)
                        elif box["type"] in (b"stco", b"co64"):
                            old_chunks = len(_stco_entries(box))
                    found = True

    if not found or not sizes or not old_chunks:
        raise ValueError("input has no usable video sample tables")

    numerator = int(cfg.get("sample_multiplier_numerator", 20))
    denominator = int(cfg.get("sample_multiplier_denominator", 3))
    new_count = int(real_count * numerator / denominator + 0.5)
    new_count = max(real_count, new_count)
    fake_count = new_count - real_count

    seconds = total_duration / timescale if timescale else 0
    bitrate = int(sum(sizes) * 8 / seconds) if seconds else 0

    stats = {
        "real_count": real_count,
        "sample_delta": sample_delta,
        "total_duration": total_duration,
        "stsc": stsc,
        "old_chunks": old_chunks,
        "sizes": list(sizes) + [dummy_size] * fake_count,
        "fake_count": fake_count,
        "bitrate": bitrate,
        "watermark": cfg.get(
            "watermark",
            "Patched by https://github.com/buwryme/tiktok-lossless-upload",
        ),
        "strip_udta": bool(cfg.get("strip_udta", True)),
    }

    free_box = _box(b"free", b"") if cfg.get("free_box", True) else b""
    ftyp = _build_ftyp(codec)

    probe_moov = _rebuild_moov(moov, 0, 0, stats)
    new_mdat_payload_offset = (
        len(ftyp) + len(free_box) + len(probe_moov) + 8
    )
    old_mdat_payload_offset = mdat["offset"] + mdat["header"]
    delta = new_mdat_payload_offset - old_mdat_payload_offset
    final_size = (
        new_mdat_payload_offset
        + (mdat["size"] - mdat["header"])
        + len(dummy_payload)
    )
    safe_offset = final_size - len(dummy_payload)

    new_moov = _rebuild_moov(moov, delta, safe_offset, stats)
    if len(new_moov) != len(probe_moov):
        raise ValueError("moov size changed between patch passes")

    mdat_payload = data[mdat["offset"] + mdat["header"]:mdat["end"]]
    new_mdat = _build_mdat(mdat_payload + dummy_payload)
    return ftyp + free_box + new_moov + new_mdat


def patch_mp4(input_path, config=None):
    """Patch an MP4 file in place."""
    path = Path(input_path)
    data = path.read_bytes()
    result = patch_bytes(data, config or load_patcher_config())
    path.write_bytes(result)
    return True


def encode_for_tiktok(input_path, output_path, crf=18, codec="h264"):
    if codec == "h264":
        video_args = [
            "-c:v", "libx264", "-preset", "medium",
            "-crf", str(crf), "-level", "4.2",
            "-pix_fmt", "yuv420p",
            "-g", "9999", "-bf", "0",
            "-force_key_frames", "0,9.1,18.2",
        ]
    else:
        video_args = [
            "-c:v", "libx265", "-preset", "medium",
            "-crf", str(crf), "-pix_fmt", "yuv420p10le",
            "-x265-params", "log-level=error",
            "-g", "9999", "-bf", "0",
        ]

    cmd = [
        "ffmpeg", "-i", input_path,
        *video_args,
        "-c:a", "aac", "-b:a", "256k",
        "-movflags", "+faststart",
        "-metadata:s:v", "handler_name=VideoHandler",
        "-metadata:s:a", "handler_name=SoundHandler",
        "-map_metadata:s:v", "0:s:v",
        "-map_metadata:s:a", "0:s:a",
        "-y", output_path,
    ]
    log.info("encoding with %s crf%s", codec, crf)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error = result.stderr[-800:] if len(result.stderr) > 800 else result.stderr
        log.error("ffmpeg encode error:\n%s", error)
        return False
    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        log.info("encoded successfully (%.1f MB)", size_mb)
        return True
    log.error("output file was not created")
    return False


def remux_for_tiktok(input_path, output_path):
    cmd = [
        "ffmpeg", "-i", input_path,
        "-c:v", "copy", "-c:a", "copy",
        "-movflags", "+faststart",
        "-metadata:s:v", "handler_name=VideoHandler",
        "-metadata:s:a", "handler_name=SoundHandler",
        "-map_metadata:s:v", "0:s:v",
        "-map_metadata:s:a", "0:s:a",
        "-avoid_negative_ts", "disabled",
        "-copyinkf",
        "-y", output_path,
    ]
    log.info("remuxing without re-encoding")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error = result.stderr[-800:] if len(result.stderr) > 800 else result.stderr
        log.error("ffmpeg remux error:\n%s", error)
        return False
    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        log.info("remuxed successfully (%.1f MB)", size_mb)
        return True
    log.error("output file was not created")
    return False


def _make_output(input_path, output_path, config):
    if config.get("re_encode", True):
        return encode_for_tiktok(
            input_path, output_path, config.get("crf", 18),
            config.get("codec", "h264"),
        )
    return remux_for_tiktok(input_path, output_path)


def patch_video(input_path, config=None):
    """Encode/remux a video and patch the resulting MP4."""
    cfg = dict(ENCODE_DEFAULTS)
    if config:
        for key in ENCODE_DEFAULTS:
            if key in config:
                cfg[key] = config[key]

    source = Path(input_path)
    output_path = source.parent / f"{source.stem}_tiktok.mp4"
    log.info("writing patched output to %s", output_path)
    if not _make_output(str(source), str(output_path), cfg):
        raise RuntimeError("encoding failed during execution")
    if not patch_mp4(output_path, load_patcher_config()):
        raise RuntimeError("patching MP4 structure failed")
    return True
