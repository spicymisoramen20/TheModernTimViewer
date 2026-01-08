# timlib.py
import os
import json
import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PIL import Image


# -----------------------------
# TIM parsing utilities
# -----------------------------

def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]

def u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]

def ps1_15bit_to_rgba(c: int) -> Tuple[int, int, int, int]:
    """
    PS1 TIM color: 0b0BBBBBGGGGGRRRRR (15-bit), bit15 often STP.
    We treat color 0 (lower 15 bits == 0) as transparent in indexed modes.
    """
    r5 = (c >> 0) & 0x1F
    g5 = (c >> 5) & 0x1F
    b5 = (c >> 10) & 0x1F

    r = (r5 << 3) | (r5 >> 2)
    g = (g5 << 3) | (g5 >> 2)
    b = (b5 << 3) | (b5 >> 2)

    a = 0 if (c & 0x7FFF) == 0 else 255
    return (r, g, b, a)


# -----------------------------
# Data structures
# -----------------------------

@dataclass
class TimClut:
    source_path: str
    clut_index: int
    colors: List[Tuple[int, int, int, int]]
    raw_15bit: List[int]
    width: int
    height: int
    row: int

    @property
    def label(self) -> str:
        base = os.path.basename(self.source_path)
        return f"{base} | CLUT #{self.clut_index} (row {self.row}, {self.width} cols)"


@dataclass
class TimImage:
    path: str
    original_bytes: bytes          # original TIM bytes (for reference)
    flags: int                     # TIM flags word (bpp + hasClut bit)
    bpp_mode: int                  # 0=4bpp, 1=8bpp, 2=16bpp, 3=24bpp (not implemented)
    has_clut: bool

    img_x: int
    img_y: int
    img_w_words: int               # width in 16-bit words
    img_h: int                     # height in pixels
    img_data: bytes                # image payload only

    # Raw CLUT block bytes (includes the CLUT block length field) so we can rebuild the TIM file.
    clut_block_raw: Optional[bytes] = None

    applied_clut: Optional[TimClut] = None

    def pixel_width(self) -> int:
        # TIM image width field is in 16-bit words.
        if self.bpp_mode == 0:   # 4bpp: 1 word = 4 pixels
            return self.img_w_words * 4
        if self.bpp_mode == 1:   # 8bpp: 1 word = 2 pixels
            return self.img_w_words * 2
        if self.bpp_mode == 2:   # 16bpp: 1 word = 1 pixel
            return self.img_w_words
        if self.bpp_mode == 3:   # 24bpp: not implemented correctly here
            return self.img_w_words * 2
        return self.img_w_words


# -----------------------------
# TIM parsing / CLUT extraction
# -----------------------------

def parse_tim(path: str) -> TimImage:
    with open(path, "rb") as f:
        data = f.read()

    if len(data) < 8:
        raise ValueError("File too small to be a TIM")

    magic = u32(data, 0)
    if magic != 0x10:
        raise ValueError("Not a TIM (magic != 0x10)")

    flags = u32(data, 4)
    bpp_mode = flags & 0x7
    has_clut = (flags & 0x8) != 0

    off = 8
    clut_block_raw = None

    if has_clut:
        if len(data) < off + 12:
            raise ValueError("TIM CLUT block truncated")

        clut_block_len = u32(data, off)
        if len(data) < off + clut_block_len:
            raise ValueError("TIM CLUT block truncated (declared length too large)")

        clut_block_raw = data[off: off + clut_block_len]
        off += clut_block_len

    if len(data) < off + 12:
        raise ValueError("TIM image block truncated")

    img_block_len = u32(data, off)
    if len(data) < off + img_block_len:
        raise ValueError("TIM image block truncated (declared length too large)")

    img_x = u16(data, off + 4)
    img_y = u16(data, off + 6)
    img_w_words = u16(data, off + 8)
    img_h = u16(data, off + 10)

    img_data_off = off + 12
    img_data_len = img_block_len - 12
    img_data = data[img_data_off: img_data_off + img_data_len]

    return TimImage(
        path=path,
        original_bytes=data,
        flags=flags,
        bpp_mode=bpp_mode,
        has_clut=has_clut,
        img_x=img_x,
        img_y=img_y,
        img_w_words=img_w_words,
        img_h=img_h,
        img_data=img_data,
        clut_block_raw=clut_block_raw,
    )

def extract_cluts_from_raw_block(tim: TimImage) -> List[TimClut]:
    """
    Parse CLUT rows out of tim.clut_block_raw (if present).
    """
    if not tim.has_clut or not tim.clut_block_raw:
        return []

    blk = tim.clut_block_raw
    if len(blk) < 12:
        return []

    clut_block_len = u32(blk, 0)
    clut_w = u16(blk, 8)
    clut_h = u16(blk, 10)

    raw = blk[12:clut_block_len]
    if len(raw) < 2:
        return []
    if len(raw) % 2 != 0:
        raw = raw[:-1]

    words = list(struct.unpack("<" + "H" * (len(raw)//2), raw))

    w = clut_w
    h = clut_h
    if w <= 0:
        return []

    if len(words) < w * h:
        h = max(1, len(words) // w)

    cluts: List[TimClut] = []
    idx = 0
    for row in range(h):
        row_words = words[idx: idx + w]
        idx += w
        rgba = [ps1_15bit_to_rgba(c) for c in row_words]
        cluts.append(
            TimClut(
                source_path=tim.path,
                clut_index=row,
                colors=rgba,
                raw_15bit=row_words,
                width=w,
                height=h,
                row=row,
            )
        )
    return cluts


# -----------------------------
# Decode / render
# -----------------------------

def decode_indices(tim: TimImage) -> List[int]:
    mode = tim.bpp_mode
    wpx = tim.pixel_width()
    hpx = tim.img_h

    if mode == 0:
        out: List[int] = [0] * (wpx * hpx)
        o = 0
        for b in tim.img_data:
            if o >= len(out): break
            out[o] = b & 0x0F
            o += 1
            if o >= len(out): break
            out[o] = (b >> 4) & 0x0F
            o += 1
        return out

    if mode == 1:
        out: List[int] = [0] * (wpx * hpx)
        n = min(len(out), len(tim.img_data))
        out[:n] = tim.img_data[:n]
        return out

    raise ValueError("decode_indices called for non-indexed TIM")

def render_tim_to_image(tim: TimImage, clut: Optional[TimClut]) -> Image.Image:
    mode = tim.bpp_mode
    wpx = tim.pixel_width()
    hpx = tim.img_h

    if mode == 2:
        expected_words = tim.img_w_words * tim.img_h
        raw = tim.img_data[: (len(tim.img_data)//2)*2]
        words = struct.unpack("<" + "H" * (len(raw)//2), raw)
        words = words[:expected_words]

        out = Image.new("RGBA", (wpx, hpx))
        px = out.load()
        i = 0
        for y in range(hpx):
            for x in range(wpx):
                px[x, y] = ps1_15bit_to_rgba(words[i]) if i < len(words) else (0, 0, 0, 0)
                i += 1
        return out

    if mode in (0, 1):
        indices = decode_indices(tim)

        if clut is None:
            out = Image.new("RGBA", (wpx, hpx))
            px = out.load()
            for y in range(hpx):
                base = y * wpx
                for x in range(wpx):
                    v = indices[base + x] & 0xFF
                    px[x, y] = (v, v, v, 255)
            return out

        palette = clut.colors
        plen = len(palette)
        out = Image.new("RGBA", (wpx, hpx))
        px = out.load()
        for y in range(hpx):
            base = y * wpx
            for x in range(wpx):
                idx = indices[base + x]
                px[x, y] = palette[idx] if idx < plen else (255, 0, 255, 255)
        return out

    raise NotImplementedError(f"TIM bpp mode {mode} not supported in this tool.")


# -----------------------------
# Animation helpers
# -----------------------------

def auto_detect_frames(sheet_w: int, sheet_h: int) -> Tuple[int, int, str, int]:
    """
    Heuristic:
      - If tall and divisible: vertical strip of square frames
      - If wide and divisible: horizontal strip of square frames
      - Else: single frame
    """
    if sheet_w > 0 and sheet_h > 0:
        if sheet_h % sheet_w == 0 and sheet_h >= sheet_w:
            fw = sheet_w
            fh = sheet_w
            return fw, fh, "vertical", max(1, sheet_h // sheet_w)
        if sheet_w % sheet_h == 0 and sheet_w >= sheet_h:
            fw = sheet_h
            fh = sheet_h
            return fw, fh, "horizontal", max(1, sheet_w // sheet_h)
    return sheet_w, sheet_h, "horizontal", 1

def slice_frames_fixed(sheet: Image.Image, frame_w: int, frame_h: int, direction: str) -> List[Image.Image]:
    if frame_w <= 0 or frame_h <= 0:
        return [sheet]

    sw, sh = sheet.size
    frames: List[Image.Image] = []

    def crop_padded(x0: int, y0: int) -> Image.Image:
        part = sheet.crop((x0, y0, x0 + frame_w, y0 + frame_h))
        out = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
        out.paste(part, (0, 0))
        return out

    if direction == "vertical":
        count = max(1, sh // frame_h)
        for i in range(count):
            frames.append(crop_padded(0, i * frame_h))
    else:
        count = max(1, sw // frame_w)
        for i in range(count):
            frames.append(crop_padded(i * frame_w, 0))

    return frames if frames else [sheet]


# -----------------------------
# Index export/import (resizable)
# -----------------------------

def make_grayscale_palette(num_entries: int) -> List[int]:
    pal = []
    for i in range(256):
        if i < num_entries:
            v = int(round(i * 255 / (num_entries - 1))) if num_entries > 1 else 0
            pal.extend([v, v, v])
        else:
            pal.extend([0, 0, 0])
    return pal

def export_indices_png_and_meta(tim: TimImage, out_png_path: str) -> str:
    if tim.bpp_mode not in (0, 1):
        raise ValueError("Index export only applies to 4bpp/8bpp TIMs.")

    w = tim.pixel_width()
    h = tim.img_h
    indices = decode_indices(tim)

    img = Image.new("P", (w, h))
    img.putdata([i & 0xFF for i in indices])

    num_entries = 16 if tim.bpp_mode == 0 else 256
    img.putpalette(make_grayscale_palette(num_entries))

    os.makedirs(os.path.dirname(out_png_path) or ".", exist_ok=True)
    img.save(out_png_path, "PNG", optimize=False)

    meta = {
        "format": "tim_index_edit_v2",
        "source_tim": tim.path,
        "bpp_mode": tim.bpp_mode,
        "width": w,
        "height": h,
        "note": (
            "PNG is indexed (mode P). Pixel values are the indices. "
            "You may upscale the PNG; on import we can resize the TIM to match the PNG."
        ),
    }

    json_path = os.path.splitext(out_png_path)[0] + ".json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return json_path

def words_for_width_pixels(bpp_mode: int, width_px: int) -> int:
    if bpp_mode == 0:
        if width_px % 4 != 0:
            raise ValueError("4bpp TIM width must be a multiple of 4 pixels.")
        return width_px // 4
    if bpp_mode == 1:
        if width_px % 2 != 0:
            raise ValueError("8bpp TIM width must be a multiple of 2 pixels.")
        return width_px // 2
    if bpp_mode == 2:
        return width_px
    raise ValueError("Unsupported bpp for resizing")

def pack_indices_for_size(indices: List[int], bpp_mode: int, width_px: int, height_px: int) -> bytes:
    expected = width_px * height_px
    if len(indices) != expected:
        raise ValueError(f"Index pixel count mismatch: expected {expected}, got {len(indices)}")

    if bpp_mode == 1:
        return bytes((v & 0xFF) for v in indices)

    if bpp_mode == 0:
        out = bytearray()
        for i in range(0, len(indices), 2):
            a = indices[i] & 0x0F
            b = (indices[i + 1] & 0x0F) if (i + 1) < len(indices) else 0
            out.append(a | (b << 4))
        return bytes(out)

    raise ValueError("pack_indices_for_size only supports 4bpp/8bpp")

def import_indices_from_png_resize_tim(tim: TimImage, png_path: str, meta_path: Optional[str]) -> None:
    if tim.bpp_mode not in (0, 1):
        raise ValueError("This import mode is only for 4bpp/8bpp TIMs.")

    if meta_path:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("format") not in ("tim_index_edit_v1", "tim_index_edit_v2"):
            raise ValueError("Meta JSON format not recognized.")
        if int(meta.get("bpp_mode", -1)) != tim.bpp_mode:
            raise ValueError("Meta bpp_mode does not match currently selected TIM.")

    img = Image.open(png_path)
    if img.mode != "P":
        raise ValueError(
            "Edited PNG is not indexed (mode 'P').\n\n"
            "For reliable import, keep the PNG in indexed mode and avoid anti-aliasing."
        )

    new_w, new_h = img.size
    indices = list(img.getdata())

    if tim.bpp_mode == 0 and any(v > 15 for v in indices):
        raise ValueError("4bpp import: found indices > 15. Keep indices in 0..15.")

    tim.img_w_words = words_for_width_pixels(tim.bpp_mode, new_w)
    tim.img_h = new_h
    tim.img_data = pack_indices_for_size(indices, tim.bpp_mode, new_w, new_h)

def build_tim_bytes(tim: TimImage) -> bytes:
    out = bytearray()
    out += struct.pack("<I", 0x10)
    out += struct.pack("<I", tim.flags)

    if tim.has_clut:
        if not tim.clut_block_raw:
            raise ValueError("TIM claims to have CLUT but clut_block_raw is missing.")
        out += tim.clut_block_raw

    img_block_len = 12 + len(tim.img_data)
    out += struct.pack("<I", img_block_len)
    out += struct.pack("<HHHH", tim.img_x, tim.img_y, tim.img_w_words, tim.img_h)
    out += tim.img_data
    return bytes(out)
