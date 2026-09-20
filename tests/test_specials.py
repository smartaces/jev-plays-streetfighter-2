import unittest
from controller.actions import ActionExecutor, JUMP_ATTACKS
from controller.config import Settings
from controller.contracts import ActionId
from controller.jev import question_spec
from test_controller import BUTTONS, VALUES


class SpecialMoveTests(unittest.TestCase):
    def test_special_motions_mirror_and_end_with_release(self):
        for action in (ActionId.FIREBALL, ActionId.DRAGON, ActionId.HURRICANE):
            sequences = []
            for x in (346, 100):
                values = {**VALUES, "opponent_x": x}
                executor = ActionExecutor(BUTTONS, Settings())
                executor.accept(action, values)
                sequence = []
                while executor.remaining:
                    self.assertTrue(executor.locked)
                    sequence.append({b for b, v in zip(BUTTONS, executor.next_buttons(values)) if v})
                self.assertEqual(sequence[-1], set())
                self.assertFalse(executor.locked)
                sequences.append(sequence)
            mirror = {"LEFT": "RIGHT", "RIGHT": "LEFT"}
            self.assertEqual([{mirror.get(b, b) for b in frame} for frame in sequences[0]], sequences[1])

    def test_every_advertised_move_can_execute_and_be_cancelled(self):
        self.assertEqual(set(question_spec(Settings())["criteria"]), set(ActionId))
        for action in ActionId:
            executor = ActionExecutor(BUTTONS, Settings())
            self.assertTrue(executor.accept(action, VALUES))
            self.assertLessEqual(executor.remaining, 51 if action in JUMP_ATTACKS else
                                 36 if action in {ActionId.BLOCK, ActionId.RETREAT} else 12)
            executor.next_buttons(VALUES)
            executor.clear()
            self.assertFalse(any(executor.next_buttons(VALUES)))

    def test_low_guard_holds_away_and_updates_when_sides_change(self):
        executor = ActionExecutor(BUTTONS, Settings())
        executor.accept(ActionId.BLOCK, VALUES)
        vector = executor.next_buttons(VALUES)
        self.assertEqual({b for b, v in zip(BUTTONS, vector) if v}, {"DOWN", "LEFT"})
        self.assertEqual({b for b, v in zip(BUTTONS, executor.next_buttons({**VALUES, "opponent_x": 100})) if v}, {"DOWN", "RIGHT"})
