from copy import deepcopy
import json
from pathlib import Path
import unittest

from controller.characters import opponent_profile
from controller.coaching import Coaching, clock_seconds
from controller.context import decision_context
from controller.observation import ObservationBuilder
from test_controller import VALUES


class CoachingTests(unittest.TestCase):
    def setUp(self):
        self.coach = Coaching()
        self.observer = ObservationBuilder()
        self.info = {**VALUES, "opponent_character_id": 4}
        self.execution = {"action": "neutral", "input_sequence_active": False}
        self.step(0)

    def step(self, frame, **changes):
        self.info.update(changes)
        self.coach.observe(self.info, frame, self.execution)
        self.observer.update(self.info, frame / 60)

    def advance(self, end, **changes):
        for frame in range(self.coach.frame + 1, end + 1):
            self.step(frame, **changes)

    def context(self):
        state = decision_context(self.observer.model_state("neutral", 0, None, execution=self.execution))
        return self.coach.context(state, self.info, "champion")

    def fireballs(self):
        self.coach.applied("fireball", 0)
        self.advance(60)
        self.coach.applied("neutral", 60)
        self.coach.applied("retreat", 60)
        self.coach.applied("fireball", 60)

    def test_rolling_pace_ignores_recovery_and_counts_attack_not_strength(self):
        self.fireballs()
        ctx = self.context()
        self.assertEqual(ctx["recent_tactics"]["fireball_attempts"], 2)
        self.assertEqual(ctx["recent_tactics"]["consecutive_fireball_attacks"], 2)
        self.assertEqual(ctx["recent_tactics"]["fireball_pace"], "ease_off")
        self.assertEqual(ctx["coaching"]["cue"], "change_pressure")
        self.assertEqual(ctx["recent_tactics"]["fireball_pace"], "ease_off")
        self.advance(360)
        self.assertEqual(self.context()["recent_tactics"]["fireball_pace"], "available")

    def test_applied_combo_counts_towards_pace_but_is_one_attack(self):
        self.coach.applied("crouch_kick_fireball", 0)
        self.coach.applied("fireball", 0)
        history = self.context()["recent_tactics"]
        self.assertEqual(history["fireball_attempts"], 2)
        self.assertEqual(sum(history["last_attack_mix"].values()), 2)

    def test_projectile_spawn_is_counted_once_and_separate_from_attempt(self):
        self.coach.applied("fireball", 0)
        self.advance(12, player_projectile_flags=257)
        self.assertEqual(self.context()["recent_tactics"]["projectiles_spawned"], 1)
        self.advance(15, player_projectile_flags=256)
        self.advance(18, player_projectile_flags=257)
        self.assertEqual(self.context()["recent_tactics"]["projectiles_spawned"], 2)
        self.assertEqual(self.context()["recent_tactics"]["fireball_attempts"], 1)

    def test_successful_zoning_gets_modest_allowance_not_unlimited_fireballs(self):
        self.step(1, opponent_character_id=6)
        self.coach.applied("fireball", 1)
        self.advance(60, enemy_health=150)
        self.coach.applied("fireball", 60)
        ctx = self.context()
        self.assertEqual(ctx["recent_tactics"]["fireball_allowance"], 3)
        self.assertEqual(ctx["recent_tactics"]["fireball_pace"], "available")
        self.coach.applied("fireball", 60)
        ctx = self.context()
        self.assertEqual(ctx["recent_tactics"]["fireball_pace"], "ease_off")
        self.assertIn("outside grabs", ctx["coaching"]["hint"])
        self.assertNotIn("forward jump", ctx["coaching"]["hint"])
        self.step(61, health=120)
        self.assertEqual(self.context()["recent_tactics"]["fireball_allowance"], 2)

    def test_high_damage_against_ken_does_not_remove_pacing(self):
        self.fireballs()
        self.step(61, enemy_health=80)
        self.assertEqual(self.context()["recent_tactics"]["fireball_pace"], "ease_off")

    def test_threat_recovery_and_close_opportunity_outrank_pace(self):
        self.fireballs()
        self.step(61, player_status=524)
        self.assertEqual(self.context()["coaching"]["cue"], "recover")
        self.step(62, player_status=512, opponent_projectile_flags=257,
                  opponent_projectile_x=250, opponent_projectile_vx=-742)
        self.assertEqual(self.context()["coaching"]["cue"], "projectile_defence")
        self.step(63, opponent_projectile_flags=0, opponent_x=223)
        self.assertEqual(self.context()["coaching"]["cue"], "close_pressure")
        self.step(64, opponent_status=522)
        self.assertEqual(self.context()["coaching"]["cue"], "ground_defence")
        self.step(65, opponent_status=512)
        self.assertEqual(self.context()["coaching"]["cue"], "counter_opportunity")

    def test_rising_approach_gets_interception_and_recovery_gets_guard(self):
        self.step(1, opponent_x=245, opponent_y=170, opponent_status=516)
        self.step(2, opponent_x=240, opponent_y=160)
        self.assertEqual(self.context()["coaching"]["cue"], "intercept_jump")
        self.step(3, player_status=526, opponent_x=235, opponent_y=150)
        self.assertEqual(self.context()["coaching"]["cue"], "air_defence")
        self.step(4, player_y=150)
        self.assertEqual(self.context()["coaching"]["cue"], "airborne")

    def test_low_health_still_has_close_attack_and_clock_changes_urgency(self):
        self.step(1, health=40, opponent_x=223)
        ctx = self.context()
        self.assertEqual(ctx["coaching"]["preference"], "careful_counter")
        self.assertEqual(ctx["coaching"]["cue"], "close_pressure")
        self.step(2, round_timer=0x1950)
        self.assertEqual(self.context()["coaching"]["preference"], "must_score")
        self.step(3, health=160, enemy_health=50)
        self.assertEqual(self.context()["coaching"]["preference"], "protect_lead")

    def test_pause_duplicate_frame_and_request_normalisation_do_not_change_history(self):
        self.fireballs()
        before = self.context()
        self.coach.observe(self.info, 60, self.execution)
        self.assertEqual(before, self.context())
        original = deepcopy(before)
        self.assertEqual(decision_context(before), before)
        self.assertEqual(before, original)
        self.assertNotIn("matchup_advice", before)

    def test_reset_discontinuity_and_identity_change_clear_streak(self):
        self.fireballs()
        self.step(2)
        self.assertEqual(self.context()["recent_tactics"]["fireball_attempts"], 0)
        self.coach.applied("fireball", 2)
        self.step(3, opponent_character_id=5)
        self.assertEqual(self.context()["recent_tactics"]["fireball_attempts"], 0)
        self.coach.applied("fireball", 3)
        self.step(50)
        self.assertEqual(self.context()["recent_tactics"]["fireball_attempts"], 0)
        self.coach.reset()
        self.assertIsNone(self.coach.summary(opponent_profile("Ken", "champion")))

    def test_passive_counter_excludes_recovery_and_resets_on_movement(self):
        self.advance(180, player_status=524)
        self.assertEqual(self.context()["recent_tactics"]["ready_passive_seconds"], 0)
        self.advance(361, player_status=512)
        self.assertGreaterEqual(self.context()["recent_tactics"]["ready_passive_seconds"], 3)
        self.execution["action"] = "approach"
        self.step(362)
        self.assertEqual(self.context()["recent_tactics"]["ready_passive_seconds"], 0)

    def test_capabilities_depend_on_mode_and_observation_overrides_profile(self):
        self.assertFalse(opponent_profile("Chun-Li", "champion")["has_projectile"])
        self.assertTrue(opponent_profile("Chun-Li", "hyper")["has_projectile"])
        self.assertIsNone(opponent_profile("Chun-Li", "unknown")["has_projectile"])
        self.assertIsNone(opponent_profile("unknown", "champion")["has_projectile"])
        self.step(1, opponent_character_id=5, opponent_projectile_flags=257,
                  opponent_projectile_x=250, opponent_projectile_vx=-742)
        ctx = self.context()
        self.assertTrue(ctx["opponent_profile"]["capability_conflict"])
        self.assertIsNone(ctx["opponent_profile"]["has_projectile"])
        self.assertEqual(ctx["coaching"]["cue"], "projectile_defence")

    def test_bcd_clock_and_unknown_readiness(self):
        self.assertEqual(clock_seconds(0x8950), 89)
        self.assertIsNone(clock_seconds(0xAF00))
        self.assertIsNone(clock_seconds(None))
        self.step(1, player_status=999)
        self.assertEqual(self.context()["coaching"]["cue"], "recover")

    def test_recorded_walking_failures_get_attack_options_or_threats(self):
        cases = json.loads((Path(__file__).parent / "fixtures/walking-regression.json").read_text())["cases"]
        for case in cases:
            with self.subTest(request=case["request_id"]):
                raw = case["observation"]
                player, enemy = raw["player"], raw["opponent"]
                info = {**VALUES, "health": player["health"], "enemy_health": enemy["health"],
                        "player_x": player["x"], "opponent_x": enemy["x"],
                        "player_y": player["y"], "opponent_y": enemy["y"],
                        "player_status": player["status_raw"], "opponent_status": enemy["status_raw"]}
                coach = Coaching()
                coach.observe(info, 0, self.execution)
                ctx = coach.context(decision_context(raw), info, "champion")
                self.assertEqual(ctx["coaching"]["cue"], case["expected_cue"])
                self.assertEqual(ctx["recent_tactics"]["fireball_pace"], "available")
                self.assertNotIn("Prefer approach", ctx["coaching"]["hint"])
                if case["request_id"] == 100:
                    self.assertEqual(ctx["coaching"]["preference"], "careful_counter")
                    self.assertIn("do not chase", ctx["coaching"]["hint"])

    def walk_passage(self):
        for frame in (0, 40, 80, 120):
            self.advance(frame)
            self.coach.applied("approach", frame)
            self.coach.applied("neutral", frame)

    def test_walks_survive_recovery_replies_and_measure_distance_progress(self):
        self.walk_passage()
        self.step(121, opponent_x=VALUES["opponent_x"] - 30)
        ctx = self.context()
        passage = ctx["recent_tactics"]["approach"]
        self.assertEqual(passage["attempts_since_attack"], 4)
        self.assertEqual(passage["distance_closed"], 30)
        self.assertEqual(passage["feedback"], "walking_without_attacking")
        self.assertIn("Repeated walking", ctx["coaching"]["hint"])
        self.assertEqual(ctx["recent_tactics"]["fireball_pace"], "available")

    def test_walk_feedback_tracks_harm_but_does_not_mislabel_projectile_followup(self):
        self.walk_passage()
        self.step(121, health=150)
        self.assertEqual(self.context()["recent_tactics"]["approach"]["feedback"], "taking_damage_without_attacking")
        self.step(122, enemy_health=150)
        self.assertEqual(self.context()["recent_tactics"]["approach"]["feedback"], "none")

    def test_attack_expiry_and_round_reset_end_walk_passage(self):
        self.walk_passage()
        before = self.context()
        self.coach.observe(self.info, 120, self.execution)
        self.assertEqual(before, self.context())  # Paused/duplicate frame does not age the history.
        self.coach.applied("crouching_kick", 120)
        self.assertEqual(self.context()["recent_tactics"]["approach"]["attempts_since_attack"], 0)
        self.coach.applied("approach", 120)
        self.advance(481)
        self.assertEqual(self.context()["recent_tactics"]["approach"]["attempts_since_attack"], 0)
        self.coach.applied("approach", 481)
        self.step(482, opponent_character_id=5)
        self.assertEqual(self.context()["recent_tactics"]["approach"]["attempts_since_attack"], 0)

    def test_walking_feedback_does_not_overrule_immediate_threat(self):
        self.walk_passage()
        self.step(121, opponent_status=522, opponent_x=270)
        ctx = self.context()
        self.assertEqual(ctx["coaching"]["cue"], "ground_defence")
        self.assertNotIn("Repeated walking", ctx["coaching"]["hint"])
        self.assertEqual(ctx["recent_tactics"]["approach"]["feedback"], "walking_without_attacking")

    def test_distant_ground_attack_is_not_discarded_as_no_threat(self):
        self.step(1, opponent_x=450, opponent_status=524)
        self.assertEqual(self.context()["coaching"]["cue"], "ground_attack_observed")

    def test_air_threat_distance_is_independent_of_jab_band(self):
        self.advance(8, opponent_x=250, opponent_y=120, opponent_status=522)
        self.assertEqual(self.context()["engagement"]["range_band"], "poke")
        self.assertEqual(self.context()["coaching"]["cue"], "air_defence")

    def test_unknown_distance_does_not_invent_an_attack_opportunity(self):
        state = decision_context(self.observer.model_state("neutral", 0, None))
        state["distance"] = None
        ctx = self.coach.context(state, self.info, "champion")
        self.assertEqual(ctx["coaching"]["cue"], "uncertain")

    def test_caution_and_clock_preferences_change_the_advice_not_just_label(self):
        self.step(1, health=40)
        self.assertIn("Low health", self.context()["coaching"]["hint"])
        self.step(2, round_timer=0x1950)
        self.assertIn("seek damage", self.context()["coaching"]["hint"])
        self.step(3, health=160, enemy_health=50)
        self.assertIn("entry is optional", self.context()["coaching"]["hint"])
