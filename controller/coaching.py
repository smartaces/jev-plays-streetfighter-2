"""Deterministic, bounded coaching. No action selection, RAM writes or inference."""
from collections import Counter, deque
from .characters import opponent_profile
from .observation import grounded_ready, GROUND_Y
from .engagement import range_band

COACHING_VERSION = "attack-opportunities-v2"
PACE_SECONDS = 6
HISTORY_SECONDS = 10
BASE_FIREBALL_ALLOWANCE = 2
ZONING_FIREBALL_ALLOWANCE = 3


def family(action):
    if action == "fireball":
        return "projectile"
    if action == "crouch_kick_fireball":
        return "close_combination"
    if action == "dragon_punch":
        return "uppercut"
    if action == "hurricane_kick":
        return "travelling_attack"
    if action.startswith("throw_"):
        return "throw"
    if action.startswith("jump_") and action.endswith(("punch", "kick")):
        return "jump_entry"
    if action.startswith("air_"):
        return "air_attack"
    if action in {"punch", "heavy_punch", "kick", "crouching_punch", "crouching_kick", "sweep"}:
        return "close_attack"
    return None


def clock_seconds(raw):
    if not isinstance(raw, int) or isinstance(raw, bool) or not 0 <= raw <= 65535:
        return None
    high = raw >> 8
    tens, units = high >> 4, high & 15
    return tens * 10 + units if tens <= 9 and units <= 9 else None


class Coaching:
    def __init__(self, fps=60):
        self.fps = fps
        self.reset()

    def reset(self):
        self.frames = deque(maxlen=round(self.fps * HISTORY_SECONDS) + 1)
        self.attacks = deque(maxlen=256)
        self.inputs = deque(maxlen=256)
        self.spawns = deque(maxlen=256)
        self.frame = None
        self.identity = None
        self.projectile_active = None
        self.idle_ready_frames = 0
        self.last_ground_attack = None

    def observe(self, info, frame, execution):
        identity = (info.get("player_character_id"), info.get("opponent_character_id"))
        if self.frame is not None and (frame < self.frame or frame > self.frame + 1 or identity != self.identity):
            self.reset()
        if frame == self.frame:
            return
        self.frame, self.identity = frame, identity
        v = dict(info)
        self.frames.append((frame, v))
        active = True if v.get("player_projectile_flags") == 257 else False if v.get("player_projectile_flags") in (0, 256) else None
        if self.projectile_active is False and active is True:
            self.spawns.append(frame)
        self.projectile_active = active
        if v.get("opponent_y") == GROUND_Y and v.get("opponent_status") in (522, 524):
            self.last_ground_attack = frame
        no_threat = (v.get("opponent_projectile_flags") in (0, 256) and
                     v.get("opponent_y") == GROUND_Y and v.get("opponent_status") in (512, 514, 520, 526))
        passive = execution.get("action") in ("neutral", "retreat", "crouching_block", "crouch")
        if grounded_ready(v) and not execution.get("input_sequence_active") and no_threat and passive:
            self.idle_ready_frames += 1
        else:
            self.idle_ready_frames = 0
        oldest = frame - self.fps * HISTORY_SECONDS
        while self.attacks and self.attacks[0][0] <= oldest:
            self.attacks.popleft()
        while self.spawns and self.spawns[0] <= oldest:
            self.spawns.popleft()
        while self.inputs and self.inputs[0][0] <= oldest:
            self.inputs.popleft()

    def applied(self, action, frame):
        action = str(action)
        if self.frames:
            self.inputs.append((frame, action, self.frames[-1][1]))
        if family(action):
            self.attacks.append((frame, action))
            self.idle_ready_frames = 0

    def summary(self, profile):
        if not self.frames:
            return None
        now, latest = self.frames[-1]
        past = next(v for frame, v in self.frames if frame >= now - self.fps * PACE_SECONDS)
        hp_loss = max(0, max(0, past["health"]) - max(0, latest["health"]))
        damage = max(0, max(0, past["enemy_health"]) - max(0, latest["enemy_health"]))
        recent = [(f, a) for f, a in self.attacks if f > now - self.fps * PACE_SECONDS]
        fireballs = sum(a in {"fireball", "crouch_kick_fireball"} for _, a in recent)
        # Even useful zoning has a bounded cadence. A damaging exchange alone
        # does not justify sitting still and firing without limit.
        useful_zoning = profile["preferred_spacing"] in {"outside_grabs", "outside_active_specials"} and damage >= hp_loss + 8
        allowance = ZONING_FIREBALL_ALLOWANCE if useful_zoning else BASE_FIREBALL_ALLOWANCE
        last_attacks = list(self.attacks)[-8:]
        streak = 0
        for _, action in reversed(self.attacks):
            if action != "fireball":
                break
            streak += 1
        retry_in = 0
        shots = [f for f, a in recent if a in {"fireball", "crouch_kick_fireball"}]
        if len(shots) >= allowance:
            retry_in = round(max(0, shots[-allowance] + self.fps * PACE_SECONDS - now) / self.fps, 1)
        # Recovery/guard replies do not conceal repeated walking. A new attack,
        # expiry or round reset ends the passage; damage remains unattributed.
        passage = []
        for f, action, info in self.inputs:
            if f <= now - self.fps * PACE_SECONDS:
                continue
            if family(action):
                passage.clear()
            elif action == "approach":
                passage.append((f, info))
        first_frame, first = passage[0] if passage else (now, latest)
        elapsed = (now - first_frame) / self.fps
        passage_damage = max(0, max(0, first["enemy_health"]) - max(0, latest["enemy_health"]))
        passage_loss = max(0, max(0, first["health"]) - max(0, latest["health"]))
        feedback = "none"
        if len(passage) >= 3 and elapsed >= 1 and passage_damage == 0:
            feedback = "taking_damage_without_attacking" if passage_loss >= 8 else "walking_without_attacking"
        approach = {"attempts_since_attack": len(passage), "elapsed_seconds": round(elapsed, 1),
                    "distance_closed": abs(first["opponent_x"] - first["player_x"]) - abs(latest["opponent_x"] - latest["player_x"]),
                    "player_health_lost": passage_loss, "opponent_health_lost": passage_damage, "feedback": feedback}
        return {"window_seconds": PACE_SECONDS, "observed_seconds": round(min(PACE_SECONDS, (now - self.frames[0][0]) / self.fps), 1),
                "fireball_attempts": fireballs, "projectiles_spawned": sum(f > now - self.fps * PACE_SECONDS for f in self.spawns),
                "fireball_allowance": allowance, "fireball_pace": "ease_off" if fireballs >= allowance else "available",
                "pace_recheck_seconds": retry_in, "consecutive_fireball_attacks": streak,
                "last_attack_mix": dict(Counter(family(a) for _, a in last_attacks)),
                "player_health_lost": hp_loss, "opponent_health_lost": damage,
                "approach": approach,
                "ready_passive_seconds": round(self.idle_ready_frames / self.fps, 1)}

    def context(self, state, info, ruleset):
        player, opponent = state.get("player", {}), state.get("opponent", {})
        profile = opponent_profile(opponent.get("character", "unknown"), ruleset)
        enemy_shot = state.get("projectiles", {}).get("opponent", {})
        if profile["has_projectile"] is False and enemy_shot.get("active") is True:
            profile.update(has_projectile=None, capability_conflict=True)
        result = {k: v for k, v in state.items() if k != "matchup_advice"}
        result["opponent_profile"] = profile
        seconds = clock_seconds(info.get("round_timer"))
        hp, enemy_hp = player.get("health"), opponent.get("health")
        health = round(max(0, hp) / 176 * 100) if isinstance(hp, (int, float)) else None
        enemy_health = round(max(0, enemy_hp) / 176 * 100) if isinstance(enemy_hp, (int, float)) else None
        result["round"] = {"clock_seconds": seconds, "player_health_percent": health,
                           "opponent_health_percent": enemy_health}
        preference = "balanced" if health is None else "careful_counter" if health <= 30 else "pressure" if health >= 65 else "balanced"
        if seconds is not None and seconds <= 20 and health is not None and enemy_health is not None:
            if enemy_health - health >= 20:
                preference = "must_score"
            elif health - enemy_health >= 20:
                preference = "protect_lead"
        history = self.summary(profile)
        if history is not None:
            result["recent_tactics"] = history
        ready = player.get("ground_action_ready") is True
        enemy_ground = opponent.get("body_position") == "grounded"
        distance = state.get("distance")
        band = range_band(distance)
        close = band == "close"
        normal_range = band in {"close", "poke"}
        enemy_air = opponent.get("body_position") == "airborne"
        closing_air = enemy_air and opponent.get("horizontal_motion") == "towards_opponent"
        forecast = opponent.get("airborne_distance_after_reply_estimate")
        near_air = enemy_air and ((isinstance(distance, (int, float)) and distance <= 70) or closing_air
                                 or (isinstance(forecast, (int, float)) and forecast <= 70))
        ground_attack = enemy_ground and opponent.get("state") in ("attack_motion", "special_motion")
        own_shot = state.get("projectiles", {}).get("player", {})
        ease_off = history is not None and history["fireball_pace"] == "ease_off"
        cue, hint = "reassess", "Use current threats and reach. Seek an attack or useful ground when ready."
        if state.get("controls", {}).get("input_sequence_active"):
            cue, hint = "committed", "Let the current inputs finish; choose neutral."
        elif player.get("body_position") == "airborne":
            cue, hint = "airborne", "Ryu cannot guard airborne. Use an air attack only if air_attack_ready_estimate is true; otherwise wait to land."
        elif enemy_shot.get("incoming") is True:
            cue, hint = "projectile_defence", "Guard the incoming projectile. Consider a jump only with enough takeoff time; high health does not justify taking a hit."
        elif near_air:
            if ready and closing_air and opponent.get("vertical_motion") == "rising":
                cue, hint = "intercept_jump", "Opponent is rising towards you. Prefer dragon_punch if its reach and reply delay allow interception; stand-guard with retreat if too late. No fireball or low sweep."
            else:
                cue, hint = "air_defence", "Watch the airborne approach. Stand-guard with retreat when grounded; a ready uppercut needs time and reach. Do not crouch-block an air attack."
        elif ground_attack:
            projected = state.get("engagement", {}).get("distance_after_reply_estimate")
            near = isinstance(distance, (int, float)) and distance <= 100
            near = near or isinstance(projected, (int, float)) and projected <= 100
            cue, hint = ("ground_defence", "Opponent is attacking on the ground. Guard if it can reach during the reply delay; counter when ready after it ends. Do not walk into the attack.") if near else (
                "ground_attack_observed", "Opponent is attacking at a distance. Judge its reach and motion; hold useful space or use a safe ranged opening rather than walking into it. An attack animation is not proof of recovery.")
        elif not ready:
            cue, hint = "recover", "No new ground attack while readiness is false or unknown. Wait without inventing an opening."
        elif enemy_ground and normal_range and opponent.get("state") == "guard_posture":
            cue, hint = "challenge_guard", "Close grounded guard: try a throw only at touching distance; otherwise use low-kick pressure. Avoid another stationary fireball."
        elif enemy_ground and normal_range and opponent.get("state") in ("neutral_or_walking", "crouching", "hit_or_block_reaction"):
            ended = self.frame is not None and self.last_ground_attack is not None and self.frame - self.last_ground_attack <= self.fps * .5
            cue = "counter_opportunity" if ended else "close_pressure" if close else "poke_opportunity"
            hint = ("A quick punch or low kick can contest this close grounded opponent; challenge a guard with a touching throw."
                    if close else "A medium punch or crouching kick can contest this gap; sweep an exposed grounded opponent. Consider where the opponent will be after the reply delay, rather than walking until touching.")
            hint += " A reaction does not guarantee a free hit."
        elif enemy_air:
            cue, hint = "track_landing", "Opponent is airborne away from immediate contact. Position for an uppercut or attack the landing when reachable; do not chase underneath blindly or sweep an airborne target."
        elif band == "unknown" or opponent.get("state") == "unknown" or not enemy_ground or enemy_shot.get("active") is None:
            cue, hint = "uncertain", "Threat information is incomplete. Reassess known position and guard needs; do not assume an opening."
        elif ease_off or own_shot.get("active") is True:
            cue = "change_pressure" if ease_off else "follow_projectile"
            hint = ("Ease off fireballs. " if ease_off else "Your projectile is already active. ") + (
                "Contest space outside grabs or active specials with a reachable poke or interception. Movement needs a purpose."
                if profile["preferred_spacing"] in {"outside_grabs", "outside_active_specials"} else
                "Consider a reachable jump attack, hurricane kick or ground poke. Walk only if it improves the next attack; do not simply wait for another fireball.")
        else:
            cue = "attack_options"
            if profile["preferred_spacing"] in {"outside_grabs", "outside_active_specials"}:
                hint = "A paced fireball or reachable poke can contest space outside grabs/active specials. Intercept an approaching jump; entry is optional."
            elif band == "entry":
                hint = "Hurricane kick can attack across this gap; forward jump kick attacks from above; a fireball challenges grounded movement. Compare their reach and the opponent's response. Walking is setup, not an attack."
            else:
                hint = "A fireball can attack at range. A jump attack or short approach can set up closer contact if travel is useful. Do not require close range before attempting damage."
        offensive = cue in {"attack_options", "close_pressure", "poke_opportunity", "counter_opportunity", "change_pressure", "follow_projectile", "track_landing", "challenge_guard"}
        if offensive:
            hint += {"pressure": " Seek a damage opportunity, not just forward movement.",
                     "careful_counter": " Low health: favour a brief poke, safe shot or punish; do not chase into danger.",
                     "protect_lead": " Protect the lead with controlled spacing and brief attacks; entry is optional.",
                     "must_score": " Time is short and you trail: seek damage, accepting a reasonable attack risk.",
                     "balanced": ""}[preference]
            if history and history["approach"]["feedback"] != "none":
                hint += " Repeated walking has produced no attack or damage. Reassess an attack that can reach, or defend a threat; do not repeat movement automatically."
            # Pacing still applies to close combos; it is never a generic ban on shots.
            if ease_off and cue not in {"change_pressure", "follow_projectile"}:
                hint += " Recent fireballs are frequent: consider a useful non-projectile attack."
        result["coaching"] = {"version": COACHING_VERSION, "source": "controller_rules", "preference": preference,
                              "cue": cue, "hint": hint}
        return result
