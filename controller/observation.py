from collections import deque
from numbers import Integral
from .characters import character_name
from .contracts import ActionId

OBSERVATION_VERSION = "sf2-controls-v4"
GROUND_Y = 192
NORMAL_REACH = 45  # Conservative centre-distance heuristic, not a hitbox measurement.
STATUS = {512: "neutral_or_walking", 514: "crouching", 516: "jump_motion",
          520: "guard_posture", 522: "attack_motion", 524: "special_motion",
          526: "hit_or_block_reaction"}
GROUND_ACTIONS = set(ActionId) - {ActionId.NEUTRAL, ActionId.APPROACH, ActionId.RETREAT,
    ActionId.BLOCK, ActionId.CROUCH, ActionId.AIR_PUNCH, ActionId.AIR_KICK}


def projectile(values, who):
    prefix = who + "_projectile_"
    flags = values.get(prefix + "flags")
    # 0x0100 is a despawn transition. Unknown combinations are not active facts.
    active = True if flags == 0x0101 else False if flags in (0, 0x0100) else None
    result = {"active": active, "flags_raw": flags}
    if active is True:
        result.update(x=values[prefix + "x"], y=values[prefix + "y"],
                      vx_per_frame=round(values[prefix + "vx"] / 256, 3))
    return result


def grounded_ready(values):
    code = values.get("player_status")
    if code not in STATUS:
        return None
    return code in (512, 514) and values["player_y"] == GROUND_Y


class InvalidState(ValueError):
    pass


class ObservationBuilder:
    def __init__(self, fps=60):
        self.fps = fps
        self.history = deque(maxlen=120)
        self.latest = None
        self.motion = deque(maxlen=7)
        self.fireball_recovery = False

    def clear(self):
        self.history.clear()
        self.motion.clear()
        self.fireball_recovery = False

    def update(self, info, now):
        values = {}
        limits = {"health": (-512, 176), "enemy_health": (-512, 176),
                  "player_character_id": (0, 255), "opponent_character_id": (0, 255),
                  "player_x": (0, 65535), "opponent_x": (0, 65535),
                  "player_y": (0, 65535), "opponent_y": (0, 65535),
                  "player_status": (0, 65535), "opponent_status": (0, 65535),
                  "player_move_timer": (0, 255)}
        for who in ("player", "opponent"):
            for field in ("flags", "x", "y", "vx"):
                limits[f"{who}_projectile_{field}"] = (-32768, 32767) if field == "vx" else (0, 65535)
        for name, (low, high) in limits.items():
            value = info.get(name)
            if isinstance(value, bool) or not isinstance(value, Integral) or not low <= value <= high:
                raise InvalidState(f"Invalid or missing game value: {name}")
            values[name] = int(value)
        values["continuetimer"] = int(info.get("continuetimer", 0))
        if values["player_status"] != 524:
            self.fireball_recovery = False
        elif self.motion and (projectile(self.latest, "player")["active"] is False and
                              projectile(values, "player")["active"] is True):
            # Identify recovery from an observed spawn, not a requested move.
            self.fireball_recovery = True
        self.latest = values
        self.motion.append(values.copy())
        self.history.append((now, values.copy()))
        while len(self.history) > 1 and self.history[0][0] < now - 0.5:
            self.history.popleft()
        return values

    def fighter(self, who):
        v = self.latest
        code = v[who + "_status"]
        vx = vy = None
        if len(self.motion) > 1:
            # Each update is one emulated frame; wall-clock catch-up is not velocity.
            old, frames = self.motion[0], len(self.motion) - 1
            vx = round((v[who + "_x"] - old[who + "_x"]) / frames, 2)
            vy = round((v[who + "_y"] - old[who + "_y"]) / frames, 2)
        above_ground = v[who + "_y"] < GROUND_Y
        result = {"character": character_name(v[who + "_character_id"]),
                  "health": v["health" if who == "player" else "enemy_health"],
                  "full_health": 176, "x": v[who + "_x"], "y": v[who + "_y"],
                  "state": STATUS.get(code, "unknown"), "status_raw": code,
                  "above_ground": above_ground, "jumping": code == 516 and above_ground,
                  "vx_per_frame": vx, "vy_per_frame": vy}
        if who == "player":
            result["ground_action_ready"] = grounded_ready(v)
            timer = v["player_move_timer"]
            result["fireball_recovery_frames"] = timer if self.fireball_recovery and timer <= 40 else None
        else:
            result["jump_towards_player"] = (vx * (v["player_x"] - v["opponent_x"]) > 0.1
                                             if result["jumping"] and vx is not None
                                             else None if result["jumping"] else False)
        return result

    def model_state(self, current_action, frames_remaining, last_action, decision_delay_frames=None, execution=None):
        if self.latest is None:
            raise InvalidState("No game observation is available.")
        v = self.latest
        separation = v["opponent_x"] - v["player_x"]
        recent = None
        if len(self.history) > 1:
            start, old = self.history[0]
            end, _ = self.history[-1]
            recent = {"interval_ms": round((end - start) * 1000),
                      "distance_change": abs(separation) - abs(old["opponent_x"] - old["player_x"]),
                      "player_health_change": v["health"] - old["health"],
                      "opponent_health_change": v["enemy_health"] - old["enemy_health"]}
        own, enemy = projectile(v, "player"), projectile(v, "opponent")
        enemy.update(incoming=None, distance=None, frames_to_player_x_estimate=None)
        if enemy["active"] is False:
            enemy["incoming"] = False
        elif enemy["active"] is True:
            dx, speed = v["player_x"] - enemy["x"], enemy["vx_per_frame"]
            enemy["distance"] = abs(dx)
            enemy["incoming"] = dx * speed > 0 or abs(dx) <= 16
            if dx * speed > 0:
                enemy["frames_to_player_x_estimate"] = round(abs(dx / speed), 1)
        player, opponent = self.fighter("player"), self.fighter("opponent")
        execution = execution or {"action": str(current_action), "input_sequence_active": False,
                                  "phase": "unavailable", "frames_remaining_upper_bound": frames_remaining}
        player["observed_ground_ready"] = player["ground_action_ready"]
        if execution["input_sequence_active"]:
            player["ground_action_ready"] = False
        player["air_attack_ready_estimate"] = (player["jumping"] and not execution["input_sequence_active"])
        delay = round(self.fps * 0.35) if decision_delay_frames is None else decision_delay_frames
        opponent["jump_in_distance_after_reply_estimate"] = None
        if opponent["jump_towards_player"] is True and player["vx_per_frame"] is not None:
            opponent["jump_in_distance_after_reply_estimate"] = round(abs(
                separation + (opponent["vx_per_frame"] - player["vx_per_frame"]) * delay), 1)
        return {
            "observation_version": OBSERVATION_VERSION,
            "player": player, "opponent": opponent, "decision_delay_frames_estimate": delay,
            "projectiles": {"player": own, "opponent": enemy},
            "distance": abs(separation), "signed_separation": separation,
            "spacing": "close" if abs(separation) <= NORMAL_REACH else "outside_basic_attack_reach",
            "controls": execution,
            "opponent_side": "right" if separation > 0 else "left" if separation < 0 else "unknown",
            "recent": recent, "current_action": current_action,
            "input_frames_remaining": frames_remaining, "last_attempted_action": last_action,
        }


def action_state_rejection(action, values):
    """Recheck an unstarted action's readiness, without a tactical range veto."""
    if action in {"air_punch", "air_kick"} and not (
            values.get("player_status") == 516 and values["player_y"] < GROUND_Y):
        return "air_attack_window_passed"
    if str(action).startswith("jump_") and values["player_y"] < GROUND_Y:
        return "already_airborne"
    if action in GROUND_ACTIONS and grounded_ready(values) is not True:
        return "ground_action_window_passed"
    return None
