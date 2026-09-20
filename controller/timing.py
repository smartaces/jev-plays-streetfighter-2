class FrameClock:
    def __init__(self, fps):
        self.fps = fps
        self.last = None
        self.debt = 0.0

    def reset(self, now):
        self.last = now
        self.debt = 0.0

    def advance(self, now):
        if self.last is None:
            self.reset(now)
            return 0, 0.0, False
        elapsed = max(0, now - self.last)
        self.last = now
        if elapsed > 0.25:
            self.debt = 0.0
            return 0, elapsed, True
        self.debt += elapsed * self.fps
        due = int(self.debt + 1e-8)
        self.debt -= due
        return min(5, due), elapsed, due > 5

