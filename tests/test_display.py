import unittest
from controller.display import fitted_rectangle


class DisplayLayoutTests(unittest.TestCase):
    def test_four_by_three_window_is_filled(self):
        self.assertEqual(fitted_rectangle(960, 720), (0, 0, 960, 720))

    def test_wide_window_centres_game_without_stretching(self):
        self.assertEqual(fitted_rectangle(1200, 720), (120, 0, 960, 720))

    def test_tall_window_centres_game_without_stretching(self):
        self.assertEqual(fitted_rectangle(960, 900), (0, 90, 960, 720))
