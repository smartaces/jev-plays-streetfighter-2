from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from queue import Empty, Full, Queue
import threading
import time
from uuid import uuid4

from .config import ROOT, GAME, STATE, ROM_SHA1
from .jev import question_spec, questions_spec, QUESTION_VERSION
from .guide import GUIDE_VERSION
from .observation import OBSERVATION_VERSION
from .context import CONTEXT_VERSION
from .coaching import COACHING_VERSION
from .engagement import ENGAGEMENT_VERSION
from .characters import PROFILE_VERSION
from .config import RULESET


class Recorder:
    def __init__(self, settings, mode):
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
        self.path = ROOT / "runs" / self.run_id
        self.path.mkdir(parents=True)
        self.manifest = {"run_id": self.run_id, "complete": False, "mode": mode, "game": GAME,
                         "state": STATE, "rom_sha1": ROM_SHA1, "settings": settings.public(),
                         "question_version": QUESTION_VERSION, "question": question_spec(settings),
                         "questions": questions_spec(settings),
                         "guide_version": GUIDE_VERSION,
                         "observation_version": OBSERVATION_VERSION,
                         "context_version": CONTEXT_VERSION,
                         "coaching_version": COACHING_VERSION if settings.coaching else None,
                         "engagement_version": ENGAGEMENT_VERSION,
                         "profile_version": PROFILE_VERSION if settings.coaching else None,
                         "ruleset": RULESET,
                         "dashboard_version": "ringside-video-v3-coaching",
                         "packages": {p: version(p) for p in ("stable-retro", "typesafe-sdk", "python-dotenv")},
                         "files": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                   for name in ("config/game-data.json", "config/scenario.json", "config/ryu-fighting-guide.md",
                                                "controller/observation.py", "controller/context.py", "controller/actions.py", "controller/coaching.py", "controller/engagement.py",
                                                "controller/app.py", "controller/rounds.py", "controller/game.py",
                                                "controller/characters.py", "controller/jev.py", "controller/guide.py",
                                                "controller/contracts.py", "controller/report.py", "controller/dashboard.py", "controller/video.py",
                                                "controller/dashboard_assets/index.html", "controller/dashboard_assets/app.js",
                                                "controller/dashboard_assets/style.css", "controller/dashboard_assets/video.js",
                                                "controller/dashboard_assets/video.css")}}
        validation = ROOT / "config/validation.json"
        self.manifest["validation"] = json.loads(validation.read_text()) if validation.exists() else None
        self._write_json("manifest.json", self.manifest)
        self.queue = Queue(maxsize=2048)
        self.stopping = threading.Event()
        self.error = None
        self.lost_events = 0
        self.summary = None
        self.thread = threading.Thread(target=self._write_loop, name="sf2-log-writer", daemon=True)
        self.thread.start()

    def _write_json(self, name, value):
        tmp = self.path / (name + ".tmp")
        tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
        tmp.replace(self.path / name)

    def emit(self, event, **values):
        if self.error is not None:
            return False
        record = {"event": event, "time": time.monotonic(), **values}
        if getattr(self, "listener", None):
            try:
                self.listener(event, record)
            except Exception as exc:
                self.listener = None
                print(f"Dashboard event view disabled ({type(exc).__name__}); audit logging continues.", flush=True)
        try:
            self.queue.put_nowait(record)
            return True
        except Full:
            self.lost_events += 1
            self.error = "The run log queue filled up."
            return False

    def _write_loop(self):
        try:
            with (self.path / "events.jsonl").open("w", buffering=65536) as stream:
                last_flush = time.monotonic()
                while True:
                    try:
                        record = self.queue.get(timeout=0.1)
                    except Empty:
                        if self.stopping.is_set():
                            break
                    else:
                        stream.write(json.dumps(record, default=lambda v: asdict(v) if is_dataclass(v) else str(v),
                                                allow_nan=False) + "\n")
                    if time.monotonic() - last_flush >= 0.5:
                        stream.flush()
                        last_flush = time.monotonic()
                stream.flush()
            if self.summary is not None:
                self.summary["lost_log_events"] = self.lost_events
                self.summary["complete"] = self.error is None and self.summary.get("complete", True)
                self._write_json("summary.json", self.summary)
                self.manifest["complete"] = self.summary["complete"]
                self._write_json("manifest.json", self.manifest)
        except Exception as exc:
            self.error = f"Could not write run records ({type(exc).__name__})."
        # Reporting is optional and runs after records close, outside the frame loop.
        # A reporting problem must not invalidate saved gameplay logs.
        if self.error is None and self.summary is not None:
            try:
                from .report import write_report
                write_report(self.path)
            except Exception as exc:
                print(f"Decision report unavailable ({type(exc).__name__}); raw run logs are saved.", flush=True)

    def finish(self, summary):
        self.summary = summary
        self.stopping.set()


def distribution(samples):
    if not samples:
        return {"count": 0, "median_ms": None, "p95_ms": None}
    from statistics import median
    import math
    ordered = sorted(samples)
    return {"count": len(samples), "median_ms": round(median(samples) * 1000, 2),
            "p95_ms": round(ordered[math.ceil(len(ordered) * .95) - 1] * 1000, 2)}
