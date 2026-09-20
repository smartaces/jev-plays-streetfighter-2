"""Loopback-only dashboard. Replay never imports the emulator or calls a model."""
import argparse
from collections import deque
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import multiprocessing as mp
from pathlib import Path
from queue import Empty, Full
import secrets
import threading
import time
from urllib.parse import urlsplit, parse_qs
import webbrowser

from .config import ROOT
from .characters import character_name
from .video import DISPLAY_FPS, frame_packet, encode_png

STATIC = Path(__file__).with_name("dashboard_assets")


def plain(value):
    return asdict(value) if is_dataclass(value) else value


class EventView:
    """Joins selected/applied/discarded by request ID, never by event adjacency."""
    def __init__(self, limit=None):
        self.rows = {}
        self.buttons = deque(maxlen=40)
        self.rounds = []
        self.limit = limit
        self.opponent = "Unknown"

    def record(self, kind, values):
        if kind in {"initial_state", "state", "reset", "match_restarted"}:
            self.opponent = character_name(values.get("state", {}).get("opponent_character_id"))
        if kind in {"request", "result", "applied", "discarded"}:
            payload = plain(values.get("job") if kind == "request" else values.get("result") if kind == "result" else values)
            ident = payload["request_id"]
            row = self.rows.setdefault(ident, {"id": ident, "disposition": "awaiting response"})
            if kind == "request":
                row["snapshot"] = payload["snapshot"]
            elif kind == "result":
                row["result"] = payload
                row.setdefault("snapshot", payload["snapshot"])
                row["disposition"] = "awaiting application" if payload.get("outcome", "ok") == "ok" else payload["outcome"]
            else:
                row["execution"] = dict(values)
                row["disposition"] = "applied" if kind == "applied" else "discarded: " + values["reason"]
            if self.limit:
                while len(self.rows) > self.limit:
                    del self.rows[next(iter(self.rows))]
        elif kind == "buttons":
            self.buttons.append(dict(values))
        elif kind == "inputs_released":
            self.buttons.append({**values, "held": []})
        elif kind == "round_finished":
            self.rounds.append({"opponent": self.opponent, **values})
            self.rounds = self.rounds[-200:]


def run_list(root):
    rows = []
    for path in sorted(root.glob("*/manifest.json"), reverse=True):
        try:
            m = json.loads(path.read_text())
            summary_path = path.with_name("summary.json")
            summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
            rows.append({"id": path.parent.name, "mode": m.get("mode"), "complete": m.get("complete"),
                         "version": m.get("question_version"), "summary": summary})
        except (OSError, ValueError):
            continue
    return rows


def read_replay(root, ident):
    # IDs are discovered from manifests; symlinks and traversal cannot escape runs/.
    if not ident or Path(ident).name != ident or ident in {".", ".."}:
        raise ValueError("Invalid run")
    folder = (root / ident).resolve()
    if folder.parent != root.resolve():
        raise ValueError("Invalid run")
    manifest = json.loads((folder / "manifest.json").read_text())
    view = EventView()
    frames, buttons, timeline, skipped = [], [], [], 0
    with (folder / "events.jsonl").open() as stream:
        for line in stream:
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError()
                kind = event.get("event")
                view.record(kind, event)
                if kind in {"state", "initial_state", "reset", "match_restarted"}:
                    frames.append(event)
                if kind in {"buttons", "inputs_released"}:
                    buttons.append({**event, "held": event.get("held", [])})
                if kind in {"paused", "resumed", "round_started", "round_finished", "match_finished", "controller_execution"}:
                    timeline.append(event)
            except (ValueError, KeyError, TypeError):
                skipped += 1
    summary_path = folder / "summary.json"
    return {"manifest": manifest, "rows": list(view.rows.values()), "frames": frames,
            "buttons": buttons, "rounds": view.rounds, "events": timeline, "skipped": skipped,
            "summary": json.loads(summary_path.read_text()) if summary_path.exists() else {}}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, root, commands=None, port=0):
        self.root, self.commands = Path(root), commands
        self.latest = None
        self.video = None
        self.video_error = None
        self.token = secrets.token_urlsafe(24)
        self.cache = {}
        super().__init__(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{self.server_port}"

    def replay(self, ident):
        # read_replay performs the authoritative path validation before any read.
        if Path(ident).name != ident or ident in {".", ".."}:
            raise ValueError("Invalid run")
        folder = (self.root / ident).resolve()
        if folder.parent != self.root.resolve():
            raise ValueError("Invalid run")
        stamp = tuple((folder / name).stat().st_mtime_ns for name in ("manifest.json", "events.jsonl"))
        if ident not in self.cache or self.cache[ident][0] != stamp:
            data = read_replay(self.root, ident)
            self.cache = {ident: (stamp, data)}
        return self.cache[ident][1]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def allowed(self):
        return (self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}" and
                self.headers.get("Origin", self.server.url) == self.server.url and
                self.headers.get("Sec-Fetch-Site", "same-origin") in {"same-origin", "none"})

    def send(self, status, data, kind="application/json", headers=None):
        body = json.dumps(data, allow_nan=False).encode() if kind == "application/json" else data
        self.send_response(status)
        self.send_header("Content-Type", kind + ("; charset=utf-8" if kind.startswith("text/") or kind == "application/json" else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
        for name, value in (headers or {}).items():
            self.send_header(name, str(value))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {"error": "Local same-origin access only"})
        path = urlsplit(self.path).path
        try:
            if path == "/api/live":
                return self.send(200, {"live": self.server.latest, "live_available": self.server.commands is not None,
                                       "video_error": self.server.video_error, "token": self.server.token, "now": time.time()})
            if path == "/api/video":
                video = self.server.video
                requested = parse_qs(urlsplit(self.path).query).get("run", [None])[0]
                if video is None:
                    return self.send(204, b"", "image/png")
                if requested != video["run_id"]:
                    return self.send(409, {"error": "Video belongs to a different session"})
                return self.send(200, video["png"], "image/png", {
                    "X-Run-Id": video["run_id"], "X-Game-Frame": video["frame"],
                    "X-Game-Episode": video["episode"], "X-Frame-Captured": video["captured"],
                    "X-Buttons": ",".join(video["held"]), "X-Input-Phase": video["phase"]})
            if path == "/api/runs":
                return self.send(200, run_list(self.server.root))
            if path.startswith("/api/runs/"):
                return self.send(200, self.server.replay(path[len("/api/runs/"):]))
            assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                      "/style.css": ("style.css", "text/css"), "/video.js": ("video.js", "text/javascript"),
                      "/video.css": ("video.css", "text/css")}
            if path in assets:
                name, kind = assets[path]
                return self.send(200, (STATIC / name).read_bytes(), kind)
            return self.send(404, {"error": "Not found"})
        except (OSError, ValueError, KeyError):
            return self.send(404, {"error": "Run unavailable"})

    def do_POST(self):
        if not self.allowed() or self.headers.get("X-Dashboard-Token") != self.server.token:
            return self.send(403, {"error": "Invalid command origin or token"})
        if self.path != "/api/control":
            return self.send(404, {"error": "Not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise ValueError()
            value = json.loads(self.rfile.read(length))
            live = self.server.latest
            if (not isinstance(value, dict) or value.get("command") not in {"pause", "resume", "stop", "mute"} or
                not isinstance(value.get("id"), str) or not 1 <= len(value["id"]) <= 80):
                raise ValueError()
            if (not live or live.get("finished") or not self.server.commands or
                value.get("run_id") != live["run_id"] or time.time() - live["sent_at"] > 3):
                return self.send(409, {"error": "No matching live controller"})
            self.server.commands.put_nowait(value)
            return self.send(202, {"queued": value["id"]})
        except Full:
            return self.send(429, {"error": "Controller queue busy"})
        except (ValueError, TypeError):
            return self.send(400, {"error": "Invalid command"})


def serve_process(root, updates, frames, commands, ready, stop, open_browser):
    try:
        server = DashboardServer(root, commands)
        ready.put(server.url)
        if open_browser:
            webbrowser.open(server.url)
        def pump():
            while not stop.is_set():
                try:
                    server.latest = updates.get(timeout=.2)
                except Empty:
                    pass
            while True:
                try:
                    server.latest = updates.get_nowait()
                except Empty:
                    break
            # Give the browser a final chance to receive Stop acknowledgement.
            time.sleep(.6)
            server.shutdown()
        threading.Thread(target=pump, daemon=True).start()
        def video_pump():
            while not stop.is_set():
                try:
                    packet = frames.get(timeout=.1)
                    # Only encode the newest waiting frame; never a replay backlog.
                    while True:
                        try:
                            packet = frames.get_nowait()
                        except Empty:
                            break
                    encoded = encode_png(packet)
                    server.video = {**{k: v for k, v in packet.items() if k != "rgb"}, "png": encoded}
                    server.video_error = None
                except Empty:
                    pass
                except Exception as exc:
                    server.video_error = f"Video unavailable ({type(exc).__name__})"
        threading.Thread(target=video_pump, daemon=True).start()
        server.serve_forever(poll_interval=.1)
        server.server_close()
    except Exception as exc:
        ready.put(f"Dashboard unavailable ({type(exc).__name__})")


class DashboardBridge:
    """Small independent display queue; a slow browser cannot fill the audit log."""
    def __init__(self, root, open_browser=True):
        ctx = mp.get_context("spawn")
        self.updates, self.commands, self.ready = ctx.Queue(2), ctx.Queue(8), ctx.Queue(2)
        self.frames = ctx.Queue(2)
        self.stop = ctx.Event()
        self.view = EventView(limit=12)
        self.last_sent = 0
        self.last_video_at = 0
        self.last_video_key = None
        self.ack = None
        self.process = ctx.Process(target=serve_process,
            args=(str(root), self.updates, self.frames, self.commands, self.ready, self.stop, open_browser), daemon=True)
        self.process.start()

    def record(self, kind, values):
        self.view.record(kind, values)

    def poll(self, app):
        if not self.process.is_alive():
            raise RuntimeError("Dashboard server exited")
        try:
            print("Dashboard: " + self.ready.get_nowait(), flush=True)
        except Empty:
            pass
        for _ in range(8):
            try:
                command = self.commands.get_nowait()
            except Empty:
                break
            accepted = command["run_id"] == app.recorder.run_id and not app.closing
            reason = None
            if accepted:
                if command["command"] == "pause":
                    app.autostart = False
                    app.pause("Paused from dashboard")
                elif command["command"] == "resume":
                    app.resume()
                    accepted = not app.paused
                elif command["command"] == "mute":
                    accepted = app.audio is not None
                    reason = ("Game audio muted" if app.audio.toggle_mute() else "Game audio on") if accepted else "Audio is unavailable"
                else:
                    app.begin_close()
            self.ack = {"id": command["id"], "accepted": accepted, "reason": reason or app.reason}
            app.recorder.emit("dashboard_command", command=command["command"], **self.ack)
            self.last_sent = 0

    def publish(self, app, force=False):
        now = time.monotonic()
        if not force and now - self.last_sent < .2:
            return
        self.last_sent = now
        current = (app.observer.model_state(app.actions.current, app.actions.remaining, app.actions.last_action,
                                           execution=app.actions.execution_state()) if app.rounds.fighting else app.latest_game_info)
        value = {"sent_at": time.time(), "run_id": app.recorder.run_id, "mode": app.mode,
                 "paused": app.paused, "finished": app.finished, "reason": app.reason, "fault": app.fault,
                 "can_resume": not (app.fault or app.terminal or app.budget_finished or app.closing) and
                               (app.worker is None or app.worker_ready),
                 "frame": app.game.frame, "episode": app.episode, "epoch": app.epoch,
                 "fighting": app.rounds.fighting, "observation": current,
                 "execution": app.actions.execution_state(), "ack": self.ack,
                 "held": [b for b, pressed in zip(app.game.buttons, app.previous_buttons) if pressed],
                 "rows": [dict(row) for row in self.view.rows.values()], "buttons": list(self.view.buttons), "rounds": list(self.view.rounds),
                 "summary": app.summary(), "manifest": {key: app.recorder.manifest[key] for key in
                     ("run_id", "mode", "question_version", "context_version", "questions", "settings")}}
        try:
            self.updates.put_nowait(value)
        except Full:
            # Drop display updates only; all original events remain in Recorder.
            pass

    def publish_video(self, app):
        now = time.monotonic()
        key = (app.episode, app.game.frame, app.epoch)
        if now - self.last_video_at < 1 / DISPLAY_FPS or self.last_video_key == key:
            return
        self.last_video_at = now
        packet = frame_packet(app.game.image, run_id=app.recorder.run_id, episode=app.episode,
                              frame=app.game.frame, captured=time.time(),
                              held=[b for b, pressed in zip(app.game.buttons, app.previous_buttons) if pressed],
                              phase=app.actions.execution_state()["phase"])
        if packet is None:
            return
        try:
            self.frames.put_nowait(packet)
            self.last_video_key = key
        except Full:
            pass

    def close(self):
        self.stop.set()
        self.process.join(timeout=1)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=.5)
        for channel in (self.updates, self.frames, self.commands, self.ready):
            channel.cancel_join_thread()
            channel.close()


def main():
    parser = argparse.ArgumentParser(description="Review saved SF2 runs locally. No game or model requests.")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server = DashboardServer(ROOT / "runs", port=args.port)
    print(f"Dashboard: {server.url} · Ctrl-C to close", flush=True)
    if not args.no_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
