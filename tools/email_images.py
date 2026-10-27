"""Render private contact addresses as PNGs using only Python's standard library.

The bundled, pre-rendered glyph atlas keeps website updates dependency-free.
It uses Liberation Sans under the SIL Open Font License; see
``email_font_license.txt``. The generated PNG contains pixels and no metadata.
"""
from __future__ import annotations

import base64
from functools import lru_cache
import json
from pathlib import Path
import struct
import zlib


SCALE = 3
COLOR = (0, 70, 132)  # Official N.C. A&T Aggie Blue, #004684.


@lru_cache(maxsize=1)
def _font():
    atlas = json.loads(Path(__file__).with_name("email_font.json").read_text(encoding="ascii"))
    for glyph in atlas["glyphs"].values():
        glyph[5] = zlib.decompress(base64.b64decode(glyph[5])) if glyph[5] else b""
    return atlas


def _chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))


def render_email(address):
    """Return ``(PNG bytes, native width, native height)`` for an ASCII address.

    The native raster is 3 times its display size. Use ``width / SCALE`` and
    ``height / SCALE`` as CSS dimensions. Address validation belongs to the
    caller; this renderer accepts printable ASCII and rejects control characters
    or unsupported characters instead of silently rendering an incorrect value.
    """
    if not isinstance(address, str) or not address or len(address) > 320:
        raise ValueError("Email image text must contain 1 to 320 characters.")
    if any(ord(char) < 32 or ord(char) > 126 for char in address):
        raise ValueError("Email images support printable ASCII addresses only.")

    atlas = _font()
    cursor = atlas["padding"] * 64
    placements = []
    rightmost = 0
    for char in address:
        advance, left, top, width, height, mask = atlas["glyphs"][char]
        x = (cursor + 32) // 64 + left
        y = atlas["baseline"] + top
        placements.append((x, y, width, height, mask))
        rightmost = max(rightmost, x + width)
        cursor += advance

    # Whole CSS pixels avoid browser resampling between fractional dimensions.
    width = max((cursor + 63) // 64, rightmost) + atlas["padding"]
    width = ((width + SCALE - 1) // SCALE) * SCALE
    height = atlas["height"]
    alpha = bytearray(width * height)
    for x, y, glyph_width, glyph_height, mask in placements:
        for gy in range(glyph_height):
            row_start = (y + gy) * width + x
            mask_start = gy * glyph_width
            for gx in range(glyph_width):
                dest = row_start + gx
                alpha[dest] = max(alpha[dest], mask[mask_start + gx])

    raw = bytearray()
    red, green, blue = COLOR
    for row in range(height):
        raw.append(0)  # PNG's None filter.
        for value in alpha[row * width:(row + 1) * width]:
            raw.extend((red, green, blue, value))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
           + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b""))
    return png, width, height
