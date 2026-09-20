import unittest
from controller.audio import PcmBuffer


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.buffer = PcmBuffer(1000)
        self.buffer.set_enabled(True)

    def test_order_and_partial_chunks(self):
        self.buffer.push(b"a" * 100)
        self.buffer.push(b"b" * 100)
        out = bytearray(160)
        self.assertEqual(self.buffer.fill(out), 160)
        self.assertEqual(out, b"a" * 100 + b"b" * 60)
        tail = bytearray(80)
        self.assertEqual(self.buffer.fill(tail), 40)
        self.assertEqual(tail, b"b" * 40 + b"\0" * 40)

    def test_pause_and_resume_discard_old_audio(self):
        self.buffer.push(b"a" * 200)
        self.buffer.set_enabled(False)
        self.buffer.push(b"b" * 200)
        out = bytearray(200)
        self.assertEqual(self.buffer.fill(out), 0)
        self.buffer.set_enabled(True)
        self.assertEqual(self.buffer.fill(out), 0)
        self.buffer.push(b"c" * 200)
        self.assertEqual(self.buffer.fill(out), 200)
        self.assertEqual(out, b"c" * 200)

    def test_buffer_is_bounded_and_keeps_recent_samples(self):
        self.buffer.push(b"a" * 400)
        self.buffer.push(b"b" * 400)
        self.assertLessEqual(self.buffer.size, self.buffer.capacity)
        out = bytearray(400)
        self.buffer.fill(out)
        self.assertEqual(out, b"b" * 400)
        self.assertEqual(self.buffer.dropped_bytes, 400)

    def test_short_start_waits_for_prefill(self):
        self.buffer.push(b"a" * 80)
        out = bytearray(80)
        self.assertEqual(self.buffer.fill(out), 0)
        self.buffer.push(b"b" * 80)
        self.assertEqual(self.buffer.fill(out), 80)
        self.assertEqual(out, b"a" * 80)

    def test_callback_does_not_wait_for_producer(self):
        self.buffer.push(b"a" * 200)
        with self.buffer.lock:
            out = bytearray(b"x" * 80)
            self.assertEqual(self.buffer.fill(out), 0)
            self.assertEqual(out, bytes(80))
