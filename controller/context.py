"""Compact decision input; detailed observations remain available in run logs."""

from .characters import MATCHUP_TIPS
from .observation import GROUND_Y
from .engagement import engagement

CONTEXT_VERSION = "sf2-tactical-context-v6"


def _number(value):
    import math
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _fighter(fighter, other):
    """Translate geometry, not tactics. Raw measurements stay in snapshot.observation."""
    result = {k: v for k, v in fighter.items() if k not in {
        "status_raw", "observed_ground_ready", "x", "y", "vx_per_frame", "vy_per_frame",
        "above_ground", "jumping", "jump_towards_player", "jump_in_distance_after_reply_estimate",
    }}
    y = fighter.get("y")
    body = ("airborne" if y < GROUND_Y else "grounded" if y == GROUND_Y else "unknown") if _number(y) else fighter.get("body_position", "unknown")
    result["body_position"] = body
    vy = fighter.get("vy_per_frame")
    result["vertical_motion"] = ("on_ground" if body == "grounded" else
        ("descending" if vy > 0 else "rising" if vy < 0 else "level") if body == "airborne" and _number(vy)
        else fighter.get("vertical_motion", "unknown"))
    x, other_x, vx = fighter.get("x"), other.get("x"), fighter.get("vx_per_frame")
    if all(_number(v) for v in (x, other_x, vx)):
        result["horizontal_motion"] = ("stationary" if vx == 0 else "aligned" if x == other_x
            else "towards_opponent" if vx * (other_x - x) > 0 else "away_from_opponent")
    else:
        result["horizontal_motion"] = fighter.get("horizontal_motion", "unknown")
    return result


def decision_context(state):
    """Idempotent model view, including for saved observations and sparse checks."""
    result = {key: value for key, value in state.items() if key not in {
        "observation_version", "spacing", "signed_separation",
        "current_action", "input_frames_remaining",
    }}
    if "spacing" in state:
        # Preserve the existing rough close cue without claiming a universal reach.
        result["spacing"] = "separated" if state["spacing"] == "outside_basic_attack_reach" else state["spacing"]
    for who in ("player", "opponent"):
        if who in result:
            result[who] = _fighter(state[who], state.get("opponent" if who == "player" else "player", {}))
    player, opponent = state.get("player", {}), state.get("opponent", {})
    numbers = [opponent.get("x"), player.get("x"), opponent.get("vx_per_frame"), player.get("vx_per_frame")]
    if all(_number(v) for v in numbers):
        dx, relative_vx = numbers[0] - numbers[1], numbers[2] - numbers[3]
        result["separation_motion"] = "steady" if relative_vx == 0 else "aligned" if dx == 0 else "closing" if dx * relative_vx < 0 else "opening"
        delay = state.get("decision_delay_frames_estimate")
        result["engagement"] = engagement(state.get("distance"),
            round(abs(dx + relative_vx * delay), 1) if _number(delay) and delay >= 0 else None)
        if "opponent" in result:
            result["opponent"]["airborne_distance_after_reply_estimate"] = (
                round(abs(dx + relative_vx * delay), 1)
                if result["opponent"]["body_position"] == "airborne" and _number(delay) else None)
    else:
        result.setdefault("separation_motion", "unknown")
        result.setdefault("engagement", engagement(state.get("distance")))
    if "projectiles" in result:
        result["projectiles"] = {
            who: {key: value for key, value in projectile.items() if key != "flags_raw"}
            for who, projectile in result["projectiles"].items()
        }
    if "controls" in result:
        result["controls"] = {key: value for key, value in result["controls"].items()
                              if key in {"action", "strength", "input_sequence_active"}}
    name = result.get("opponent", {}).get("character")
    if "opponent_profile" in result:
        # The application freezes the coaching/profile into each request once.
        # Worker-side normalisation must neither recompute history nor duplicate tips.
        result.pop("matchup_advice", None)
    else:
        result["matchup_advice"] = MATCHUP_TIPS.get(name,
            "Opponent identity unknown; use the general guide and observed threats.")
    return result
