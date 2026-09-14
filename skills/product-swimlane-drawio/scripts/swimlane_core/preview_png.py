"""Bounded PNG integrity checks; no renderer, pixel recognition or metadata execution."""
from __future__ import annotations

import binascii
import hashlib
import struct
import zlib

from . import contracts

PNG_MAX_BYTES = 32 * 1024 * 1024
PNG_MAX_DIMENSION = 16_384
PNG_MAX_PIXELS = 32_000_000
PNG_MAX_DECODED = 128 * 1024 * 1024
PNG_TOTAL_BYTES = 128 * 1024 * 1024


def png_failure(message, *, unsupported=False, resource=False, **evidence):
    code = "review/resource-limit" if resource else "preview/format-unsupported" if unsupported else "preview/png-invalid"
    raise contracts.DiagramError(message, code=code, evidence=evidence)


def inspect_png(raw: bytes):
    """Validate a complete RGB/RGBA8 noninterlaced stream within fixed budgets."""
    if len(raw) > PNG_MAX_BYTES:
        png_failure("PNG exceeds its raw byte budget", resource=True, limit=PNG_MAX_BYTES)
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        png_failure("Invalid PNG signature")
    offset, width, height, channels = 8, 0, 0, 0
    seen_header = seen_data = ended_data = seen_end = seen_palette = False
    compressed = []
    while offset < len(raw):
        if len(raw) - offset < 12:
            png_failure("Truncated PNG chunk")
        size = struct.unpack_from(">I", raw, offset)[0]
        if size > len(raw) - offset - 12:
            png_failure("PNG chunk length exceeds the actual file")
        kind = raw[offset + 4:offset + 8]
        if any(not (65 <= byte <= 90 or 97 <= byte <= 122) for byte in kind) or kind[2] & 32:
            png_failure("Invalid PNG chunk type")
        content = memoryview(raw)[offset + 8:offset + 8 + size]
        crc = struct.unpack_from(">I", raw, offset + 8 + size)[0]
        if binascii.crc32(content, binascii.crc32(kind)) & 0xffffffff != crc:
            png_failure("PNG chunk CRC mismatch", chunk=kind.decode("ascii"))
        offset += size + 12
        if not seen_header and kind != b"IHDR":
            png_failure("PNG must begin with IHDR")
        if kind == b"IHDR":
            if seen_header or size != 13:
                png_failure("PNG IHDR must be unique and exactly 13 bytes")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", content)
            seen_header = True
            if not width or not height:
                png_failure("PNG dimensions must be positive")
            if width > PNG_MAX_DIMENSION or height > PNG_MAX_DIMENSION or width * height > PNG_MAX_PIXELS:
                png_failure("PNG dimensions exceed the supported budget", resource=True)
            if depth != 8 or color not in (2, 6) or compression != 0 or filtering != 0 or interlace != 0:
                png_failure("Only noninterlaced RGB8/RGBA8 PNG is supported", unsupported=True)
            channels = 3 if color == 2 else 4
            expected = height * (1 + width * channels)
            if expected > PNG_MAX_DECODED:
                png_failure("PNG decoded scanlines exceed their budget", resource=True, limit=PNG_MAX_DECODED)
        elif kind == b"IDAT":
            if ended_data:
                png_failure("PNG IDAT chunks must be consecutive")
            seen_data = True
            compressed.append(content)
        elif kind == b"IEND":
            if size or not seen_data or offset != len(raw):
                png_failure("PNG must end with one empty IEND after image data")
            seen_end = True
            break
        else:
            if seen_data:
                ended_data = True
            if kind in (b"acTL", b"fcTL", b"fdAT"):
                png_failure("Animated PNG is unsupported", unsupported=True)
            if kind == b"PLTE":
                if seen_palette or seen_data or not size or size > 768 or size % 3:
                    png_failure("Invalid optional PNG palette")
                seen_palette = True
            elif not kind[0] & 32:
                png_failure("Unknown critical PNG chunk", unsupported=True, chunk=kind.decode("ascii"))
    if not seen_end:
        png_failure("PNG is missing IEND")
    decoder = zlib.decompressobj()
    try:
        decoded = decoder.decompress(b"".join(compressed), expected + 1)
    except zlib.error as exc:
        png_failure("Invalid PNG compressed data", reason=str(exc))
    if (len(decoded) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail):
        png_failure("PNG compressed stream or decoded size is not exact")
    stride = 1 + width * channels
    if any(decoded[index] > 4 for index in range(0, expected, stride)):
        png_failure("Invalid PNG row filter")
    return {"png_validation_version": 1, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            "width": width, "height": height, "bit_depth": depth, "color_type": color, "interlace": interlace,
            "decoded_bytes": expected, "verification": "png_integrity_only"}
