import unittest

from controller.observation import ObservationBuilder, InvalidState, projectile, action_state_rejection
from controller.actions import ActionExecutor
from controller.config import Settings
from test_controller import VALUES, BUTTONS


class MemoryTests(unittest.TestCase):
    def test_identity_comes_from_memory_and_unknown_is_not_guile(self):
        for identifier, expected in ((3, "Guile"), (4, "Ken"), (0, "Ryu"), (255, "unknown")):
            self.assertEqual(self.state(opponent_character_id=identifier)["opponent"]["character"], expected)

    def state(self, **changes):
        observer = ObservationBuilder()
        observer.update({**VALUES, **changes}, 0)
        return observer.model_state("neutral", 0, None)

    def test_absent_fields_fail_instead_of_silently_disabling_detection(self):
        for key in ("player_status", "opponent_projectile_flags", "player_move_timer"):
            values = VALUES.copy()
            del values[key]
            with self.assertRaises(InvalidState):
                ObservationBuilder().update(values, 0)

    def test_unknown_state_and_flags_remain_unknown(self):
        state = self.state(player_status=999, opponent_projectile_flags=511)
        self.assertIsNone(state["player"]["ground_action_ready"])
        self.assertEqual(state["player"]["state"], "unknown")
        self.assertIsNone(state["projectiles"]["opponent"]["active"])
        self.assertIsNone(state["projectiles"]["opponent"]["incoming"])

    def test_despawn_hides_leftover_coordinates_and_velocity(self):
        for flags in (0, 256):
            state = self.state(opponent_projectile_flags=flags, opponent_projectile_x=225,
                               opponent_projectile_y=150, opponent_projectile_vx=-742)
            p = state["projectiles"]["opponent"]
            self.assertFalse(p["active"])
            self.assertFalse(p["incoming"])
            self.assertNotIn("x", p)
            self.assertIsNone(p["frames_to_player_x_estimate"])

    def test_incoming_direction_on_both_sides_and_after_passing(self):
        for x, vx, incoming in ((250, -742, True), (160, 742, True),
                                (250, 742, False), (160, -742, False)):
            p = self.state(opponent_projectile_flags=257, opponent_projectile_x=x,
                           opponent_projectile_vx=vx)["projectiles"]["opponent"]
            self.assertEqual(p["incoming"], incoming)
            if incoming:
                self.assertAlmostEqual(p["frames_to_player_x_estimate"], 15.5, places=1)

    def test_airborne_hit_reaction_is_not_labelled_jump(self):
        player = self.state(player_y=139, player_status=526)["player"]
        self.assertTrue(player["above_ground"])
        self.assertFalse(player["jumping"])
        self.assertFalse(player["ground_action_ready"])
        self.assertEqual(player["state"], "hit_or_block_reaction")

    def test_jump_direction_uses_opponent_motion_not_player_approach(self):
        b = ObservationBuilder()
        for i in range(7):
            b.update({**VALUES, "player_x": 205+i*5, "opponent_x": 280+i,
                      "opponent_status": 516, "opponent_y": 150}, i/600)
        state = b.model_state("approach", 0, "approach")
        self.assertLess(state["recent"]["distance_change"], 0)
        self.assertFalse(state["opponent"]["jump_towards_player"])
        self.assertEqual(state["opponent"]["vx_per_frame"], 1)
        b.clear()
        b.update(VALUES, 5)
        self.assertIsNone(b.model_state("neutral", 0, None)["opponent"]["vx_per_frame"])

    def test_recovery_requires_observed_spawn_and_resets_on_state_change(self):
        b = ObservationBuilder()
        b.update({**VALUES, "player_status": 524, "player_move_timer": 40}, 0)
        self.assertIsNone(b.model_state("neutral", 0, "fireball")["player"]["fireball_recovery_frames"])
        b.update({**VALUES, "player_status": 524, "player_move_timer": 39,
                  "player_projectile_flags": 257}, .016)
        self.assertEqual(b.model_state("neutral", 0, "neutral")["player"]["fireball_recovery_frames"], 39)
        b.update({**VALUES, "player_move_timer": 255}, .032)
        self.assertIsNone(b.model_state("neutral", 0, "fireball")["player"]["fireball_recovery_frames"])

    def test_jump_forecast_uses_reply_delay_and_recent_frame_motion(self):
        b = ObservationBuilder()
        for i in range(7):
            b.update({**VALUES, "opponent_x": 355-i*3, "opponent_status": 516,
                      "opponent_y": 145+i}, i/60)
        state = b.model_state("neutral", 0, None, decision_delay_frames=20)
        self.assertEqual(state["distance"], 132)
        self.assertTrue(state["opponent"]["jump_towards_player"])
        self.assertEqual(state["opponent"]["jump_in_distance_after_reply_estimate"], 72)

    def test_ready_inputs_have_no_tactical_range_or_projectile_veto(self):
        for changes in ({}, {"opponent_x": 235}, {"player_projectile_flags": 257}):
            for action in ("punch", "crouching_kick", "fireball", "dragon_punch", "crouching_block"):
                self.assertIsNone(action_state_rejection(action, {**VALUES, **changes}))
        self.assertEqual(action_state_rejection("air_punch", VALUES), "air_attack_window_passed")
        self.assertIsNone(action_state_rejection("air_punch", {**VALUES, "player_status": 516, "player_y": 150}))

    def test_delayed_ground_attacks_recheck_readiness_but_guard_remains_possible(self):
        for status in (520, 522, 524, 526, 999):
            for action in ("punch", "crouching_kick", "sweep", "fireball", "dragon_punch", "hurricane_kick", "throw_forward", "crouch_kick_fireball", "jump_forward_kick"):
                self.assertEqual(action_state_rejection(action, {**VALUES, "player_status": status}), "ground_action_window_passed")
            for action in ("neutral", "retreat", "crouching_block"):
                self.assertIsNone(action_state_rejection(action, {**VALUES, "player_status": status}))

    def test_readiness_accounts_for_committed_input_even_before_game_state_changes(self):
        b = ObservationBuilder()
        b.update(VALUES, 0)
        executor = ActionExecutor(BUTTONS, Settings())
        executor.accept("fireball", VALUES)
        state = b.model_state(executor.current, executor.remaining, executor.last_action,
                              execution=executor.execution_state())
        self.assertTrue(state["player"]["observed_ground_ready"])
        self.assertFalse(state["player"]["ground_action_ready"])
        self.assertTrue(state["controls"]["input_sequence_active"])

    def test_defence_bridges_a_decision_delay_and_still_cancels(self):
        for action in ("retreat", "crouching_block"):
            executor = ActionExecutor(BUTTONS, Settings())
            executor.accept(action, VALUES)
            for _ in range(25):
                self.assertTrue(any(executor.next_buttons(VALUES)))
            executor.clear()
            self.assertFalse(any(executor.next_buttons(VALUES)))

    def test_approach_can_enter_throw_range(self):
        executor = ActionExecutor(BUTTONS, Settings())
        executor.accept("approach", VALUES)
        self.assertTrue(any(executor.next_buttons(VALUES)))
        self.assertTrue(any(executor.next_buttons({**VALUES, "opponent_x": 225})))
