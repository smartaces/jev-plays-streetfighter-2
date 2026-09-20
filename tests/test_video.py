import struct
import time
import unittest
import zlib
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from controller.dashboard import DashboardBridge
from controller.video import frame_packet, encode_png


class VideoTests(unittest.TestCase):
    def test_png_crop_pixels_orientation_and_detached_copy(self):
        source = np.arange(4 * 320 * 3, dtype=np.uint8).reshape(4, 320, 3)
        expected = source[:, :256].tobytes()
        packet = frame_packet(source, run_id="example", episode=2, frame=10,
                              captured=1.25, held=["DOWN", "Y"], phase="entering_buttons")
        source.fill(0)
        self.assertEqual(packet["rgb"], expected)
        encoded = encode_png(packet)
        self.assertEqual(encoded[:8], b"\x89PNG\r\n\x1a\n")
        offset, chunks = 8, {}
        while offset < len(encoded):
            length = struct.unpack(">I", encoded[offset:offset + 4])[0]
            kind = encoded[offset + 4:offset + 8]
            data = encoded[offset + 8:offset + 8 + length]
            crc = struct.unpack(">I", encoded[offset + 8 + length:offset + 12 + length])[0]
            self.assertEqual(crc, zlib.crc32(kind + data) & 0xffffffff)
            chunks[kind] = data
            offset += 12 + length
        self.assertEqual(struct.unpack(">II", chunks[b"IHDR"][:8]), (256, 4))
        pixels = zlib.decompress(chunks[b"IDAT"])
        self.assertEqual(pixels, b"".join(b"\0" + expected[i:i + 768] for i in range(0, len(expected), 768)))

    def test_invalid_video_input_is_rejected(self):
        with self.assertRaises(ValueError):
            encode_png({"width": 256, "height": 224, "rgb": b"too short"})
        with self.assertRaises(ValueError):
            frame_packet(np.zeros((10, 10, 4), dtype=np.uint8), run_id="x", episode=0,
                         frame=1, captured=0, held=[], phase="idle")

    def test_full_display_queue_drops_frame_without_marking_it_sent(self):
        bridge = DashboardBridge.__new__(DashboardBridge)
        bridge.frames = Queue(maxsize=1)
        bridge.frames.put("busy")
        bridge.last_video_at = 0
        bridge.last_video_key = None
        app = SimpleNamespace(game=SimpleNamespace(image=np.zeros((4, 320, 3),dtype=np.uint8),frame=10,buttons=["X"]),
            recorder=SimpleNamespace(run_id="test"), episode=2, epoch=3, previous_buttons=[1],
            actions=SimpleNamespace(execution_state=lambda:{"phase":"entering_buttons"}))
        with patch("controller.dashboard.time.monotonic",return_value=10):
            bridge.publish_video(app)
        self.assertIsNone(bridge.last_video_key)
        self.assertEqual(bridge.frames.get_nowait(),"busy")
        with patch("controller.dashboard.time.monotonic",return_value=10.1):
            bridge.publish_video(app)
        self.assertEqual(bridge.last_video_key,(2,10,3))
        packet=bridge.frames.get_nowait()
        self.assertEqual(packet["held"],["X"])
        with patch("controller.dashboard.time.monotonic",return_value=10.2):
            bridge.publish_video(app)
        self.assertTrue(bridge.frames.empty())  # Paused image isn't re-enqueued.
        app.epoch += 1
        app.previous_buttons = [0]
        with patch("controller.dashboard.time.monotonic",return_value=10.3):
            bridge.publish_video(app)
        self.assertEqual(bridge.frames.get_nowait()["held"],[])

    def test_mute_acknowledges_audio_state_without_toggling_game_pause(self):
        bridge=DashboardBridge.__new__(DashboardBridge)
        bridge.commands=Queue();bridge.ready=Queue();bridge.process=SimpleNamespace(is_alive=lambda:True)
        app=SimpleNamespace(recorder=SimpleNamespace(run_id="current",emit=Mock()),closing=False,
                            paused=False,reason="Running",audio=SimpleNamespace(toggle_mute=Mock(return_value=True)))
        bridge.commands.put({"id":"mute","run_id":"current","command":"mute"})
        bridge.poll(app)
        self.assertEqual(bridge.ack,{"id":"mute","accepted":True,"reason":"Game audio muted"})
        self.assertFalse(app.paused)


if __name__ == "__main__":
    unittest.main()
