"""Latest-only emulator video. Encoding happens in the dashboard process."""
import struct
import zlib

DISPLAY_FPS = 30
VISIBLE_WIDTH = 256  # Same padded-edge crop as GameDisplay.


def frame_packet(image, *, run_id, episode, frame, captured, held, phase):
    if image is None:
        return None
    if image.ndim != 3 or image.shape[2] != 3 or str(image.dtype) != "uint8":
        raise ValueError("Expected an RGB uint8 emulator frame")
    visible = image[:, :VISIBLE_WIDTH, :]
    height, width, _ = visible.shape
    return {"run_id": run_id, "episode": episode, "frame": frame, "captured": captured,
            "width": width, "height": height, "rgb": visible.tobytes(),
            "held": list(held), "phase": phase}


def encode_png(packet):
    width, height, rgb = packet["width"], packet["height"], packet["rgb"]
    if not (0 < width <= 1024 and 0 < height <= 1024) or len(rgb) != width * height * 3:
        raise ValueError("Invalid RGB frame size")

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data +
                struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    stride = width * 3
    pixels = b"".join(b"\0" + rgb[row:row + stride] for row in range(0, len(rgb), stride))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(pixels, level=1)) + chunk(b"IEND", b""))
