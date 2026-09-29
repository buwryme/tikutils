"""
TikUtils Backend: Patcher
Handles MP4 parsing, dual-track audio inflation, and TikTok lossless passthrough patching.
"""
import sys
import struct
import subprocess
import os
import json
import logging
from pathlib import Path

log = logging.getLogger("TikUtils.patcher")

DEFAULTS = {
    "encoder": "Lavf59.27.100",
    "comment": "Patched by Buwryme",
    "comment_short": "Patched by Buwryy",
    "name_box_payload": "buwryy<3",
    "inflation_rate": 10,
    "dummy_sample_size": 8,
    "trailing_bytes": 184100,
    "re_encode": True,
    "crf": 18,
    "codec": "h264",
}


class ConfigManager:
    def __init__(self):
        self.config_dir = Path.home() / ".local" / "share" / "net.buwryy.TikUtils"
        self.config_path = self.config_dir / "settings.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.default_config = dict(DEFAULTS)
        self.config = self.default_config.copy()
        self.load()

    def load(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    saved = json.load(f)
                    for k, v in saved.items():
                        if k in self.default_config:
                            self.config[k] = v
                log.debug(f"loaded config from {self.config_path}")
            except Exception as e:
                log.warning(f"failed to load config: {e}")

    def save(self, new_config):
        filtered = {k: v for k, v in new_config.items() if k in self.default_config}
        with open(self.config_path, 'w') as f:
            json.dump(filtered, f, indent=2)
        self.config = filtered
        log.info("configuration saved to disk.")

    def get_defaults(self):
        return dict(self.default_config)


def read_u32be(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack('>I', data[offset:offset+4])[0]


def write_u32be(data: bytearray, offset: int, value: int):
    struct.pack_into('>I', data, offset, value & 0xFFFFFFFF)


def read_u64be(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack('>Q', data[offset:offset+8])[0]


def write_u64be(data: bytearray, offset: int, value: int):
    struct.pack_into('>Q', data, offset, value & 0xFFFFFFFFFFFFFFFF)


def parse_boxes(data: bytearray, start: int, end: int) -> list[dict]:
    boxes = []
    pos = start
    while pos + 8 <= end:
        raw_size = read_u32be(data, pos)
        size = raw_size
        if raw_size == 1:
            if pos + 16 > end:
                break
            hi = read_u32be(data, pos + 8)
            lo = read_u32be(data, pos + 12)
            size = (hi << 32) + lo
        elif raw_size == 0:
            size = end - pos
        if size < 8 or pos + size > end:
            break
        atype = data[pos+4:pos+8]
        boxes.append({"offset": pos, "size": size, "type": atype, "end": pos + size})
        pos += size
    return boxes


def find_box(data: bytearray, fourcc: bytes, start: int = 0, end: int = None) -> dict | None:
    if end is None:
        end = len(data)
    for box in parse_boxes(data, start, end):
        if box["type"] == fourcc:
            return box
    return None


def build_box(box_type: bytes, payload: bytes) -> bytes:
    size = 8 + len(payload)
    return struct.pack('>I', size) + box_type + payload


def build_fullbox(box_type: bytes, version: int, flags: int, payload: bytes) -> bytes:
    ver_flags = struct.pack('>I', (version << 24) | (flags & 0x00FFFFFF))
    return build_box(box_type, ver_flags + payload)


def build_hdlr(handler_type: bytes, name: str) -> bytes:
    payload = b'\x00' * 4 + handler_type + b'\x00' * 12
    payload += name.encode('utf-8') + b'\x00'
    return build_fullbox(b'hdlr', 0, 0, payload)


def build_data_atom(value: str) -> bytes:
    payload = struct.pack('>II', 1, 0) + value.encode('utf-8')
    return build_box(b'data', payload)


def build_ilst_entry(tag: bytes, value: str) -> bytes:
    return build_box(tag, build_data_atom(value))


def build_combined_udta(cfg: dict) -> bytes:
    # --- meta1 (flags=375): hdlr(appl) + ilst ---
    ilst1 = b''
    tag_map = [
        ('\xa9nam', cfg.get("title", "")),
        ('\xa9ART', cfg.get("artist", "")),
        ('\xa9wrt', cfg.get("composer", "")),
        ('\xa9alb', cfg.get("album", "")),
        ('\xa9day', cfg.get("date", "")),
        ('\xa9too', cfg.get("encoder", "")),
        ('\xa9cmt', cfg.get("comment", "")),
        ('\xa9gen', cfg.get("genre", "")),
        ('cprt', cfg.get("copyright", "")),
        ('\xa9grp', cfg.get("grouping", "")),
    ]
    for tag, val in tag_map:
        if val:
            ilst1 += build_ilst_entry(tag.encode('latin-1'), val)

    hdlr1_payload = b'\x00' * 4 + b'mdir' + b'\x00' * 12
    hdlr1_payload += b'appl\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
    hdlr1_box = build_fullbox(b'hdlr', 0, 0, hdlr1_payload)

    meta1_inner = hdlr1_box + build_box(b'ilst', ilst1)
    meta1_payload = b'\x00\x00\x00\x00' + meta1_inner
    meta1_box = build_fullbox(b'meta', 0, 375, meta1_payload)

    # --- meta2 (flags=0): hdlr(zeros) + name + ilst(short comment) ---
    hdlr2_payload = b'\x00' * 4 + b'mdir' + b'\x00' * 12
    hdlr2_payload += b'\x00' * 12 + b'\x00'
    hdlr2_box = build_fullbox(b'hdlr', 0, 0, hdlr2_payload)

    name_payload = cfg.get("name_box_payload", "").encode('utf-8')
    name_box = build_box(b'name', name_payload) if name_payload else b''

    ilst2 = b''
    short_comment = cfg.get("comment_short", "")
    if short_comment:
        ilst2 += build_ilst_entry('\xa9cmt'.encode('latin-1'), short_comment)
    ilst2_box = build_box(b'ilst', ilst2)

    meta2_inner = hdlr2_box + name_box + ilst2_box
    meta2_payload = b'\x00\x00\x00\x00' + meta2_inner
    meta2_box = build_fullbox(b'meta', 0, 0, meta2_payload)

    udta_payload = meta1_box + meta2_box
    return build_box(b'udta', udta_payload)


def build_trailing_garbage(size: int) -> bytes:
    void_box = b'\x00\x00\x00\x04VOID'
    pattern = b'\x00\x00\x00\x04'
    remaining = size - len(void_box)
    repeats = max(0, remaining // len(pattern))
    return void_box + (pattern * repeats)


def zero_mp4a_samplerate(data: bytearray):
    """zero out SampleRate in all mp4a sample entries"""
    top_boxes = parse_boxes(data, 0, len(data))
    moov = next((b for b in top_boxes if b["type"] == b'moov'), None)
    if not moov:
        return
    _zero_mp4a_recursive(data, moov["offset"] + 8, moov["end"])


def _zero_mp4a_recursive(data: bytearray, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        sz = read_u32be(data, pos)
        if sz < 8 or pos + sz > end:
            break
        typ = data[pos+4:pos+8]
        if typ == b'mp4a' and sz >= 36:
            old = read_u32be(data, pos + 28)
            write_u32be(data, pos + 28, 0)
            log.info(f"ZEROED mp4a samplerate at offset {pos}: {old} → 0")
        elif typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd'):
            _zero_mp4a_recursive(data, pos + 8, pos + sz)
        pos += sz


def spoof_audio_bitrate(data: bytearray, source_path: str):
    """copy esds avgBitrate/maxBitrate and mp4a-btrt from source to all target mp4a entries"""
    try:
        with open(source_path, 'rb') as f:
            src = bytearray(f.read())
    except IOError as e:
        log.warning(f"could not read source for bitrate spoof: {e}")
        return

    src_moov = find_box(src, b'moov')
    if not src_moov:
        return

    src_bitrates = []
    _extract_mp4a_bitrates(src, src_moov["offset"] + 8, src_moov["end"], src_bitrates)
    if not src_bitrates:
        log.warning("no mp4a bitrate info found in source")
        return
    src_avg, src_max = src_bitrates[0]
    log.debug(f"source audio bitrate: avg={src_avg}, max={src_max}")

    tgt_moov = find_box(data, b'moov')
    if not tgt_moov:
        return
    count = _apply_mp4a_bitrates(data, tgt_moov["offset"] + 8, tgt_moov["end"], src_avg, src_max)
    log.info(f"spoofed audio bitrate on {count} mp4a entries (avg={src_avg}, max={src_max})")


def _extract_mp4a_bitrates(data: bytearray, start: int, end: int, results: list):
    pos = start
    while pos + 8 <= end:
        sz = read_u32be(data, pos)
        if sz < 8 or pos + sz > end:
            break
        typ = data[pos+4:pos+8]
        if typ == b'mp4a' and sz >= 36:
            cpos = pos + 36
            while cpos + 8 <= pos + sz:
                csz = read_u32be(data, cpos)
                if csz < 8 or cpos + csz > pos + sz:
                    break
                ctyp = data[cpos+4:cpos+8]
                if ctyp == b'esds' and csz >= 30:
                    max_br = read_u32be(data, cpos + 22)
                    avg_br = read_u32be(data, cpos + 26)
                    results.append((avg_br, max_br))
                cpos += csz
        elif typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd'):
            _extract_mp4a_bitrates(data, pos + 8, pos + sz, results)
        pos += sz


def _apply_mp4a_bitrates(data: bytearray, start: int, end: int, avg: int, mx: int) -> int:
    count = 0
    pos = start
    while pos + 8 <= end:
        sz = read_u32be(data, pos)
        if sz < 8 or pos + sz > end:
            break
        typ = data[pos+4:pos+8]
        if typ == b'mp4a' and sz >= 36:
            cpos = pos + 36
            while cpos + 8 <= pos + sz:
                csz = read_u32be(data, cpos)
                if csz < 8 or cpos + csz > pos + sz:
                    break
                ctyp = data[cpos+4:cpos+8]
                if ctyp == b'esds' and csz >= 30:
                    write_u32be(data, cpos + 22, mx)
                    write_u32be(data, cpos + 26, avg)
                    count += 1
                elif ctyp == b'btrt' and csz >= 20:
                    write_u32be(data, cpos + 12, avg)
                    write_u32be(data, cpos + 16, mx)
                cpos += csz
        elif typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd'):
            count += _apply_mp4a_bitrates(data, pos + 8, pos + sz, avg, mx)
        pos += sz
    return count


def copy_avcc_from_source(source_path: str, target_data: bytearray) -> bytearray:
    """copy raw avcC box from source to target moov/stsd/avc1"""
    try:
        with open(source_path, 'rb') as f:
            src = bytearray(f.read())
    except IOError as e:
        log.warning(f"could not read source for avcC copy: {e}")
        return target_data

    src_moov = find_box(src, b'moov')
    tgt_moov = find_box(target_data, b'moov')
    if not src_moov or not tgt_moov:
        return target_data

    src_avcc = find_box(src, b'avcC', src_moov["offset"], src_moov["end"])
    tgt_avcc = find_box(target_data, b'avcC', tgt_moov["offset"], tgt_moov["end"])
    if not src_avcc or not tgt_avcc:
        return target_data

    if src_avcc["size"] == tgt_avcc["size"]:
        target_data[tgt_avcc["offset"]:tgt_avcc["end"]] = src[src_avcc["offset"]:src_avcc["end"]]
        log.debug("copied avcC box from source (same size)")
    else:
        log.warning(f"avcC size mismatch: src={src_avcc['size']} tgt={tgt_avcc['size']}, skipping binary copy")

    return target_data


def fix_btrt_from_source(source_path: str, target_data: bytearray):
    """copy btrt avgBitRate from source video trak to target"""
    try:
        with open(source_path, 'rb') as f:
            src = bytearray(f.read())
    except IOError as e:
        log.warning(f"could not read source for btrt copy: {e}")
        return

    src_moov = find_box(src, b'moov')
    tgt_moov = find_box(target_data, b'moov')
    if not src_moov or not tgt_moov:
        return

    src_btrt = find_box(src, b'btrt', src_moov["offset"], src_moov["end"])
    tgt_btrt = find_box(target_data, b'btrt', tgt_moov["offset"], tgt_moov["end"])

    if src_btrt and tgt_btrt and src_btrt["size"] == tgt_btrt["size"]:
        avg = read_u32be(src, src_btrt["offset"] + 12)
        write_u32be(target_data, tgt_btrt["offset"] + 12, avg)
        log.debug(f"restored btrt avgBitRate: {avg}")


def strip_free_boxes(data: bytearray) -> bytearray:
    """remove all free/skip boxes from top level"""
    top_boxes = parse_boxes(data, 0, len(data))
    keep_ranges = []
    for box in top_boxes:
        if box["type"] not in (b'free', b'skip'):
            keep_ranges.append((box["offset"], box["end"]))
    if len(keep_ranges) == len(top_boxes):
        return data
    result = bytearray()
    for start, end in keep_ranges:
        result.extend(data[start:end])
    return result


def upgrade_mvhd_to_v1(mvhd: bytearray) -> bytearray:
    """convert mvhd to version 1 with unknown duration and nexttrackid=5"""
    ver = mvhd[8]
    timescale = read_u32be(mvhd, 20) if ver == 0 else read_u32be(mvhd, 28)
    ntid_off = 96 if ver == 0 else 108
    ntid = read_u32be(mvhd, ntid_off)

    # v1 mvhd body is 112 bytes INCLUDING the 4-byte version/flags prefix
    body = bytearray(112)
    body[0] = 1  # version=1, flags=0x000000
    # creation_time @ 4 = 0 (already zero)
    # modification_time @ 12 = 0
    struct.pack_into('>I', body, 20, timescale)
    struct.pack_into('>Q', body, 24, 0xFFFFFFFFFFFFFFFF)
    struct.pack_into('>I', body, 32, 0x00010000)
    struct.pack_into('>H', body, 36, 0x0100)
    struct.pack_into('>I', body, 44, 0x00010000)
    struct.pack_into('>I', body, 60, 0x00010000)
    struct.pack_into('>I', body, 76, 0x40000000)
    struct.pack_into('>I', body, 108, 5)

    # use build_box, NOT build_fullbox — version byte is already in body
    return bytearray(build_box(b'mvhd', bytes(body)))


def patch_elst_plus_one(edts_data: bytearray) -> bytearray:
    """increment first elst segment_duration by 1 tick"""
    children = parse_boxes(edts_data, 8, len(edts_data))
    new_children = []
    for c in children:
        if c["type"] == b'elst':
            elst = bytearray(edts_data[c["offset"]:c["end"]])
            ver = elst[8]
            count = read_u32be(elst, 12)
            if count > 0:
                dur_off = 16 if ver == 0 else 20
                dur = read_u32be(elst, dur_off)
                write_u32be(elst, dur_off, dur + 1)
                log.debug(f"elst segment_duration: {dur} → {dur+1}")
            new_children.append(bytes(elst))
        else:
            new_children.append(bytes(edts_data[c["offset"]:c["end"]]))
    return bytearray(build_box(b'edts', b''.join(new_children)))


def patch_video(input_path: str, config: dict = None) -> bool:
    cfg = dict(DEFAULTS)
    if config:
        cfg.update(config)

    p = Path(input_path)
    output_path = str(p.parent / f"{p.stem}_tiktok.mp4")

    log.info(f"starting patch pipeline for: {input_path}")

    if cfg.get("re_encode", True):
        log.info("step 1/2: re-encoding video...")
        crf = cfg.get("crf", 18)
        codec = cfg.get("codec", "h264")
        if not encode_for_tiktok(input_path, output_path, crf, codec):
            raise RuntimeError("Encoding failed during execution")
    else:
        log.info("step 1/2: remuxing (passthrough, no re-encode)...")
        if not remux_for_tiktok(input_path, output_path):
            raise RuntimeError("Remux failed during execution")

    log.info("step 2/2: patching mp4 structure...")
    if not patch_mp4(output_path, cfg, input_path):
        raise RuntimeError("Patching MP4 structure failed")

    log.info("pipeline complete successfully.")
    return True


def patch_mp4(input_path: str, cfg: dict, source_path: str = None) -> bool:
    try:
        with open(input_path, 'rb') as f:
            raw = f.read()
    except IOError as e:
        log.error(f"couldn't read {input_path}: {e}")
        return False

    data = bytearray(raw)
    orig_size = len(data)
    log.debug(f"input: {input_path} ({orig_size:,} bytes)")

    if source_path and not cfg.get("re_encode", True):
        data = copy_avcc_from_source(source_path, data)

    log.info("[1/7] parsing box structure...")
    top_boxes = parse_boxes(data, 0, len(data))

    ftyp_box = moov_box = mdat_box = None
    for box in top_boxes:
        if box["type"] == b'ftyp': ftyp_box = box
        elif box["type"] == b'moov': moov_box = box
        elif box["type"] == b'mdat': mdat_box = box

    if not moov_box or not mdat_box:
        log.error("missing moov or mdat, aborting.")
        return False

    log.debug(f"ftyp @ {ftyp_box['offset'] if ftyp_box else 'N/A'}")
    log.debug(f"moov @ {moov_box['offset']} ({moov_box['size']} bytes)")
    log.debug(f"mdat @ {mdat_box['offset']} ({mdat_box['size']} bytes)")

    log.info("[2/7] reconstructing layout (ftyp → moov → mdat)...")
    ftyp_data = data[ftyp_box["offset"]:ftyp_box["end"]] if ftyp_box else b''
    moov_data = bytearray(data[moov_box["offset"]:moov_box["end"]])

    mdat_header_size = 16 if read_u32be(data, mdat_box["offset"]) == 1 else 8
    mdat_payload = data[mdat_box["offset"] + mdat_header_size:mdat_box["end"]]
    mdat_data = build_box(b'mdat', mdat_payload)

    log.info("[3/7] patching moov...")
    moov_children = parse_boxes(moov_data, 8, len(moov_data))

    video_trak_idx = audio_trak_idx = tmcd_trak_idx = None
    for i, child in enumerate(moov_children):
        if child["type"] == b'trak':
            trak_children = parse_boxes(moov_data, child["offset"] + 8, child["end"])
            for tc in trak_children:
                if tc["type"] == b'mdia':
                    mdia_children = parse_boxes(moov_data, tc["offset"] + 8, tc["end"])
                    for mc in mdia_children:
                        if mc["type"] == b'hdlr':
                            ht = moov_data[mc["offset"]+16:mc["offset"]+20]
                            if ht == b'vide': video_trak_idx = i
                            elif ht == b'soun': audio_trak_idx = i
                            elif ht == b'tmcd': tmcd_trak_idx = i

    log.info(f"video trak: {video_trak_idx}, audio trak: {audio_trak_idx}, tmcd trak: {tmcd_trak_idx}")
    if audio_trak_idx is None:
        log.error("NO AUDIO TRACK FOUND - inflation will not run!")

    new_moov_children = []
    for i, child in enumerate(moov_children):
        if child["type"] == b'mvhd':
            mvhd = bytearray(moov_data[child["offset"]:child["end"]])
            mvhd = upgrade_mvhd_to_v1(mvhd)
            new_moov_children.append(bytes(mvhd))
        elif child["type"] == b'trak':
            trak_data = bytearray(moov_data[child["offset"]:child["end"]])
            is_tmcd = (i == tmcd_trak_idx)
            is_audio = (i == audio_trak_idx)
            is_video = (i == video_trak_idx)
            log.info(f"TRAK[{i}]: is_audio={is_audio}, is_video={is_video}, is_tmcd={is_tmcd}, inflate_clone={'YES' if is_audio else 'no'}")
            if is_audio:
                primary = patch_trak(trak_data, is_video, True, inflate=False, track_id=2, is_clone=False)
                new_moov_children.append(bytes(primary))
                log.debug("duplicating audio track for inflation...")
                clone = bytearray(trak_data)
                clone_patched = patch_trak(clone, False, True, inflate=True, track_id=4, is_clone=True)
                new_moov_children.append(bytes(clone_patched))
                log.info(f"CLONE APPENDED: size={len(clone_patched)}, total_traks={len([c for c in new_moov_children if len(c) > 8 and c[4:8] == b'trak'])}")
                log.debug("appended inflated audio clone after primary")
            elif is_tmcd:
                patched = patch_trak(trak_data, False, False, inflate=False, track_id=3, is_clone=False)
                new_moov_children.append(bytes(patched))
            else:
                patched = patch_trak(trak_data, is_video, False, inflate=False, track_id=1, is_clone=False)
                new_moov_children.append(bytes(patched))
        elif child["type"] == b'udta':
            log.debug("replacing existing udta.")
            continue
        else:
            new_moov_children.append(bytes(moov_data[child["offset"]:child["end"]]))

    combined_udta = build_combined_udta(cfg)
    new_moov_children.append(combined_udta)
    log.debug(f"injected dual metadata ({len(combined_udta)} bytes)")

    moov_payload = b''.join(new_moov_children)
    new_moov = build_box(b'moov', moov_payload)

    log.info(f"DIAG: audio_trak_idx={audio_trak_idx}, moov_children count={len(moov_children)}")
    for idx, ch in enumerate(moov_children):
        log.info(f"  child[{idx}]: type={ch['type']}")

    log.info("[4/7] assembling output...")
    output_data = bytearray(ftyp_data) + bytearray(new_moov) + mdat_data

    log.info("[5/7] fixing chunk offsets...")
    new_mdat_offset = len(ftyp_data) + len(new_moov) + 8
    old_mdat_payload_offset = mdat_box["offset"] + mdat_header_size
    offset_delta = new_mdat_offset - old_mdat_payload_offset
    if offset_delta != 0:
        fix_chunk_offsets(output_data, offset_delta)
        log.debug(f"shifted offsets by {offset_delta:+d}")

    log.info("[6/7] post-patch fixes...")
    zero_mp4a_samplerate(output_data)

    if source_path:
        spoof_audio_bitrate(output_data, source_path)
        fix_btrt_from_source(source_path, output_data)

    output_data = strip_free_boxes(output_data)

    log.info("[7/7] appending trailing data...")
    garbage = build_trailing_garbage(cfg.get("trailing_bytes", 184100))
    output_data += garbage
    log.debug(f"appended {len(garbage)} bytes of trailing padding.")

    final_moov = find_box(output_data, b'moov')
    if final_moov:
        final_mvhd = find_box(output_data, b'mvhd', final_moov["offset"], final_moov["end"])
        if final_mvhd:
            ntid_ver = output_data[final_mvhd["offset"] + 8]
            ntid_off = 96 if ntid_ver == 0 else 108
            actual_ntid = read_u32be(output_data, final_mvhd["offset"] + ntid_off)
            if actual_ntid != 5:
                log.warning(f"nexttrackid mismatch: expected 5, got {actual_ntid}")

    try:
        with open(input_path, 'wb') as f:
            f.write(bytes(output_data))
    except IOError as e:
        log.error(f"couldn't write to {input_path}: {e}")
        return False

    final_size = len(output_data)
    log.info(f"done! ({orig_size:,} bytes in, {final_size:,} bytes out)")
    return True


def patch_trak(trak_data: bytearray, is_video: bool, is_audio: bool,
               inflate: bool = False, track_id: int = 0, is_clone: bool = False) -> bytearray:
    trak_children = parse_boxes(trak_data, 8, len(trak_data))
    new_children = []

    for tc in trak_children:
        if tc["type"] == b'tkhd':
            tkhd = bytearray(trak_data[tc["offset"]:tc["end"]])
            ver = tkhd[8]
            if ver == 0:
                write_u32be(tkhd, 12, 0)
                write_u32be(tkhd, 16, 0)
            else:
                write_u64be(tkhd, 12, 0)
                write_u64be(tkhd, 20, 0)
            if track_id > 0:
                tid_off = 20 if ver == 0 else 28
                write_u32be(tkhd, tid_off, track_id)
            new_children.append(bytes(tkhd))
        elif tc["type"] == b'tref':
            if is_clone:
                log.debug("stripped tref on clone")
                continue
            new_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))
        elif tc["type"] == b'edts':
            if is_clone:
                log.debug("stripped edts/elst on clone")
                continue
            if is_video:
                edts_data = bytearray(trak_data[tc["offset"]:tc["end"]])
                edts_data = patch_elst_plus_one(edts_data)
                new_children.append(bytes(edts_data))
            else:
                new_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))
        elif tc["type"] == b'mdia':
            mdia_data = bytearray(trak_data[tc["offset"]:tc["end"]])
            mdia_data = patch_mdia(mdia_data, is_video, is_audio, inflate)
            new_children.append(bytes(mdia_data))
        else:
            new_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))

    return bytearray(build_box(b'trak', b''.join(new_children)))


def patch_mdia(mdia_data: bytearray, is_video: bool, is_audio: bool,
               inflate: bool = False) -> bytearray:
    mdia_children = parse_boxes(mdia_data, 8, len(mdia_data))
    new_children = []

    for mc in mdia_children:
        if mc["type"] == b'mdhd':
            mdhd = bytearray(mdia_data[mc["offset"]:mc["end"]])
            ver = mdhd[8]
            if ver == 0:
                write_u32be(mdhd, 12, 0)
                write_u32be(mdhd, 16, 0)
            else:
                write_u64be(mdhd, 12, 0)
                write_u64be(mdhd, 20, 0)
            new_children.append(bytes(mdhd))
        elif mc["type"] == b'hdlr':
            if is_video:
                new_children.append(build_hdlr(b'vide', "VideoHandler"))
            elif is_audio:
                new_children.append(build_hdlr(b'soun', "SoundHandler"))
            else:
                new_children.append(build_hdlr(b'vide', ""))
        elif mc["type"] == b'minf':
            minf_data = bytearray(mdia_data[mc["offset"]:mc["end"]])
            minf_data = patch_minf(minf_data, is_audio, inflate)
            new_children.append(bytes(minf_data))
        else:
            new_children.append(bytes(mdia_data[mc["offset"]:mc["end"]]))

    return bytearray(build_box(b'mdia', b''.join(new_children)))


def patch_minf(minf_data: bytearray, is_audio: bool, inflate: bool = False) -> bytearray:
    minf_children = parse_boxes(minf_data, 8, len(minf_data))
    new_children = []

    for mc in minf_children:
        if mc["type"] == b'nmhd':
            continue
        elif mc["type"] == b'stbl' and is_audio and inflate:
            stbl_data = bytearray(minf_data[mc["offset"]:mc["end"]])
            stbl_data = patch_stbl(stbl_data)
            new_children.append(bytes(stbl_data))
        else:
            new_children.append(bytes(minf_data[mc["offset"]:mc["end"]]))

    return bytearray(build_box(b'minf', b''.join(new_children)))


def patch_stbl(stbl_data: bytearray) -> bytearray:
    stbl_children = parse_boxes(stbl_data, 8, len(stbl_data))

    stsz_box = stts_box = stsc_box = stco_box = None
    for sc in stbl_children:
        if sc["type"] == b'stsz': stsz_box = sc
        elif sc["type"] == b'stts': stts_box = sc
        elif sc["type"] == b'stsc': stsc_box = sc
        elif sc["type"] == b'stco': stco_box = sc

    if not all([stsz_box, stts_box, stsc_box, stco_box]):
        return stbl_data

    inflate_factor = getattr(sys.modules[__name__], '_inflate_factor', 9)
    dummy_size = getattr(sys.modules[__name__], '_dummy_size', 8)

    stsz_payload = stbl_data[stsz_box["offset"] + 12:stsz_box["end"]]
    if len(stsz_payload) < 8:
        return stbl_data

    uniform_size = read_u32be(stsz_payload, 0)
    sample_count = read_u32be(stsz_payload, 4)

    original_sizes = []
    if uniform_size == 0:
        pos = 8
        for _ in range(sample_count):
            if pos + 4 > len(stsz_payload): break
            original_sizes.append(read_u32be(stsz_payload, pos))
            pos += 4
    else:
        original_sizes = [uniform_size] * sample_count

    if not original_sizes:
        return stbl_data

    real_count = len(original_sizes)
    extra_count = real_count * (inflate_factor - 1)

    stsc_payload = stbl_data[stsc_box["offset"] + 12:stsc_box["end"]]
    if len(stsc_payload) < 4:
        return stbl_data
    stsc_entry_count = read_u32be(stsc_payload, 0)

    stsc_entries = []
    pos = 4
    for _ in range(stsc_entry_count):
        if pos + 12 > len(stsc_payload): break
        fc = read_u32be(stsc_payload, pos)
        spc = read_u32be(stsc_payload, pos + 4)
        sdi = read_u32be(stsc_payload, pos + 8)
        stsc_entries.append((fc, spc, sdi))
        pos += 12

    last_sdi = stsc_entries[-1][2] if stsc_entries else 1

    stco_payload = stbl_data[stco_box["offset"] + 12:stco_box["end"]]
    if len(stco_payload) < 4:
        return stbl_data
    stco_entry_count = read_u32be(stco_payload, 0)

    stco_offsets = []
    pos = 4
    for _ in range(stco_entry_count):
        if pos + 4 > len(stco_payload): break
        stco_offsets.append(read_u32be(stco_payload, pos))
        pos += 4

    if not stco_offsets:
        return stbl_data

    def stsc_total(entries, chunks):
        total = 0
        for i, e in enumerate(entries):
            fc, spc, _ = e
            nfc = entries[i+1][0] if i+1 < len(entries) else chunks + 1
            if nfc > fc:
                total += (nfc - fc) * spc
        return total

    orig_chunks = len(stco_offsets)
    if not stsc_entries or stsc_total(stsc_entries, orig_chunks) != real_count:
        stsc_entries = [(1, real_count, last_sdi)]
        stco_offsets = [stco_offsets[0]]
        orig_chunks = 1

    first_dummy_offset = stco_offsets[0]

    new_sizes = list(original_sizes) + [dummy_size] * extra_count
    new_count = len(new_sizes)

    new_stsz_payload = struct.pack('>II', 0, new_count)
    for sz in new_sizes:
        new_stsz_payload += struct.pack('>I', sz)
    new_stsz = build_fullbox(b'stsz', 0, 0, new_stsz_payload)
    log.info(f"INFLATE: factor={inflate_factor}, real_count={real_count}, extra={extra_count}, new_count={new_count}")
    log.debug(f"inflated stsz: {real_count} → {new_count}")

    orig_stts_pay = stbl_data[stts_box["offset"] + 12:stts_box["end"]]
    orig_tc = read_u32be(orig_stts_pay, 0) if len(orig_stts_pay) >= 4 else 0

    ext_payload = struct.pack('>I', orig_tc + 1) + orig_stts_pay[4:]
    ext_payload += struct.pack('>II', extra_count, 1)
    new_stts = build_fullbox(b'stts', 0, 0, ext_payload)
    log.debug(f"extended stts: {orig_tc} → {orig_tc + 1}")

    new_stsc_entries = list(stsc_entries)
    if extra_count > 0:
        new_stsc_entries.append((orig_chunks + 1, extra_count, last_sdi))

    new_stsc_payload = struct.pack('>I', len(new_stsc_entries))
    for fc, spc, sdi in new_stsc_entries:
        new_stsc_payload += struct.pack('>III', fc, spc, sdi)
    new_stsc = build_fullbox(b'stsc', 0, 0, new_stsc_payload)
    log.debug(f"adjusted stsc: {stsc_entry_count} → {len(new_stsc_entries)}")

    new_stco_offsets = list(stco_offsets)
    if extra_count > 0:
        new_stco_offsets.append(first_dummy_offset)

    new_stco_payload = struct.pack('>I', len(new_stco_offsets))
    for off in new_stco_offsets:
        new_stco_payload += struct.pack('>I', off)
    new_stco = build_fullbox(b'stco', 0, 0, new_stco_payload)
    log.debug(f"adjusted stco: {stco_entry_count} → {len(new_stco_offsets)}")

    new_children = []
    for sc in stbl_children:
        if sc["type"] == b'stsz': new_children.append(new_stsz)
        elif sc["type"] == b'stts': new_children.append(new_stts)
        elif sc["type"] == b'stsc': new_children.append(new_stsc)
        elif sc["type"] == b'stco': new_children.append(new_stco)
        else: new_children.append(bytes(stbl_data[sc["offset"]:sc["end"]]))

    return bytearray(build_box(b'stbl', b''.join(new_children)))


def fix_chunk_offsets(data: bytearray, delta: int):
    top_boxes = parse_boxes(data, 0, len(data))
    for box in top_boxes:
        if box["type"] == b'moov':
            fix_offsets_recursive(data, box["offset"] + 8, box["end"], delta)


def fix_offsets_recursive(data: bytearray, start: int, end: int, delta: int):
    pos = start
    while pos + 8 <= end:
        sz = read_u32be(data, pos)
        if sz < 8 or pos + sz > end: break
        typ = data[pos+4:pos+8]
        if typ == b'stco':
            cnt = read_u32be(data, pos + 12)
            for i in range(cnt):
                v = read_u32be(data, pos + 16 + i*4)
                if v > 0: write_u32be(data, pos + 16 + i*4, v + delta)
        elif typ == b'co64':
            cnt = read_u32be(data, pos + 12)
            for i in range(cnt):
                hi = read_u32be(data, pos + 16 + i*8)
                lo = read_u32be(data, pos + 20 + i*8)
                v = (hi << 32) + lo
                if v > 0:
                    v += delta
                    write_u32be(data, pos + 16 + i*8, (v >> 32) & 0xFFFFFFFF)
                    write_u32be(data, pos + 20 + i*8, v & 0xFFFFFFFF)
        elif typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl'):
            fix_offsets_recursive(data, pos + 8, pos + sz, delta)
        pos += sz


def encode_for_tiktok(input_path: str, output_path: str, crf: int = 18, codec: str = "h264") -> bool:
    if codec == "h264":
        video_args = [
            "-c:v", "libx264", "-preset", "medium",
            "-crf", str(crf), "-level", "4.2",
            "-pix_fmt", "yuv420p",
            "-g", "9999",
            "-bf", "0",
            "-force_key_frames", "0,9.1,18.2",
        ]
    else:
        video_args = [
            "-c:v", "libx265", "-preset", "medium",
            "-crf", str(crf), "-pix_fmt", "yuv420p10le",
            "-x265-params", "log-level=error",
            "-g", "9999",
            "-bf", "0",
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
        "-y", output_path
    ]

    log.debug(f"running ffmpeg: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        error_msg = result.stderr[-800:] if len(result.stderr) > 800 else result.stderr
        log.error(f"ffmpeg encode error:\n{error_msg}")
        return False

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        log.info(f"encoded successfully ({size_mb:.1f} MB)")
        return True

    log.error("encode failed: output file not created.")
    return False


def remux_for_tiktok(input_path: str, output_path: str) -> bool:
    """Remux without re-encoding: strips metadata, normalizes handlers, copies streams."""
    cmd = [
        "ffmpeg", "-i", input_path,
        "-c:v", "copy",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-metadata:s:v", "handler_name=VideoHandler",
        "-metadata:s:a", "handler_name=SoundHandler",
        "-map_metadata:s:v", "0:s:v",
        "-map_metadata:s:a", "0:s:a",
        "-avoid_negative_ts", "disabled",
        "-copyinkf",
        "-y", output_path
    ]

    log.debug(f"running ffmpeg remux: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        error_msg = result.stderr[-800:] if len(result.stderr) > 800 else result.stderr
        log.error(f"ffmpeg remux error:\n{error_msg}")
        return False

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        log.info(f"remuxed successfully ({size_mb:.1f} MB)")
        return True

    log.error("remux failed: output file not created.")
    return False

# module-level globals set before patching
_inflate_factor = DEFAULTS["inflation_rate"]
_dummy_size = DEFAULTS["dummy_sample_size"]


def set_runtime_params(inflation_rate: int, dummy_sample_size: int):
    global _inflate_factor, _dummy_size
    _inflate_factor = inflation_rate
    _dummy_size = dummy_sample_size