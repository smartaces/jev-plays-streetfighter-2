from collections import Counter
from statistics import median
import signal
import time

from .actions import ActionExecutor
from .coaching import Coaching
from .config import RULESET
from .contracts import ActionId, Snapshot, RequestJob, rejection_reason
from .context import decision_context
from .game import GameSession
from .jev import Worker
from .observation import ObservationBuilder, InvalidState, action_state_rejection
from .recording import Recorder, distribution
from .rounds import RoundFlow
from .timing import FrameClock


class Application:
    def __init__(self, settings, mode, *, mock_delay=None, headless=False, autostart=False, exit_after=None,
                 dashboard=False, open_browser=True, dashboard_only=False):
        self.settings, self.mode = settings, mode
        self.headless, self.autostart, self.exit_after = headless, autostart, exit_after
        self.dashboard_enabled, self.open_browser = dashboard or dashboard_only, open_browser
        self.dashboard_only = dashboard_only
        self.dashboard = None
        self.execution_request_id = None
        self.game = GameSession(settings)
        self.observer = ObservationBuilder(settings.fps)
        self.coach = Coaching(settings.fps)
        self.actions = ActionExecutor(self.game.buttons, settings)
        self.clock = FrameClock(settings.fps)
        self.episode = self.epoch = 0
        self.observed_at = time.monotonic()
        initial = self.game.reset()
        self.latest_game_info = initial
        self.observer.update(initial, self.observed_at)
        self.coach.observe(initial, self.game.frame, self.actions.execution_state())
        self.rounds = RoundFlow(initial, settings.fps)
        self.recorder = Recorder(settings, mode)
        self.worker = Worker(settings, mock_delay if mode == "mock" else None) if mode != "inspect" else None
        self.worker_ready = False
        self.worker_started = time.monotonic()
        self.outstanding = None
        self.outstanding_at = 0.0
        self.next_eligible = 0.0
        self.pending = None
        self.local_action = None
        self.raw_pulse = None
        self.held = set()
        self.previous_buttons = tuple(self.game.zero)
        self.paused = True
        self.reason = "Press Resume in the dashboard to start" if dashboard_only else "Press Space to start"
        self.fault = None
        self.terminal = False
        self.budget_finished = False
        self.closing = False
        self.finished = False
        self.close_at = None
        self.writer_finishing = False
        self.want_reset = False
        self.active_seconds = 0.0
        self.total_frames = 0
        self.jobs = 0
        self.counts = Counter()
        self.counts["rounds_started"] = 1
        self.latencies = []
        self.ages = []
        self.input_tokens = self.output_tokens = 0
        self.unknown_usage = 0
        self.latest_latency = None
        self.viewer = None
        self.audio = None
        self.audio_error = None
        self.last_status = 0.0
        self.last_status_text = None
        self.started_at = time.monotonic()
        self.signal_installed = False
        self.recorder.emit("initial_state", episode=self.episode, epoch=self.epoch,
                           state=self.observer.latest, preparation_frames=settings.settle_frames + 1)

    def clear_controls(self):
        self.epoch += 1
        self.pending = self.local_action = self.raw_pulse = None
        self.held.clear()
        self.actions.clear()
        self.observer.clear()
        self.game.release()
        if self.audio:
            self.audio.set_playing(False)
        self.previous_buttons = tuple(self.game.zero)
        self.recorder.emit("inputs_released", epoch=self.epoch, episode=self.episode, frame=self.game.frame,
                           request_id=self.execution_request_id)
        self.execution_request_id = None

    def pause(self, reason):
        self.paused = True
        self.reason = reason
        self.clear_controls()
        self.clock.reset(time.monotonic())
        self.recorder.emit("paused", reason=reason, epoch=self.epoch)

    def resume(self):
        if not self.paused:
            return
        if self.fault:
            self.reason = self.fault + " Fix the problem and relaunch."
            print(self.reason, flush=True)
            return
        if self.terminal:
            self.reason = "Round finished — press R to reset"
            return
        if self.budget_finished:
            self.reason = ("Session complete — use Stop in the dashboard to finish the log" if self.dashboard_only else
                           "Session complete — close this window to finish the log")
            return
        if self.worker is not None and not self.worker_ready:
            self.reason = "Starting decision worker…"
            return
        self.paused = False
        self.reason = "Running" if self.rounds.fighting else "Between rounds — waiting for the next fight"
        if self.audio:
            self.audio.set_playing(True)
        self.clock.reset(time.monotonic())
        self.recorder.emit("resumed", epoch=self.epoch)

    def reset(self):
        self.pause("Reset — press Space to start")
        self.coach.reset()
        self.episode += 1
        self.actions.last_action = None
        values = self.game.reset()
        self.latest_game_info = values
        self.rounds.reset(values)
        self.counts["rounds_started"] += 1
        self.observed_at = time.monotonic()
        self.observer.update(values, self.observed_at)
        self.terminal = False
        self.recorder.emit("reset", episode=self.episode, epoch=self.epoch, state=values,
                           preparation_frames=self.settings.settle_frames + 1)

    def restart_match(self):
        self.clear_controls()
        self.coach.reset()
        self.episode += 1
        self.actions.last_action = None
        values = self.game.retry_match()
        self.latest_game_info = values
        self.rounds.reset(values)
        self.observed_at = time.monotonic()
        self.observer.update(values, self.observed_at)
        self.counts["rematches"] += 1
        self.counts["rounds_started"] += 1
        self.reason = "Running"
        self.clock.reset(self.observed_at)
        if self.audio:
            self.audio.set_playing(True)
        self.recorder.emit("match_restarted", episode=self.episode, epoch=self.epoch,
                           state=values, source="current_opponent_checkpoint")

    def update_round(self, info, terminal):
        for event in self.rounds.advance(info, terminal):
            name = event.pop("event")
            self.recorder.emit(name, episode=self.episode, frame=self.game.frame, **event)
            if name == "round_finished":
                self.coach.reset()
                self.counts["rounds_finished"] += 1
                self.clear_controls()
                self.actions.last_action = None
                if not self.settings.continue_rounds:
                    self.terminal = True
                    self.pause("Round finished — press R to reset")
                    return False
                self.reason = "Between rounds — waiting for the next fight"
                if self.audio:
                    self.audio.set_playing(True)
            elif name == "match_finished":
                self.counts["matches_finished"] += 1
                self.reason = ("Match won — advancing to the next opponent" if event["won"] else
                               "Match lost — retrying this opponent shortly")
            elif name == "match_started":
                self.game.save_checkpoint()
                self.counts["opponents_advanced"] += 1
            elif name == "round_started":
                self.coach.reset()
                self.clear_controls()
                self.episode += 1
                self.counts["rounds_started"] += 1
                self.actions.last_action = None
                self.reason = "Running"
                if self.audio:
                    self.audio.set_playing(True)
            elif name == "rematch":
                self.restart_match()
                return True
            elif name == "transition_paused":
                self.terminal = True
                self.pause(event["reason"] + " — R restarts the tournament")
                return False
        return False

    def finish_trial_if_needed(self):
        finished_requests = (self.jobs >= self.settings.max_requests and self.outstanding is None
                             and self.pending is None and not self.actions.remaining)
        if self.mode != "inspect" and (self.active_seconds >= self.settings.duration or finished_requests):
            self.budget_finished = True
            self.pause("Session complete — use Stop in the dashboard to finish" if self.dashboard_only else
                       "Session complete — close the window to finish")
            self.recorder.emit("trial_summary", summary=self.summary())
            print(f"Session complete: {self.counts['applied']} actions applied.", flush=True)
            return True
        return False

    def fail(self, reason):
        if self.fault is None:
            self.fault = reason
            self.pause(reason)
            if self.worker:
                self.worker.stop()
            print(reason + " Fix the problem and relaunch.", flush=True)

    def begin_close(self):
        if not self.closing:
            self.closing = True
            self.close_at = time.monotonic()
            self.pause("Closing…")
            if self.worker:
                self.worker.stop()

    def accept_results(self, now):
        if self.worker is None:
            return
        for result in self.worker.drain():
            if isinstance(result, dict):
                if result["kind"] == "ready":
                    self.worker_ready = True
                else:
                    self.fail(result["error"])
                continue
            self.recorder.emit("result", result=result)
            self.counts[result.outcome] += 1
            if result.attempted:
                self.counts["inference_attempts"] += 1
                self.latest_latency = result.received - result.started
                if result.outcome == "ok":
                    self.latencies.append(self.latest_latency)
                if result.input_tokens is None:
                    self.unknown_usage += 1
                else:
                    self.input_tokens += result.input_tokens
                if result.output_tokens is not None:
                    self.output_tokens += result.output_tokens
            if result.request_id != self.outstanding:
                self.discard(result, "unknown_request")
                continue
            self.outstanding = None
            self.next_eligible = result.next_eligible
            if result.fatal:
                self.fail(result.error or "Decision worker suspended.")
            reason = rejection_reason(result, self.recorder.run_id, self.episode, self.epoch,
                                      now, self.settings.max_age)
            if reason is None and (self.paused or self.closing):
                reason = "inactive"
            if reason:
                self.discard(result, reason)
            else:
                if self.pending is not None:
                    self.discard(self.pending, "replaced")
                self.pending = result

    def discard(self, result, reason, **details):
        self.counts["discarded"] += 1
        self.recorder.emit("discarded", request_id=result.request_id, reason=reason, **details)

    def apply_pending(self, now):
        if self.pending is None or self.actions.locked or not self.rounds.fighting:
            return
        result, self.pending = self.pending, None
        reason = rejection_reason(result, self.recorder.run_id, self.episode, self.epoch,
                                  now, self.settings.max_age)
        if reason:
            self.discard(result, reason)
            return
        if self.mode == "play":
            reason = action_state_rejection(result.action, self.observer.latest)
            if reason:
                # A rejected attack is not held through recovery or replaced by
                # a controller-selected move. Stop an old uncommitted walk and
                # let normal dispatch obtain a fresh decision at the usual rate.
                if self.actions.current == "approach":
                    self.actions.clear()
                latest = self.observer.latest
                self.discard(result, reason, frame=self.game.frame,
                             player_status=latest["player_status"],
                             distance=abs(latest["opponent_x"] - latest["player_x"]))
                return
        if not self.actions.accept(result.action, self.observer.latest, result.strength):
            self.discard(result, "unknown_direction")
            return
        self.counts["applied"] += 1
        self.coach.applied(result.action, self.game.frame)
        self.execution_request_id = result.request_id
        age = now - result.snapshot.captured
        self.ages.append(age)
        self.recorder.emit("applied", request_id=result.request_id, frame=self.game.frame,
                           action=result.action, strength=self.actions.strength,
                           age_ms=round(age * 1000, 2), source=self.mode)

    def next_buttons(self):
        if not self.rounds.fighting:
            return self.game.zero
        if self.mode == "inspect":
            if self.local_action is not None and not self.actions.locked:
                self.actions.accept(self.local_action, self.observer.latest)
                self.recorder.emit("manual_action", action=self.local_action, frame=self.game.frame)
                self.local_action = None
            if self.raw_pulse is not None:
                button, remaining = self.raw_pulse
                self.raw_pulse = (button, remaining - 1) if remaining > 1 else None
                return self.actions.vector((button,))
            if self.held:
                names = self.held.copy()
                for pair in ({"UP", "DOWN"}, {"LEFT", "RIGHT"}):
                    if pair <= names:
                        names -= pair
                return self.actions.vector(names)
        return self.actions.next_buttons(self.observer.latest)

    def dispatch(self, now):
        if (self.paused or not self.rounds.fighting or self.actions.locked or self.pending is not None or self.worker is None or
            not self.worker_ready or self.outstanding is not None or
            now < self.next_eligible or self.jobs >= self.settings.max_requests or
            now - self.observed_at > self.settings.queue_age):
            return
        observation = self.observer.model_state(
                                self.actions.current, self.actions.remaining, self.actions.last_action,
                                round(median(self.latencies[-12:]) * self.settings.fps) if self.latencies else None,
                                execution=self.actions.execution_state())
        context = decision_context(observation)
        if self.settings.coaching:
            context = self.coach.context(context, self.latest_game_info, RULESET)
        snapshot = Snapshot(self.recorder.run_id, self.episode, self.epoch, self.game.frame,
                            self.observed_at, context, observation=observation)
        job = RequestJob(self.jobs + 1, snapshot)
        if self.worker.send(job):
            self.jobs += 1
            self.outstanding = job.request_id
            self.outstanding_at = now
            self.recorder.emit("request", job=job)

    def callback(self, _dt=0):
        try:
            self._callback()
        except Exception as exc:
            # Avoid crashing out of the Cocoa loop before the child and logs are closed.
            self.fail(f"Controller stopped ({type(exc).__name__}: {exc}).")
            self.begin_close()

    def dashboard_call(self, method, **kwargs):
        if self.dashboard is None:
            return
        try:
            getattr(self.dashboard, method)(self, **kwargs)
        except Exception as exc:
            print(f"Dashboard disconnected ({type(exc).__name__}); game controls and logging remain available.", flush=True)
            self.recorder.listener = None
            self.dashboard_enabled = False
            try:
                self.dashboard.close()
            except Exception:
                pass
            self.dashboard = None
            if self.dashboard_only:
                self.fail("The dashboard disconnected. Relaunch or use Play.command for the native window.")
                self.begin_close()

    def _callback(self):
        now = time.monotonic()
        self.dashboard_call("poll")
        if not self.signal_installed:
            signal.signal(signal.SIGINT, lambda *_: self.begin_close())
            signal.signal(signal.SIGTERM, lambda *_: self.begin_close())
            self.signal_installed = True
        self.accept_results(now)
        if self.exit_after is not None and now - self.started_at >= self.exit_after:
            self.begin_close()
        if self.closing:
            self.dashboard_call("publish_video")
            self.dashboard_call("publish")
            self.shutdown_step(now)
            return
        if self.recorder.error:
            self.fail(self.recorder.error)
        if self.worker is not None and not self.fault:
            if not self.worker.process.is_alive():
                self.fail("The decision worker exited unexpectedly.")
            elif not self.worker_ready and now - self.worker_started > 10:
                self.fail("The decision worker could not start.")
            elif self.outstanding is not None and now - self.outstanding_at > self.settings.watchdog:
                self.fail("The decision worker stopped responding.")
        if self.want_reset:
            self.want_reset = False
            self.reset()
        if self.autostart and (self.worker is None or self.worker_ready):
            self.autostart = False
            self.resume()
        if not self.paused:
            due, elapsed, stalled = self.clock.advance(now)
            self.active_seconds += elapsed
            if self.finish_trial_if_needed():
                due = 0
            if stalled and not self.paused:
                self.counts["clock_stalls"] += 1
                self.clear_controls()
                if self.audio:
                    self.audio.set_playing(True)
                self.recorder.emit("clock_stall", elapsed=elapsed)
            for _ in range(due):
                frame_now = time.monotonic()
                self.apply_pending(frame_now)
                buttons = self.next_buttons()
                while self.actions.events:
                    self.recorder.emit("controller_execution", frame=self.game.frame,
                                       episode=self.episode, request_id=self.execution_request_id,
                                       **self.actions.events.popleft())
                if tuple(buttons) != self.previous_buttons:
                    self.recorder.emit("buttons", frame=self.game.frame, episode=self.episode,
                                       request_id=self.execution_request_id,
                                       held=[b for b, pressed in zip(self.game.buttons, buttons) if pressed])
                    self.previous_buttons = tuple(buttons)
                info, terminal = self.game.step(buttons)
                self.latest_game_info = info
                if self.audio:
                    self.audio.push(self.game.audio_samples())
                self.total_frames += 1
                self.observed_at = time.monotonic()
                restarted = self.update_round(info, terminal)
                if restarted:
                    break
                if self.rounds.fighting:
                    self.observer.update(info, self.observed_at)
                    self.coach.observe(info, self.game.frame, self.actions.execution_state())
                if self.total_frames % 6 == 0:
                    self.recorder.emit("state", episode=self.episode, frame=self.game.frame,
                                       fighting=self.rounds.fighting, state=info)
                if self.paused:
                    break
            if not self.paused:
                if not self.finish_trial_if_needed():
                    self.dispatch(time.monotonic())
        else:
            self.clock.reset(now)
        self.dashboard_call("publish_video")
        self.dashboard_call("publish")
        if self.viewer is not None:
            self.viewer.set_frame(self.game.image)
        if now - self.last_status >= 1:
            self.last_status = now
            latency = f"{self.latest_latency * 1000:.0f} ms" if self.latest_latency is not None else "waiting"
            action_status = f"last: {self.actions.last_action or 'waiting'}"
            status = (f"Jev SF2 | {self.mode.upper()} | {self.reason if self.paused or not self.rounds.fighting else action_status}"
                      f" | {self.active_seconds:.0f}s | {latency}")
            if self.mode == "inspect":
                v = self.observer.latest
                status += f" | HP {v['health']}/{v['enemy_health']} | x {v['player_x']}/{v['opponent_x']}"
            if self.viewer is not None:
                self.viewer.window.set_caption(status)
            if status != self.last_status_text:
                print(status, flush=True)
                self.last_status_text = status

    def summary(self):
        return {"complete": self.fault is None, "mode": self.mode, "active_seconds": round(self.active_seconds, 3),
                "frames": self.total_frames,
                "game_speed_ratio": round(self.total_frames / self.settings.fps / self.active_seconds, 4) if self.active_seconds else None,
                "jobs_dispatched": self.jobs, "counts": dict(self.counts),
                "request_latency": distribution(self.latencies), "decision_age": distribution(self.ages),
                "reported_input_tokens": self.input_tokens, "reported_output_tokens": self.output_tokens,
                "attempts_without_usage": self.unknown_usage,
                "requests_without_outcome": int(self.outstanding is not None), "fault": self.fault,
                "audio": self.audio.stats() if self.audio else {"enabled": False, "error": self.audio_error}}

    def shutdown_step(self, now):
        if self.worker is not None:
            force = now - self.close_at > 2
            if force and self.worker.process.is_alive():
                self.fault = self.fault or "The worker required forced shutdown."
            if now - self.close_at > 3 and self.worker.process.is_alive():
                self.worker.process.kill()
            if not self.worker.process.is_alive():
                self.accept_results(now)
            if not self.worker.close(force=force):
                return
            self.worker = None
        if not self.writer_finishing:
            if self.audio:
                self.audio.close()
            self.recorder.emit("closed", epoch=self.epoch)
            self.recorder.finish(self.summary())
            self.writer_finishing = True
        if self.recorder.thread.is_alive() and now - self.close_at < 5:
            return
        if self.recorder.thread.is_alive() or self.recorder.error:
            print("The run log may be incomplete.", flush=True)
        self.game.close()
        self.finished = True
        self.dashboard_call("publish", force=True)
        if self.dashboard:
            self.dashboard.close()
            self.dashboard = None
        print(f"Run records: {self.recorder.path}", flush=True)
        if self.viewer is not None:
            import pyglet
            pyglet.clock.unschedule(self.callback)
            self.viewer.close()
            # On macOS, closing the last window may terminate the native application.

    def start_audio(self):
        if self.settings.audio and self.audio is None:
            try:
                from .audio import AudioOutput
                self.audio = AudioOutput(self.game.audio_rate, self.settings.volume)
                print(f"Game audio: {self.audio.device} · mute from dashboard or M in the game window", flush=True)
            except Exception as exc:
                self.audio_error = f"Audio output unavailable ({type(exc).__name__}: {exc})"
                print(self.audio_error + ". The game can continue silently.", flush=True)

    def attach_window(self):
        import pyglet
        from pyglet.window import key
        from .display import GameDisplay
        self.viewer = GameDisplay(self.game.image)
        self.start_audio()
        directions = {key.UP: "UP", key.DOWN: "DOWN", key.LEFT: "LEFT", key.RIGHT: "RIGHT",
                      key.Z: self.settings.punch, key.X: self.settings.kick}
        action_keys = dict(zip((key._1, key._2, key._3, key._4, key._5, key._6), ActionId))
        action_keys.update({key._7: ActionId.BLOCK, key._8: ActionId.HEAVY_PUNCH,
                           key._9: ActionId.SWEEP, key.F: ActionId.FIREBALL,
                           key.D: ActionId.DRAGON, key.H: ActionId.HURRICANE})
        action_keys.update({key.J: ActionId.JUMP_FORWARD_KICK, key.K: ActionId.JUMP_FORWARD_PUNCH,
                           key.V: ActionId.JUMP_UP, key.N: ActionId.JUMP_BACK,
                           key.T: ActionId.THROW_FORWARD, key.B: ActionId.THROW_BACK})
        raw_keys = dict(zip((key.F1, key.F2, key.F3, key.F4, key.F5, key.F6), "ABCXYZ"))

        def on_key_press(symbol, modifiers):
            if symbol == key.ESCAPE or (symbol == key.Q and modifiers & key.MOD_COMMAND):
                self.begin_close()
            elif symbol == key.SPACE:
                self.resume() if self.paused else self.pause("Paused — Space to resume")
            elif symbol == key.R:
                self.want_reset = True
            elif symbol == key.M and self.audio:
                muted = self.audio.toggle_mute()
                print("Game audio muted" if muted else "Game audio on", flush=True)
            elif self.mode == "inspect":
                if symbol in action_keys:
                    self.local_action = action_keys[symbol]
                    self.resume()
                elif symbol in raw_keys:
                    self.raw_pulse = (raw_keys[symbol], self.settings.pulse_frames)
                    self.resume()
                elif symbol in directions:
                    self.held.add(directions[symbol])
                    self.resume()
            return pyglet.event.EVENT_HANDLED

        def on_key_release(symbol, modifiers):
            if symbol in directions:
                self.held.discard(directions[symbol])
            return pyglet.event.EVENT_HANDLED

        def on_close():
            self.begin_close()
            return pyglet.event.EVENT_HANDLED

        def on_deactivate():
            if not self.paused and not self.dashboard_enabled:
                self.pause("Window inactive — Space to resume")

        self.viewer.window.push_handlers(on_key_press=on_key_press, on_key_release=on_key_release,
                                        on_close=on_close, on_deactivate=on_deactivate)

    def run(self):
        if self.dashboard_enabled:
            from .dashboard import DashboardBridge
            try:
                self.dashboard = DashboardBridge(self.recorder.path.parent, self.open_browser)
                self.recorder.listener = self.dashboard.record
                self.recorder.emit("dashboard_enabled", pause_on_focus_loss=False)
                print("Dashboard mode: game continues when you use the browser. Use its Pause control.", flush=True)
            except Exception as exc:
                self.dashboard_enabled = False
                print(f"Dashboard could not start ({type(exc).__name__}); normal window focus rules apply.", flush=True)
                if self.dashboard_only:
                    self.fail("The browser dashboard could not start. Use Play.command for the native window.")
                    self.begin_close()
        if self.dashboard_only:
            self.start_audio()
            print("Use dashboard Resume, Pause, Mute and Stop. Ctrl-C in this terminal also stops the session.", flush=True)
        else:
            print("Space: start/pause · R: reset · M: mute/unmute · Escape: quit", flush=True)
        if self.settings.continue_rounds:
            print("Automatic next rounds enabled. Wins advance; losses retry the current opponent.", flush=True)
        if self.mode != "inspect":
            print(f"Session limits: {self.settings.duration:g} active seconds / {self.settings.max_requests} requests.", flush=True)
        if self.mode == "inspect":
            print("Arrows: move · Z/X: punch/kick · 1–6: original actions · 7: low block · 8: heavy punch · 9: sweep · F/D/H: fireball/dragon/hurricane · F1–F6: raw buttons", flush=True)
        print(f"Run records: {self.recorder.path}", flush=True)
        if self.headless or self.dashboard_only:
            try:
                while not self.finished:
                    self.callback()
                    time.sleep(0.002)
            except KeyboardInterrupt:
                self.begin_close()
                while not self.finished:
                    self.callback()
                    time.sleep(0.01)
        else:
            import pyglet
            try:
                self.attach_window()
            except Exception:
                # No native loop exists yet; clean up without trying to render again.
                self.viewer = None
                self.fail("The game window could not open. Run this from a logged-in Mac desktop.")
                self.begin_close()
                while not self.finished:
                    self.accept_results(time.monotonic())
                    self.shutdown_step(time.monotonic())
                    time.sleep(0.01)
                return
            pyglet.clock.schedule_interval(self.callback, 1 / self.settings.fps)
            pyglet.app.run()
