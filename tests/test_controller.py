import asyncio
from dataclasses import replace
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx2
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from controller.actions import ActionExecutor
from controller.config import Settings, require_api_key
from controller.contracts import ActionId, Strength, Snapshot, DecisionResult, rejection_reason
from controller.jev import request_choice, classify_error
from controller.observation import ObservationBuilder, InvalidState
from controller.timing import FrameClock

BUTTONS = ['B', 'A', 'MODE', 'START', 'UP', 'DOWN', 'LEFT', 'RIGHT', 'C', 'Y', 'X', 'Z']
VALUES = {"health": 176, "enemy_health": 176, "player_x": 205, "opponent_x": 346,
          "player_character_id": 0, "opponent_character_id": 3,
          "player_y": 192, "opponent_y": 192, "continuetimer": 0,
          "round_timer": 0x9927, "player_rounds_won": 0, "opponent_rounds_won": 0,
          "player_status": 512, "opponent_status": 512, "player_move_timer": 0}
for who in ("player", "opponent"):
    VALUES.update({f"{who}_projectile_{field}": 0 for field in ("flags", "x", "y", "vx")})


def response_body(action="approach", strength="medium"):
    return {"model": "jev-1.13.0", "answers": {"next_action": {
        "type": "choice", "choice": action, "confidence": 1.0,
        "probabilities": {a.value: float(a.value == action) for a in ActionId}},
        "attack_strength": {"type": "choice", "choice": strength, "confidence": 1.0,
                            "probabilities": {a.value: float(a.value == strength) for a in Strength}}},
        "usage": {"input_tokens": 120, "output_tokens": 12}}


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.executor = ActionExecutor(BUTTONS, Settings())

    def names(self, vector):
        return {b for b, held in zip(BUTTONS, vector) if held}

    def test_movement_releases_after_twelve_frames(self):
        self.executor.accept("approach", VALUES)
        for _ in range(12):
            self.assertEqual(self.names(self.executor.next_buttons(VALUES)), {"RIGHT"})
        self.assertEqual(self.names(self.executor.next_buttons(VALUES)), set())

    def test_crossing_sides_updates_relative_movement(self):
        self.executor.accept("approach", VALUES)
        self.assertEqual(self.names(self.executor.next_buttons({**VALUES, "opponent_x": 150})), {"LEFT"})

    def test_relative_directions_and_unknown_side(self):
        left = {**VALUES, "opponent_x": 150}
        self.executor.accept("retreat", left)
        self.assertEqual(self.names(self.executor.next_buttons(left)), {"RIGHT"})
        self.assertFalse(self.executor.accept("approach", {**VALUES, "opponent_x": 205}))

    def test_punch_has_explicit_release(self):
        self.executor.accept("punch", VALUES)
        self.assertTrue(self.executor.locked)
        self.assertEqual([self.names(self.executor.next_buttons(VALUES)) for _ in range(6)],
                         [set(), set(), {"X"}, {"X"}, set(), set()])
        self.assertFalse(self.executor.locked)

    def test_crouching_kick_prepares_and_releases(self):
        self.executor.accept("crouching_kick", VALUES)
        self.assertEqual([self.names(self.executor.next_buttons(VALUES)) for _ in range(6)],
                         [{"DOWN"}, {"DOWN"}, {"DOWN", "A"}, {"DOWN", "A"}, set(), set()])

    def test_pause_clear_overrides_a_pulse(self):
        self.executor.accept("jump_forward", VALUES)
        self.executor.next_buttons(VALUES)
        self.executor.clear()
        self.assertEqual(self.names(self.executor.next_buttons(VALUES)), set())

    def test_invalid_button_combinations_rejected(self):
        for names in [("START",), ("MODE",), ("LEFT", "RIGHT"), ("UP", "DOWN")]:
            with self.assertRaises(ValueError):
                self.executor.vector(names)


class StateAndTimingTests(unittest.TestCase):
    def test_history_and_reset(self):
        builder = ObservationBuilder()
        builder.update(VALUES, 1.0)
        builder.update({**VALUES, "player_x": 220, "health": 160}, 1.2)
        state = builder.model_state("approach", 2, "punch")
        self.assertEqual(state["recent"]["distance_change"], -15)
        self.assertEqual(state["recent"]["player_health_change"], -16)
        builder.clear()
        builder.update(VALUES, 2.0)
        self.assertIsNone(builder.model_state("neutral", 0, None)["recent"])

    def test_missing_data_not_treated_as_zero(self):
        for value in [None, float("nan"), 100000, True]:
            with self.assertRaises(InvalidState):
                ObservationBuilder().update({**VALUES, "player_x": value}, 1.0)

    def test_spacing_uses_absolute_distance_on_either_side(self):
        builder = ObservationBuilder()
        for separation, expected in ((141, "outside_basic_attack_reach"),
                                     (45, "close"), (-19, "close"),
                                     (-46, "outside_basic_attack_reach")):
            builder.update({**VALUES, "opponent_x": VALUES["player_x"] + separation}, 1.0)
            state = builder.model_state("neutral", 0, "approach")
            self.assertEqual(state["spacing"], expected)
            self.assertEqual(state["distance"], abs(separation))

    def test_frame_clock_catches_up_and_discards_long_gap(self):
        clock = FrameClock(60)
        clock.reset(0)
        self.assertEqual(clock.advance(0.05)[0], 3)
        self.assertEqual(clock.advance(0.10)[0], 3)
        due, elapsed, stalled = clock.advance(1.0)
        self.assertEqual(due, 0)
        self.assertTrue(stalled)
        self.assertAlmostEqual(elapsed, 0.9)
        self.assertEqual(clock.advance(1 + 1/60)[0], 1)

    def test_epoch_and_age_checked_at_application(self):
        snap = Snapshot("run", 0, 0, 20, 10.0, {})
        result = DecisionResult(1, snap, 10.0, 10.1, 10.25, "ok", "punch")
        self.assertIsNone(rejection_reason(result, "run", 0, 0, 10.5, 0.75))
        self.assertEqual(rejection_reason(result, "run", 0, 1, 10.5, 0.75), "old_state")
        self.assertEqual(rejection_reason(result, "run", 0, 0, 10.8, 0.75), "stale")

    def test_dotenv_loading_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env").write_text("TYPESAFE_API_KEY=file-test-value\n")
            with patch("controller.config.ROOT", Path(directory)), patch.dict("os.environ", {}, clear=True):
                require_api_key()
                import os
                self.assertEqual(os.environ["TYPESAFE_API_KEY"], "file-test-value")
                os.environ["TYPESAFE_API_KEY"] = "environment-test-value"
                require_api_key()
                self.assertEqual(os.environ["TYPESAFE_API_KEY"], "environment-test-value")


class APITests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler):
        return AsyncTypeSafeClient(api_key="offline-test-key", transport=httpx2.MockTransport(handler),
                                   retry=RetryPolicy(max_retries=0))

    async def test_real_sdk_wire_format_and_response(self):
        seen = []
        def handle(request):
            seen.append(json.loads(request.content))
            self.assertEqual(request.url.path, "/v1/systemone")
            return httpx2.Response(200, json=response_body())
        async with self.client(handle) as client:
            result = await request_choice(client, {"distance": 141}, Settings())
        self.assertEqual(result["action"], "approach")
        self.assertEqual(result["strength"], "medium")
        self.assertEqual(result["input_tokens"], 120)
        self.assertEqual(seen[0]["model"], "jev-1.13.0")
        self.assertEqual(set(seen[0]["questions"]["next_action"]["criteria"]), set(ActionId))
        from controller.guide import fighting_guide
        self.assertEqual(fighting_guide(), seen[0]["questions"]["next_action"]["instructions"]["fighting_guide"])

    async def test_rate_limit_is_not_retried(self):
        calls = []
        def handle(request):
            calls.append(request)
            return httpx2.Response(429, json={"error": "busy"}, headers={"retry-after": "2"})
        async with self.client(handle) as client:
            try:
                await request_choice(client, {}, Settings())
            except Exception as exc:
                outcome, _, fatal, delay = classify_error(exc)
            else:
                self.fail("Expected rate-limit failure")
        self.assertEqual((outcome, fatal, delay), ("rate_limit", False, 2.0))
        self.assertEqual(len(calls), 1)

    async def test_total_deadline_cancels_slow_request(self):
        async def handle(request):
            await asyncio.sleep(0.2)
            return httpx2.Response(200, json=response_body())
        async with self.client(handle) as client:
            with self.assertRaises(TimeoutError):
                await request_choice(client, {}, Settings(), timeout=0.02)

    async def test_unknown_action_rejected(self):
        async with self.client(lambda _: httpx2.Response(200, json=response_body("super_fireball"))) as client:
            with self.assertRaises(ValueError):
                await request_choice(client, {}, Settings())

    async def test_missing_answer_rejected(self):
        body = response_body()
        body["answers"] = {}
        async with self.client(lambda _: httpx2.Response(200, json=body)) as client:
            with self.assertRaises(ValueError):
                await request_choice(client, {}, Settings())

    async def test_missing_strength_rejected(self):
        body = response_body()
        del body["answers"]["attack_strength"]
        async with self.client(lambda _: httpx2.Response(200, json=body)) as client:
            with self.assertRaises(ValueError):
                await request_choice(client, {}, Settings())

    async def test_invalid_strength_rejected(self):
        async with self.client(lambda _: httpx2.Response(200, json=response_body(strength="turbo"))) as client:
            with self.assertRaises(ValueError):
                await request_choice(client, {}, Settings())

    async def test_bad_key_stops_and_error_does_not_echo_body(self):
        async with self.client(lambda _: httpx2.Response(401, json={"error": "sensitive-server-content"})) as client:
            try:
                await request_choice(client, {}, Settings())
            except Exception as exc:
                _, message, fatal, _ = classify_error(exc)
        self.assertTrue(fatal)
        self.assertNotIn("sensitive-server-content", message)


class FakeRecorder:
    run_id = "offline-app-test"
    error = None
    path = Path("unused-test-records")
    def __init__(self, *args):
        self.events = []
    def emit(self, event, **data):
        self.events.append((event, data))
        return True


class FakeGame:
    buttons = BUTTONS
    zero = [0] * 12
    image = None
    def __init__(self, _settings):
        self.frame = 0
    def reset(self):
        self.frame = 0
        return VALUES.copy()
    def step(self, buttons):
        self.frame += 1
        return VALUES.copy(), False
    def release(self):
        pass
    def save_checkpoint(self):
        self.checkpoint_saved = True
    def retry_match(self):
        self.retried = True
        return self.reset()


class FakeWorker:
    def __init__(self, *args):
        self.process = SimpleNamespace(is_alive=lambda: True)
        self.sent = []
        self.incoming = [{"kind": "ready"}]
    def send(self, job):
        self.sent.append(job)
        return True
    def drain(self):
        incoming, self.incoming = self.incoming, []
        return iter(incoming)
    def stop(self):
        pass


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        patches = [patch("controller.app.GameSession", FakeGame),
                   patch("controller.app.Recorder", FakeRecorder),
                   patch("controller.app.Worker", FakeWorker),
                   patch("time.monotonic", lambda: self.now)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        from controller.app import Application
        self.app = Application(Settings(), "mock", mock_delay=.2, headless=True, autostart=True)
        self.app.signal_installed = True
        self.app._callback()

    def advance(self, seconds):
        for _ in range(round(seconds / .02)):
            self.now += .02
            self.app._callback()

    def result(self, job, action="approach"):
        return DecisionResult(job.request_id, job.snapshot, job.snapshot.captured,
                              self.now, job.snapshot.captured + .25, "ok", action=action)

    def test_waiting_for_inference_does_not_stop_game_or_queue_more_jobs(self):
        self.advance(.5)
        self.assertEqual(len(self.app.worker.sent), 1)
        self.assertGreaterEqual(self.app.total_frames, 29)

    def test_reset_discards_old_answer(self):
        job = self.app.worker.sent[0]
        self.advance(.1)
        self.app.reset()
        self.app.worker.incoming.append(self.result(job))
        self.app._callback()
        self.assertEqual(self.app.counts["applied"], 0)
        self.assertEqual(self.app.counts["discarded"], 1)
        self.assertTrue(self.app.paused)

    def test_request_rate_and_fresh_state(self):
        job = self.app.worker.sent[0]
        self.advance(.1)
        self.app.worker.incoming.append(self.result(job))
        self.advance(.1)
        self.assertEqual(len(self.app.worker.sent), 1)
        self.advance(.1)
        self.assertEqual(len(self.app.worker.sent), 2)
        self.assertGreater(self.app.worker.sent[1].snapshot.frame, job.snapshot.frame)
        self.assertGreaterEqual(self.app.worker.sent[1].snapshot.captured - job.snapshot.captured, .25)

    def test_unresponsive_worker_releases_and_pauses(self):
        self.advance(self.app.settings.watchdog + .1)
        self.assertTrue(self.app.paused)
        self.assertIn("stopped responding", self.app.fault)
        self.assertEqual(self.app.actions.remaining, 0)

    def test_slow_initial_reply_is_discarded_but_next_fresh_reply_can_play(self):
        self.app.mode = "play"
        first = self.app.worker.sent[0]
        self.advance(1.2)
        self.assertFalse(self.app.paused)
        self.app.worker.incoming.append(self.result(first, "punch"))
        self.advance(.02)
        self.assertEqual(self.app.counts["applied"], 0)
        self.assertEqual(self.app.counts["discarded"], 1)
        self.assertEqual(len(self.app.worker.sent), 2)
        second = self.app.worker.sent[-1]
        self.assertGreater(second.snapshot.frame, first.snapshot.frame)
        self.advance(.2)
        self.app.worker.incoming.append(self.result(second, "crouching_kick"))
        self.advance(.02)
        self.assertEqual(self.app.counts["applied"], 1)
        self.assertFalse(self.app.paused)

    def test_reset_does_not_replenish_request_budget(self):
        self.app.jobs = 10
        self.app.reset()
        self.assertEqual(self.app.jobs, 10)

    def test_browser_only_closes_safely_when_its_controller_ui_fails(self):
        self.app.dashboard_only = True
        self.app.dashboard_enabled = True
        def broken(_app):
            raise RuntimeError("offline fixture")
        self.app.dashboard = SimpleNamespace(poll=broken, close=lambda:None)
        self.app.dashboard_call("poll")
        self.assertTrue(self.app.closing)
        self.assertTrue(self.app.paused)
        self.assertIsNone(self.app.dashboard)
        self.assertEqual(self.app.actions.remaining, 0)

    def test_browser_budget_message_names_the_available_stop_control(self):
        self.app.dashboard_only = True
        self.app.active_seconds = self.app.settings.duration
        self.assertTrue(self.app.finish_trial_if_needed())
        self.assertIn("Stop in the dashboard", self.app.reason)
        self.assertTrue(self.app.paused)

    def test_round_transition_releases_inputs_discards_reply_and_resumes_requests(self):
        job = self.app.worker.sent[0]
        self.app.actions.accept("fireball", VALUES)
        with patch.object(self.app.game, "step", return_value=({**VALUES, "enemy_health": -1}, False)):
            self.advance(.02)
        self.assertFalse(self.app.paused)
        self.assertFalse(self.app.rounds.fighting)
        self.assertEqual(self.app.actions.remaining, 0)
        self.assertEqual(self.app.next_buttons(), self.app.game.zero)
        self.app.worker.incoming.append(self.result(job, "punch"))
        self.advance(.3)
        self.assertEqual(len(self.app.worker.sent), 1)
        self.assertEqual(self.app.counts["applied"], 0)
        self.assertEqual(self.app.counts["discarded"], 1)
        started = {**VALUES, "round_timer": 0x9926, "player_rounds_won": 1}
        self.app.rounds.previous["round_timer"] = 0x9927
        with patch.object(self.app.game, "step", return_value=(started, False)):
            self.advance(.02)
        self.assertTrue(self.app.rounds.fighting)
        self.assertEqual(self.app.episode, 1)
        self.assertEqual(len(self.app.worker.sent), 2)
        self.assertEqual(self.app.worker.sent[-1].snapshot.episode, 1)
        self.assertIsNone(self.app.worker.sent[-1].snapshot.state["recent"])

    def test_automatic_rematch_keeps_session_limits_and_invalidates_old_reply(self):
        job = self.app.worker.sent[0]
        self.app.active_seconds = 10
        self.app.jobs = 10
        self.app.restart_match()
        self.assertFalse(self.app.paused)
        self.assertEqual(self.app.active_seconds, 10)
        self.assertEqual(self.app.jobs, 10)
        self.app.worker.incoming.append(self.result(job))
        self.app._callback()
        self.assertEqual(self.app.counts["applied"], 0)
        self.assertEqual(self.app.counts["rematches"], 1)
        self.assertTrue(self.app.game.retried)

    def test_new_opponent_checkpoints_and_invalidates_previous_match_reply(self):
        job = self.app.worker.sent[0]
        self.app.update_round({**VALUES, "enemy_health": -1, "player_rounds_won": 2}, False)
        intro = {**VALUES, "opponent_character_id": 4, "round_timer": 0x9928}
        self.app.update_round(intro, False)
        self.assertFalse(self.app.rounds.fighting)
        self.app.update_round({**intro, "round_timer": 0x9927}, False)
        self.assertTrue(self.app.rounds.fighting)
        self.assertTrue(self.app.game.checkpoint_saved)
        self.assertEqual(self.app.counts["opponents_advanced"], 1)
        self.assertEqual(self.app.counts["rematches"], 0)
        self.assertEqual(len(self.app.observer.history), 0)
        self.app.worker.incoming.append(self.result(job))
        self.app.accept_results(self.now)
        self.assertIsNone(self.app.pending)
        self.assertEqual(self.app.counts["applied"], 0)

    def test_single_round_option_still_pauses_at_knockout(self):
        self.app.settings = replace(self.app.settings, continue_rounds=False)
        with patch.object(self.app.game, "step", return_value=({**VALUES, "health": -1}, False)):
            self.advance(.02)
        self.assertTrue(self.app.paused)
        self.assertTrue(self.app.terminal)

    def test_duration_limit_applies_between_rounds_without_reset(self):
        self.app.update_round({**VALUES, "health": -1}, False)
        self.app.active_seconds = self.app.settings.duration
        with patch.object(self.app.game, "reset") as reset:
            self.advance(.02)
        self.assertTrue(self.app.paused)
        self.assertTrue(self.app.budget_finished)
        reset.assert_not_called()

    def test_request_limit_applies_between_rounds(self):
        self.app.update_round({**VALUES, "health": -1}, False)
        self.app.jobs = self.app.settings.max_requests
        self.app.outstanding = None
        self.advance(.02)
        self.assertTrue(self.app.paused)
        self.assertTrue(self.app.budget_finished)

    def test_play_passes_normal_inputs_without_tactical_range_veto(self):
        self.app.mode = "play"
        self.app.pending = self.result(self.app.worker.sent[0], "punch")
        self.app.apply_pending(self.now)
        self.assertEqual(self.app.counts["applied"], 1)

    def test_obsolete_punch_is_not_queued_and_a_fresh_ready_reply_can_attack(self):
        self.app.mode = "play"
        job = self.app.worker.sent[0]
        self.app.actions.accept("approach", VALUES)
        self.app.observer.update({**VALUES, "player_status": 526, "health": 150}, self.now)
        self.app.pending = self.result(job, "punch")
        self.app.apply_pending(self.now)
        self.assertEqual(self.app.counts["applied"], 0)
        self.assertEqual(self.app.counts["discarded"], 1)
        self.assertIsNone(self.app.pending)
        self.assertEqual(self.app.actions.remaining, 0)
        self.assertEqual(self.app.coach.summary({"preferred_spacing":"flexible_attack_range"})["last_attack_mix"], {})
        self.app.observer.update(VALUES, self.now)
        self.app.outstanding = None
        self.app.next_eligible = self.now
        self.app.dispatch(self.now)
        self.assertEqual(len(self.app.worker.sent), 2)
        self.app.pending = self.result(self.app.worker.sent[-1], "crouching_kick")
        self.app.apply_pending(self.now)
        self.assertEqual(self.app.counts["applied"], 1)

    def test_revalidation_does_not_cancel_already_committed_inputs(self):
        self.app.mode = "play"
        self.app.actions.accept("fireball", VALUES)
        self.app.observer.update({**VALUES, "player_status": 524}, self.now)
        self.app.pending = self.result(self.app.worker.sent[0], "punch")
        remaining = self.app.actions.remaining
        self.app.apply_pending(self.now)
        self.assertEqual(self.app.actions.remaining, remaining)
        self.assertEqual(self.app.counts["discarded"], 0)
        self.assertIsNotNone(self.app.pending)

    def test_no_new_request_during_committed_fireball_input(self):
        self.app.outstanding = None
        self.app.actions.accept("fireball", VALUES)
        self.app.dispatch(self.now)
        self.assertEqual(len(self.app.worker.sent), 1)
        for _ in range(9):
            self.app.actions.next_buttons(VALUES)
        self.app.dispatch(self.now)
        self.assertEqual(len(self.app.worker.sent), 2)

    def test_no_new_request_while_reply_is_waiting(self):
        self.app.outstanding = None
        self.app.pending = self.result(self.app.worker.sent[0], "punch")
        self.app.dispatch(self.now)
        self.assertEqual(len(self.app.worker.sent), 1)

    def test_attack_reply_reaches_emulator_and_releases_buttons(self):
        for action, attack_button in (("punch", "X"), ("crouching_kick", "A")):
            with self.subTest(action=action):
                self.advance(.3)
                job = self.app.worker.sent[-1]
                self.app.worker.incoming.append(self.result(job, action))
                with patch.object(self.app.game, "step", wraps=self.app.game.step) as step:
                    self.advance(.14)
                vectors = [call.args[0] for call in step.call_args_list]
                self.assertTrue(any(v[BUTTONS.index(attack_button)] for v in vectors))
                self.assertTrue(all(not v[BUTTONS.index("RIGHT")] for v in vectors))
                self.assertEqual(vectors[-1], [0] * len(BUTTONS))


if __name__ == "__main__":
    unittest.main()
