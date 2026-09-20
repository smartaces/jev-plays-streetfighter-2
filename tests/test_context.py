from copy import deepcopy
import json
from pathlib import Path
import unittest

from controller.context import decision_context
from controller.observation import ObservationBuilder
from test_controller import VALUES


class DecisionContextTests(unittest.TestCase):
    def test_native_airborne_reaction_keeps_height_distinct_from_animation(self):
        cases = json.loads((Path(__file__).parent / "fixtures/ground-air-cases.json").read_text())["cases"]
        raw = next(c["state"] for c in cases if c["name"] == "airborne_reaction")
        before = deepcopy(raw)
        state = decision_context(raw)
        enemy = state["opponent"]
        self.assertEqual(enemy["body_position"], "airborne")
        self.assertEqual(enemy["state"], "hit_or_block_reaction")
        self.assertEqual(enemy["vertical_motion"], "descending")
        self.assertEqual(enemy["horizontal_motion"], "away_from_opponent")
        self.assertEqual(state["separation_motion"], "opening")
        self.assertNotIn("jumping", enemy)
        self.assertNotIn("x", enemy)
        self.assertEqual(raw, before)
        self.assertEqual(decision_context(state), state)

    def test_airborne_attack_is_not_grounded_when_jump_animation_is_absent(self):
        observer = ObservationBuilder()
        observer.update({**VALUES, "opponent_status": 522, "opponent_y": 113}, 0)
        state = decision_context(observer.model_state("neutral", 0, None))
        self.assertEqual(state["opponent"]["body_position"], "airborne")
        self.assertEqual(state["opponent"]["state"], "attack_motion")
        self.assertEqual(state["opponent"]["vertical_motion"], "unknown")
        self.assertNotIn("jump_towards_player", state["opponent"])

    def test_missing_geometry_does_not_invent_ground_or_motion(self):
        state = decision_context({"opponent": {"state": "unknown"}, "player": {"ground_action_ready": None}})
        self.assertEqual(state["opponent"]["body_position"], "unknown")
        self.assertEqual(state["opponent"]["vertical_motion"], "unknown")
        self.assertIsNone(state["player"]["ground_action_ready"])
        self.assertEqual(decision_context(state), state)

    def test_motion_is_relative_to_opponent_on_both_sides(self):
        for enemy_x, velocity, expected in [(250, 2, "towards_opponent"), (100, 2, "away_from_opponent"), (100, -2, "towards_opponent")]:
            state = decision_context({"player": {"x":205,"y":192,"vx_per_frame":velocity,"vy_per_frame":0},
                                      "opponent": {"x":enemy_x,"y":192,"vx_per_frame":0,"vy_per_frame":0}})
            self.assertEqual(state["player"]["horizontal_motion"], expected)
            self.assertEqual(state["opponent"]["body_position"], "grounded")

    def test_only_current_matchup_tip_is_supplied_and_old_tip_is_replaced(self):
        guile = decision_context({"opponent": {"character": "Guile"}})
        ken = decision_context({**guile, "opponent": {"character": "Ken"}})
        self.assertIn("Sonic Booms", guile["matchup_advice"])
        self.assertNotIn("Guile", ken["matchup_advice"])
        self.assertIn("uppercut", ken["matchup_advice"])
        unknown = decision_context({**guile, "opponent": {"character": "unknown"}})
        self.assertIn("unknown", unknown["matchup_advice"])

    def test_unknowns_and_threat_facts_survive_without_diagnostic_codes(self):
        observer = ObservationBuilder()
        observer.update({**VALUES, "player_status": 999,
                         "opponent_projectile_flags": 257,
                         "opponent_projectile_x": 250,
                         "opponent_projectile_y": 150,
                         "opponent_projectile_vx": -742}, 0)
        raw = observer.model_state("neutral", 0, None)
        before = deepcopy(raw)
        state = decision_context(raw)
        self.assertIsNone(state["player"]["ground_action_ready"])
        self.assertEqual(state["player"]["state"], "unknown")
        self.assertTrue(state["projectiles"]["opponent"]["incoming"])
        self.assertEqual(state["projectiles"]["opponent"]["frames_to_player_x_estimate"], 15.5)
        self.assertEqual(state["distance"], 141)
        self.assertEqual(state["spacing"], "separated")
        self.assertNotIn("status_raw", state["player"])
        self.assertNotIn("flags_raw", state["projectiles"]["opponent"])
        self.assertEqual(raw, before)
        self.assertEqual(decision_context(state), state)

    def test_holding_movement_and_committed_input_remain_distinct(self):
        observer = ObservationBuilder()
        observer.update(VALUES, 0)
        for committed in (False, True):
            raw = observer.model_state("approach", 4, "approach", execution={
                "action": "approach", "strength": "light",
                "input_sequence_active": committed, "phase": "holding_input",
                "frames_remaining_upper_bound": 4,
            })
            state = decision_context(raw)
            self.assertIs(state["controls"]["input_sequence_active"], committed)
            self.assertIs(state["player"]["ground_action_ready"], not committed)
            self.assertNotIn("observed_ground_ready", state["player"])
            self.assertNotIn("input_frames_remaining", state)

    def test_close_cue_is_preserved_on_either_side(self):
        observer = ObservationBuilder()
        for delta in (-39, 39):
            observer.update({**VALUES, "opponent_x": VALUES["player_x"] + delta}, 0)
            state = decision_context(observer.model_state("neutral", 0, None))
            self.assertEqual(state["spacing"], "close")
            self.assertEqual(state["distance"], 39)

    def test_poke_band_is_distinct_from_old_close_label_and_predicts_retreat(self):
        raw = {"distance": 50, "spacing": "outside_basic_attack_reach", "decision_delay_frames_estimate": 20,
               "player": {"x":200,"y":192,"vx_per_frame":0,"vy_per_frame":0},
               "opponent": {"x":250,"y":192,"vx_per_frame":1.5,"vy_per_frame":0}}
        state = decision_context(raw)
        self.assertEqual(state["spacing"], "separated")
        self.assertEqual(state["engagement"]["range_band"], "poke")
        self.assertEqual(state["engagement"]["distance_after_reply_estimate"], 80)
        self.assertEqual(decision_context(state), state)

    def test_reach_bands_are_explicit_estimates_and_unknowns_stay_unknown(self):
        from controller.engagement import range_band
        for distance, expected in ((20,"close"), (35,"close"), (36,"poke"), (65,"poke"), (66,"entry"), (140,"entry"), (141,"far"), (None,"unknown"), (float('nan'),"unknown"), (True,"unknown")):
            self.assertEqual(range_band(distance), expected)
