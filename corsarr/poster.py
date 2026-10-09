"""Stand-in poster for titles without one.

A browsable card is a photo message, and Telegram cannot turn a photo message into a text message
while paging, so every page needs an image. Plain PNG written with the standard library only.
"""
from __future__ import annotations

import functools
import struct
import zlib

WIDTH, HEIGHT = 400, 600
COLOR = (38, 42, 51)  # dark slate, readable behind Telegram's caption overlay in both themes


@functools.cache
def placeholder_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    row = b"\x00" + bytes(COLOR) * WIDTH  # filter byte 0, then RGB pixels
    header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)  # 8 bit, truecolor
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(row * HEIGHT, 9)) + chunk(b"IEND", b""))
