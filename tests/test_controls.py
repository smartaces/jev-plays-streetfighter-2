import unittest
from controller.actions import ActionExecutor, JUMP_ATTACKS
from controller.config import Settings
from controller.contracts import ActionId, Strength
from test_controller import BUTTONS, VALUES


class ExpandedControlTests(unittest.TestCase):
    def setUp(self):
        self.a = ActionExecutor(BUTTONS, Settings())

    def names(self, vector):
        return {b for b, held in zip(BUTTONS, vector) if held}

    def test_every_normal_and_special_strength_uses_the_matching_button(self):
        punches = {ActionId.PUNCH, ActionId.CROUCH_PUNCH, ActionId.AIR_PUNCH,
                   ActionId.FIREBALL, ActionId.DRAGON}
        kicks = {ActionId.STANDING_KICK, ActionId.KICK, ActionId.AIR_KICK, ActionId.HURRICANE}
        for action in punches | kicks:
            for strength, button in zip(Strength, 'XYZ' if action in punches else 'ABC'):
                self.a.accept(action, VALUES, strength)
                frames = [self.names(self.a.next_buttons(VALUES)) for _ in range(12)]
                used = set().union(*frames) & set('ABCXYZ')
                self.assertEqual(used, {button}, (action, strength))
                self.assertFalse(frames[-1])

    def test_jump_attack_waits_for_airborne_state_and_presses_only_once(self):
        for action in JUMP_ATTACKS:
            self.a.accept(action, VALUES, 'heavy')
            button = 'Z' if action.value.endswith('_punch') else 'C'
            attack_frames = []
            for f in range(45):
                v = VALUES if f < 3 else {**VALUES, 'player_y': 130, 'player_status': 516}
                names = self.names(self.a.next_buttons(v))
                if button in names:
                    attack_frames.append(f)
            self.assertEqual(len(attack_frames), 2, action)
            self.assertGreaterEqual(attack_frames[0], 8)
            self.assertFalse(self.a.locked)

    def test_interrupted_jump_aborts_the_future_attack(self):
        self.a.accept('jump_forward_kick', VALUES, 'heavy')
        for _ in range(5):
            self.a.next_buttons({**VALUES, 'player_y': 150, 'player_status': 516})
        for _ in range(50):
            self.assertNotIn('C', self.names(self.a.next_buttons(
                {**VALUES, 'player_y': 140, 'player_status': 526})))
        self.assertFalse(self.a.locked)

    def test_failed_takeoff_does_not_turn_into_a_ground_attack(self):
        self.a.accept('jump_forward_punch', VALUES, 'heavy')
        for _ in range(60):
            self.assertNotIn('Z', self.names(self.a.next_buttons(VALUES)))
        self.assertFalse(self.a.locked)
        self.assertTrue(any(e['kind'] == 'jump_attack_aborted' for e in self.a.events))

    def test_jump_attack_mirrors_takeoff_but_keeps_same_attack_strength(self):
        for x, direction in ((346, 'RIGHT'), (100, 'LEFT')):
            v = {**VALUES, 'opponent_x': x}
            self.a.accept('jump_forward_kick', v, 'medium')
            self.assertEqual(self.names(self.a.next_buttons(v)), {'UP', direction})
            masks = [self.names(self.a.next_buttons({**v, 'player_status': 516, 'player_y': 140})) for _ in range(40)]
            self.assertTrue(any('B' in m for m in masks))

    def test_clear_cancels_future_jump_attack(self):
        self.a.accept('jump_up_punch', VALUES)
        self.a.next_buttons(VALUES)
        self.a.clear()
        self.assertFalse(self.a.locked)
        self.assertFalse(any(self.a.next_buttons({**VALUES, 'player_y': 130, 'player_status': 516})))

    def test_combo_releases_kick_and_completes_motion_without_network_wait(self):
        self.a.accept('crouch_kick_fireball', VALUES, 'heavy')
        frames = [self.names(self.a.next_buttons(VALUES)) for _ in range(12)]
        self.assertEqual(frames[2:4], [{'DOWN','B'}]*2)
        self.assertEqual(frames[4:6], [{'DOWN'}]*2)
        self.assertEqual(frames[6:8], [{'DOWN','RIGHT'}]*2)
        self.assertEqual(frames[8:10], [{'RIGHT','Z'}]*2)
        self.assertEqual(frames[10:], [set(),set()])

    def test_throw_direction_and_effective_strength_are_explicit(self):
        for x, towards, away in ((346,'RIGHT','LEFT'),(100,'LEFT','RIGHT')):
            for action, direction in (('throw_forward',towards),('throw_back',away)):
                self.a.accept(action, {**VALUES,'opponent_x':x}, 'light')
                frames = [self.names(self.a.next_buttons(VALUES)) for _ in range(5)]
                self.assertEqual(frames[2], {direction,'Z'})
                self.assertEqual(self.a.strength, 'heavy')


if __name__ == '__main__':
    unittest.main()
