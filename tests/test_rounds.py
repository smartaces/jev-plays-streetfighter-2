import unittest

from controller.observation import InvalidState
from controller.rounds import RoundFlow


START = {"health": 176, "enemy_health": 176, "round_timer": 0x9850,
         "player_character_id": 0, "opponent_character_id": 3,
         "player_rounds_won": 0, "opponent_rounds_won": 0, "continuetimer": 0,
         "player_status": 512, "opponent_status": 512}


class RoundTests(unittest.TestCase):
    def setUp(self):
        self.flow = RoundFlow(START, 60)

    def events(self, info, **kwargs):
        return [event["event"] for event in self.flow.advance(info, **kwargs)]

    def test_zero_health_is_alive_but_negative_health_ends_round(self):
        self.assertEqual(self.events({**START, "health": 0}), [])
        self.assertTrue(self.flow.fighting)
        dead = {**START, "health": -1}
        self.assertEqual(self.events(dead), ["round_finished"])
        self.assertEqual(self.events(dead), [])
        self.assertFalse(self.flow.fighting)

    def test_round_two_waits_for_clock_after_health_refill(self):
        self.events({**START, "enemy_health": -1})
        intro = {**START, "player_rounds_won": 1, "round_timer": 0x9928}
        self.assertEqual(self.events(intro), [])
        self.assertEqual(self.events(intro), [])
        self.assertFalse(self.flow.fighting)
        self.assertEqual(self.events({**intro, "round_timer": 0x9927}), ["round_started"])
        self.assertTrue(self.flow.fighting)
        self.assertEqual(self.flow.number, 2)

    def test_time_up_ends_round_without_knockout(self):
        self.assertEqual(self.events({**START, "round_timer": 0x0028}), ["round_finished"])

    def test_split_score_advances_to_round_three(self):
        self.events({**START, "enemy_health": -1, "player_rounds_won": 1})
        second = {**START, "player_rounds_won": 1, "round_timer": 0x9928}
        self.events(second)
        self.events({**second, "round_timer": 0x9927})
        self.events({**second, "health": -1, "opponent_rounds_won": 1})
        third = {**second, "opponent_rounds_won": 1}
        self.events(third)
        self.assertEqual(self.events({**third, "round_timer": 0x9927}), ["round_started"])
        self.assertEqual(self.flow.number, 3)
        self.assertIsNone(self.flow.match_end_frame)

    def test_round_award_catches_result_without_negative_health(self):
        self.assertEqual(self.events({**START, "opponent_rounds_won": 1}), ["round_finished"])

    def test_loss_retries_after_three_seconds(self):
        end = {**START, "opponent_rounds_won": 2}
        self.assertEqual(self.events(end), ["round_finished", "match_finished"])
        for _ in range(179):
            self.assertEqual(self.events(end), [])
        self.assertEqual(self.events(end), ["rematch"])

    def test_win_allows_next_opponent_and_waits_for_clock(self):
        end = {**START, "player_rounds_won": 2, "enemy_health": -1}
        self.assertEqual(self.events(end), ["round_finished", "match_finished"])
        for _ in range(180):
            self.assertEqual(self.events(end), [])
        intro = {**START, "opponent_character_id": 4, "round_timer": 0x9928}
        self.assertEqual(self.events(intro), [])
        self.assertFalse(self.flow.fighting)
        events = self.flow.advance({**intro, "round_timer": 0x9927})
        self.assertEqual([e["event"] for e in events], ["match_started", "round_started"])
        self.assertEqual(events[0]["opponent"], "Ken")
        self.assertEqual(self.flow.number, 1)
        self.assertTrue(self.flow.fighting)
        self.assertIsNone(self.flow.match_end_frame)

    def test_next_match_requires_reset_scores_and_two_fighters(self):
        self.events({**START, "player_rounds_won": 2})
        for changes in ({"player_rounds_won": 2}, {"opponent_status": 0}, {"enemy_health": 0}):
            intro = {**START, "round_timer": 0x9928, **changes}
            self.events(intro)
            self.assertEqual(self.events({**intro, "round_timer": 0x9927}), [])
            self.assertFalse(self.flow.fighting)

    def test_unknown_next_fighter_pauses_instead_of_sending_wrong_identity(self):
        self.events({**START, "player_rounds_won": 2})
        intro = {**START, "opponent_character_id": 255, "round_timer": 0x9928}
        self.events(intro)
        self.assertEqual(self.events({**intro, "round_timer": 0x9927}), ["transition_paused"])
        self.assertFalse(self.flow.fighting)

    def test_terminal_menu_restarts_immediately(self):
        self.assertEqual(self.events({**START, "continuetimer": 10}, terminal=True),
                         ["round_finished", "match_finished", "rematch"])

    def test_missing_round_mapping_is_an_error(self):
        for field in ("round_timer", "player_rounds_won", "opponent_rounds_won"):
            info = START.copy()
            del info[field]
            with self.assertRaises(InvalidState):
                self.flow.advance(info)

    def test_stuck_transition_pauses_without_resetting_progress(self):
        dead = {**START, "enemy_health": -1}
        self.events(dead)
        for _ in range(120 * 60 - 2):
            self.assertEqual(self.events(dead), [])
        self.assertEqual(self.events(dead), ["transition_paused"])
        self.assertEqual(self.events(dead), [])


if __name__ == "__main__":
    unittest.main()
