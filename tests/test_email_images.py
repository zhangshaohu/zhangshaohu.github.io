"""Validate contact PNGs without installing an image or font library."""
from pathlib import Path
import struct
import sys
import unittest
import zlib


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from email_images import COLOR, SCALE, render_email


def decode_png(png):
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("Invalid PNG signature")
    offset, chunks, compressed = 8, [], bytearray()
    while offset < len(png):
        length = struct.unpack(">I", png[offset:offset + 4])[0]
        kind = png[offset + 4:offset + 8]
        data = png[offset + 8:offset + 8 + length]
        checksum = struct.unpack(">I", png[offset + 8 + length:offset + 12 + length])[0]
        if checksum != zlib.crc32(kind + data) & 0xffffffff:
            raise AssertionError("Invalid PNG checksum")
        chunks.append(kind)
        if kind == b"IHDR":
            width, height, bits, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            if (bits, color, compression, filtering, interlace) != (8, 6, 0, 0, 0):
                raise AssertionError("Unexpected PNG format")
        elif kind == b"IDAT":
            compressed.extend(data)
        offset += 12 + length
    raw = zlib.decompress(compressed)
    rows = []
    for row in range(height):
        start = row * (1 + width * 4)
        if raw[start] != 0:
            raise AssertionError("Unexpected row filter")
        rows.append(raw[start + 1:start + 1 + width * 4])
    return width, height, b"".join(rows), chunks


class EmailImageTests(unittest.TestCase):
    def test_png_is_transparent_crisp_and_contains_no_address_metadata(self):
        address = "professor.smith+lab@example.edu"
        png, width, height = render_email(address)
        decoded_width, decoded_height, pixels, chunks = decode_png(png)
        self.assertEqual((decoded_width, decoded_height), (width, height))
        self.assertEqual((width % SCALE, height % SCALE), (0, 0))
        self.assertEqual(height // SCALE, 24)
        self.assertGreater(width // SCALE, 100)
        self.assertEqual(chunks, [b"IHDR", b"IDAT", b"IEND"])
        self.assertNotIn(address.encode(), png)
        self.assertEqual(len(pixels), width * height * 4)
        self.assertIn(0, pixels[3::4])
        self.assertIn(255, pixels[3::4])
        self.assertTrue(any(0 < value < 255 for value in pixels[3::4]))
        for offset in range(0, len(pixels), 4):
            self.assertEqual(tuple(pixels[offset:offset + 3]), COLOR)
        # All four edges have transparent padding, including descenders.
        self.assertFalse(any(pixels[3:width * 4:4]))
        self.assertFalse(any(pixels[-width * 4 + 3::4]))
        self.assertFalse(any(pixels[row * width * 4 + 3] for row in range(height)))
        self.assertFalse(any(pixels[(row + 1) * width * 4 - 1] for row in range(height)))

    def test_address_changes_are_deterministic_and_render_distinct_glyphs(self):
        original = render_email("person@example.edu")
        self.assertEqual(original, render_email("person@example.edu"))
        self.assertNotEqual(original[0], render_email("pers0n@example.edu")[0])
        self.assertGreater(render_email("www@example.edu")[1], render_email("iii@example.edu")[1])

    def test_all_printable_ascii_renders_without_clipping(self):
        png, width, height = render_email("".join(chr(code) for code in range(32, 127)))
        decoded_width, decoded_height, pixels, _ = decode_png(png)
        self.assertEqual((decoded_width, decoded_height), (width, height))
        self.assertTrue(any(pixels[3::4]))
        self.assertFalse(any(pixels[3:width * 4:4]))
        self.assertFalse(any(pixels[-width * 4 + 3::4]))

    def test_unsupported_values_fail_clearly(self):
        for value in (None, "", "a" * 321, "user\n@example.edu", "名字@example.edu", "x\x7f@example.edu"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                render_email(value)


if __name__ == "__main__":
    unittest.main()
