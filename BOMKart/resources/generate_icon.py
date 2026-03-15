"""Generate a simple 24x24 BOMKart icon PNG using pure Python."""
import struct
import zlib

def create_png(width, height, pixels):
    def chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    raw = b''
    for y in range(height):
        raw += b'\x00'
        for x in range(width):
            r, g, b, a = pixels[y * width + x]
            raw += struct.pack('BBBB', r, g, b, a)

    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
    idat = chunk(b'IDAT', zlib.compress(raw))
    iend = chunk(b'IEND', b'')
    return sig + ihdr + idat + iend

W, H = 24, 24
T = (0, 0, 0, 0)
B = (52, 152, 219, 255)
G = (46, 204, 113, 255)
D = (44, 62, 80, 255)

pixels = [T] * (W * H)

# Cart body
for x in range(5, 20):
    pixels[10 * W + x] = B
    pixels[17 * W + x] = B
for y in range(10, 18):
    pixels[y * W + 5] = B
    pixels[y * W + 19] = B

# Handle
for x in range(2, 7):
    pixels[9 * W + x] = D
for y in range(7, 10):
    pixels[y * W + 2] = D

# Wheels
for dx in [-1, 0, 1]:
    for dy in [0, 1]:
        pixels[(19 + dy) * W + (8 + dx)] = D
        pixels[(19 + dy) * W + (16 + dx)] = D

# "B" letter
for y in range(12, 17):
    pixels[y * W + 9] = G
for x in range(9, 15):
    pixels[12 * W + x] = G
    pixels[14 * W + x] = G
    pixels[16 * W + x] = G
pixels[13 * W + 14] = G
pixels[15 * W + 14] = G

import os
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bomkart_icon.png')
with open(out, 'wb') as f:
    f.write(create_png(W, H, pixels))
print(f"Icon: {out}")
