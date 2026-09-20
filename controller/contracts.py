from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ActionId(StrEnum):
    NEUTRAL = "neutral"
    APPROACH = "approach"
    RETREAT = "retreat"
    JUMP = "jump_forward"
    PUNCH = "punch"
    KICK = "crouching_kick"
    BLOCK = "crouching_block"
    HEAVY_PUNCH = "heavy_punch"
    SWEEP = "sweep"
    FIREBALL = "fireball"
    DRAGON = "dragon_punch"
    HURRICANE = "hurricane_kick"
    STANDING_KICK = "kick"
    CROUCH_PUNCH = "crouching_punch"
    CROUCH = "crouch"
    JUMP_BACK = "jump_back"
    JUMP_UP = "jump_up"
    JUMP_FORWARD_PUNCH = "jump_forward_punch"
    JUMP_FORWARD_KICK = "jump_forward_kick"
    JUMP_BACK_PUNCH = "jump_back_punch"
    JUMP_BACK_KICK = "jump_back_kick"
    JUMP_UP_PUNCH = "jump_up_punch"
    JUMP_UP_KICK = "jump_up_kick"
    AIR_PUNCH = "air_punch"
    AIR_KICK = "air_kick"
    THROW_FORWARD = "throw_forward"
    THROW_BACK = "throw_back"
    KICK_FIREBALL = "crouch_kick_fireball"


class Strength(StrEnum):
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"


@dataclass(frozen=True)
class Snapshot:
    run_id: str
    episode: int
    epoch: int
    frame: int
    captured: float
    state: dict[str, Any]
    observation: dict[str, Any] | None = None


@dataclass(frozen=True)
class RequestJob:
    request_id: int
    snapshot: Snapshot


@dataclass(frozen=True)
class DecisionResult:
    request_id: int
    snapshot: Snapshot
    started: float
    received: float
    next_eligible: float
    outcome: str
    action: str | None = None
    confidence: float | None = None
    probabilities: dict[str, float] | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    fatal: bool = False
    attempted: bool = True
    strength: str = "light"
    strength_confidence: float | None = None
    strength_probabilities: dict[str, float] | None = None
    diagnostics: dict[str, Any] | None = None
    diagnostic_errors: dict[str, str] | None = None


def rejection_reason(result, run_id, episode, epoch, now, max_age):
    if (result.snapshot.run_id, result.snapshot.episode, result.snapshot.epoch) != (run_id, episode, epoch):
        return "old_state"
    if result.outcome != "ok":
        return result.outcome
    if result.action not in ActionId:
        return "unknown_action"
    age = now - result.snapshot.captured
    if age < 0 or age > max_age:
        return "stale"
    return None
