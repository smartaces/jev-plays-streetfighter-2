"""Bounded stereo PCM playback through the Mac's default output device."""
from collections import deque
from threading import Lock


class PcmBuffer:
    def __init__(self, sample_rate, channels=2):
        self.frame_bytes = channels * 2
        self.capacity = int(sample_rate * .12) * self.frame_bytes
        self.prefill = int(sample_rate * .035) * self.frame_bytes
        self.lock = Lock()
        self.chunks = deque()
        self.size = 0
        self.ready = False
        self.enabled = False
        self.dropped_bytes = 0
        self.starvations = 0

    def set_enabled(self, enabled):
        with self.lock:
            self.enabled = enabled
            self.chunks.clear()
            self.size = 0
            self.ready = False

    def push(self, pcm):
        if len(pcm) % self.frame_bytes:
            raise ValueError("Audio must contain complete stereo PCM frames.")
        with self.lock:
            if not self.enabled:
                return
            if len(pcm) > self.capacity:
                self.dropped_bytes += len(pcm) - self.capacity
                pcm = pcm[-self.capacity:]
            while self.chunks and self.size + len(pcm) > self.capacity:
                old = self.chunks.popleft()
                self.size -= len(old)
                self.dropped_bytes += len(old)
            self.chunks.append(pcm)
            self.size += len(pcm)

    def fill(self, output):
        target = memoryview(output).cast("B")
        target[:] = b"\0" * len(target)
        # Never make the audio device wait for the emulator thread.
        if not self.lock.acquire(blocking=False):
            return 0
        try:
            if not self.enabled:
                return 0
            if not self.ready:
                if self.size < self.prefill:
                    return 0
                self.ready = True
            written = 0
            while self.chunks and written < len(target):
                chunk = self.chunks.popleft()
                count = min(len(chunk), len(target) - written)
                target[written:written + count] = chunk[:count]
                written += count
                self.size -= count
                if count < len(chunk):
                    self.chunks.appendleft(chunk[count:])
            if written < len(target):
                self.starvations += 1
                self.ready = False
            return written
        finally:
            self.lock.release()


class AudioOutput:
    def __init__(self, sample_rate, volume=.7):
        import sounddevice as sd
        self.sample_rate = sample_rate
        self.volume = volume
        self.buffer = PcmBuffer(sample_rate)
        self.muted = False
        self.playing = False
        self.closed = False
        self.callbacks = 0
        self.output_bytes = 0
        self.underflows = 0
        self.device = sd.query_devices(kind="output")["name"]
        self.stream = sd.RawOutputStream(samplerate=sample_rate, channels=2, dtype="int16",
                                         blocksize=512, latency=.04, callback=self._callback)
        try:
            self.stream.start()
        except Exception:
            self.stream.close()
            raise

    def _callback(self, output, frames, timing, status):
        self.callbacks += 1
        if status.output_underflow:
            self.underflows += 1
        self.output_bytes += self.buffer.fill(output)

    def set_playing(self, playing):
        self.playing = playing
        self.buffer.set_enabled(playing and not self.muted)

    def toggle_mute(self):
        self.muted = not self.muted
        self.buffer.set_enabled(self.playing and not self.muted)
        return self.muted

    def push(self, samples):
        if self.playing and not self.muted:
            if samples.ndim != 2 or samples.shape[1] != 2 or str(samples.dtype) != "int16":
                raise ValueError("Unexpected emulator audio format.")
            pcm = (samples * self.volume).astype("int16").tobytes()
            self.buffer.push(pcm)

    def stats(self):
        return {"enabled": True, "device": self.device, "sample_rate": self.sample_rate,
                "callbacks": self.callbacks, "pcm_frames_output": self.output_bytes // 4,
                "device_underflows": self.underflows, "buffer_starvations": self.buffer.starvations,
                "dropped_pcm_frames": self.buffer.dropped_bytes // 4, "muted": self.muted}

    def close(self):
        if self.closed:
            return
        self.set_playing(False)
        try:
            self.stream.abort()
        finally:
            self.stream.close()
            self.closed = True
