"""Let the ROM advance after wins; retry the current opponent after losses."""
from numbers import Integral

from .observation import InvalidState
from .characters import CHARACTERS, character_name


def round_values(info):
    limits = {"round_timer": (0, 65535), "player_rounds_won": (0, 3),
              "opponent_rounds_won": (0, 3), "continuetimer": (0, 255),
              "player_character_id": (0, 255), "opponent_character_id": (0, 255)}
    values = {}
    for name, (low, high) in limits.items():
        value = info.get(name)
        if isinstance(value, bool) or not isinstance(value, Integral) or not low <= value <= high:
            raise InvalidState(f"Invalid or missing round value: {name}")
        values[name] = int(value)
    return values


class RoundFlow:
    def __init__(self, info, fps):
        self.fps = fps
        self.reset(info)

    def reset(self, info):
        self.previous = round_values(info)
        self.fighting = True
        self.number = 1
        self.wait_frames = 0
        self.match_end_frame = None
        self.match_won = False
        self.opponent_id = info["opponent_character_id"]
        self.stopped = False

    def advance(self, info, terminal=False):
        if self.stopped:
            return []
        values = round_values(info)
        previous, self.previous = self.previous, values
        events = []
        awarded = any(values[name] > previous[name]
                      for name in ("player_rounds_won", "opponent_rounds_won"))
        # Zero health is still alive in this ROM. Actual KOs use -1.
        knockout = info["health"] < 0 or info["enemy_health"] < 0
        # High byte is the BCD seconds; low byte is the countdown within a second.
        time_up = values["round_timer"] >> 8 == 0
        if self.fighting and (knockout or awarded or time_up or terminal):
            self.fighting = False
            self.wait_frames = 0
            events.append({"event": "round_finished", "round": self.number,
                           "reason": "knockout" if knockout else "time_up" if time_up else "round_result",
                           "health": info["health"], "enemy_health": info["enemy_health"]})
        if self.fighting:
            return events

        self.wait_frames += 1
        if (max(values["player_rounds_won"], values["opponent_rounds_won"]) >= 2 or
                terminal or values["continuetimer"] > 0):
            if self.match_end_frame is None:
                self.match_end_frame = self.wait_frames
                self.match_won = values["player_rounds_won"] >= 2 and values["opponent_rounds_won"] < 2
                events.append({"event": "match_finished",
                               "won": self.match_won, "opponent": character_name(self.opponent_id),
                               "player_rounds_won": values["player_rounds_won"],
                               "opponent_rounds_won": values["opponent_rounds_won"]})
        ready = (self.wait_frames > 1 and info["health"] == info["enemy_health"] == 176 and
                 info["player_status"] >> 8 == info["opponent_status"] >> 8 == 2 and
                 values["round_timer"] >> 8 > 0 and values["round_timer"] < previous["round_timer"])
        if self.match_end_frame is not None and not self.match_won:
            if terminal or self.wait_frames - self.match_end_frame >= round(3 * self.fps):
                events.append({"event": "rematch", "reason": "match_lost"})
        elif ready:
            new_match = self.match_end_frame is not None
            scores_reset = values["player_rounds_won"] == values["opponent_rounds_won"] == 0
            if not new_match or scores_reset:
                if values["player_character_id"] != 0 or values["opponent_character_id"] not in CHARACTERS:
                    self.stopped = True
                    events.append({"event": "transition_paused", "reason": "Unsupported fighter identity"})
                else:
                    self.fighting = True
                    self.number = 1 if new_match else self.number + 1
                    self.opponent_id = values["opponent_character_id"]
                    self.match_end_frame = None
                    self.match_won = False
                    if new_match:
                        events.append({"event": "match_started", "opponent": character_name(self.opponent_id)})
                    events.append({"event": "round_started", "round": self.number})
        # Leave enough neutral time for travel, intros and a bonus stage to expire.
        # Endings/unknown screens pause rather than silently throwing away progress.
        if not self.fighting and self.wait_frames >= round(120 * self.fps) and not self.stopped:
            self.stopped = True
            events.append({"event": "transition_paused", "reason": "No next fight detected — ending or transition needs attention"})
        return events
