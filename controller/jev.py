import asyncio
from contextlib import suppress
import math
import multiprocessing as mp
from queue import Empty, Full
import time

from .contracts import ActionId, Strength, DecisionResult
from .guide import fighting_guide
from .context import decision_context

QUESTION_VERSION = "sf2-attack-opportunities-v16"


def diagnostic_questions():
    """Independent observations for review, never gates on the chosen move."""
    return {
        "main_threat": {
            "type": "choice",
            "instructions": "What is the most immediate visible threat to Ryu? Judge current state, not the opponent's reputation. Choose unclear when evidence is insufficient. These are diagnostic labels, not commands.",
            "criteria": {
                "incoming_projectile": "An active opponent projectile is incoming towards Ryu; compare its arrival estimate with decision delay.",
                "airborne_threat": "Opponent body_position is airborne, attacking nearby or approaching Ryu. Airborne attack_motion is an air threat even though it is not jump_motion. An airborne reaction alone does not prove an attack.",
                "nearby_ground_attack": "Opponent is grounded, near Ryu, and in an attacking or special-move state. A reaction or guard alone is not an attack.",
                "none_visible": "No incoming projectile, nearby attack or threatening jumping approach is visible. This does not guarantee safety.",
                "unclear": "Missing or unknown state prevents identifying the immediate threat.",
            },
        },
        "opening": {
            "type": "choice",
            "instructions": "What attacking or positioning opportunity is best supported now? Judge from state independently of next_action and main_threat; their answers are unavailable. Do not infer dizzy, exact recovery or guaranteed hits.",
            "criteria": {
                "close_attack": "Ready Ryu in engagement.range_band close or poke can attempt a ground normal against a grounded idle, crouching or reacting opponent. Poke range can favour medium punch or low kick. A touching guard belongs under throw_pressure. Reaction does not prove helplessness.",
                "throw_pressure": "Ready Ryu is touching a grounded guarding opponent: a throw attempt can challenge the guard.",
                "advancing_attack": "Ready Ryu can plausibly close a modest gap and hit with a travelling kick or jump attack, without an immediate visible threat.",
                "ranged_attack": "Ready Ryu has a grounded opponent at a useful projectile distance, no own active projectile or immediate threat, and fireball_pace is not ease_off. A ranged attack does not require movement first.",
                "reposition": "Ryu can move, but the gap or pursuit makes gaining or leaving space more useful than an immediate attack.",
                "defend_or_wait": "An immediate threat, committed inputs or lack of readiness makes defence or waiting the current priority.",
                "unclear": "The observations do not support a clear opening or positioning opportunity.",
            },
        },
    }


def question_spec(settings):
    return {
        "type": "choice",
        "instructions": {
            "question": "Which action offers useful damage or defence now? Compare attacks that can reach with movement that sets one up. Being separated does not mean walk. Use live facts first; coaching is advisory. Reconsider repeated actions when they achieve no attack or damage. Judge independently of diagnostic answers.",
            "focus": ["`controls`", "`player.ground_action_ready`", "`player.air_attack_ready_estimate`",
                      "`opponent.body_position`", "`opponent.state`", "`projectiles`", "`distance`", "`engagement`", "`separation_motion`",
                      "`recent_tactics`", "`round`", "`opponent_profile`", "`coaching`"],
            "readiness": "Committed input sequence: neutral. Start a ground attack or jump only when player.ground_action_ready=true. If readiness is false or null, do not start an attack: choose neutral with no threat; when Ryu is grounded, hold retreat against an airborne attack or crouching_block against a ground threat. Recovery may delay when guard takes effect. The only attack exception is air_attack_ready_estimate=true, which permits air_punch or air_kick during Ryu's existing jump. Ryu cannot block airborne.",
            "fighting_guide": fighting_guide(),
            "examples": [
                {"choose": "punch", "when": "Ready Ryu; grounded idle opponent at distance 20, steady separation, no threat.", "why": "A quick close normal can connect."},
                {"choose": "crouching_kick", "when": "Ready Ryu; grounded opponent at distance 50, not attacking or moving away.", "why": "Try a medium low poke; no need to walk until touching."},
                {"choose": "hurricane_kick", "when": "Ready Ryu; grounded idle opponent at distance 100; repeated approaches without attacking; no projectile.", "why": "Attack while crossing the gap, rather than another empty walk."},
                {"choose": "jump_forward_kick", "when": "Ready Ryu; opponent at distance 120, a forward jump can reach the landing, no immediate interception visible.", "why": "An attacking entry is a possible risk worth taking, not just movement."},
                {"choose": "fireball", "when": "Ready Ryu at low health; Guile grounded at distance 170; no threat, own projectile or recent fireball excess.", "why": "Attack at range instead of chasing; projectile capability alone is not a threat."},
                {"choose": "crouching_block", "when": "Guile is attacking on the ground at distance 60, within possible striking reach during the reply delay.", "why": "Respect the attack even though distance exceeds the old close cutoff."},
                {"choose": "dragon_punch", "when": "Ready Ryu; opponent rising towards Ryu with time and reach to intercept.", "why": "An early uppercut can interrupt the jump."},
                {"choose": "approach", "when": "Ready Ryu at distance 220; own projectile active; no incoming attack; a short walk improves the next attack's reach.", "why": "Movement sets up a later attack; reassess instead of walking indefinitely."},
                {"choose": "crouch_kick_fireball", "when": "Ready after landing; grounded opponent within low-kick reach; no own projectile; fireball_pace=available."}
            ]
        },
        "criteria": {
            'neutral': {
                'what': 'Wait without starting an attack or movement.',
                'use_when': 'Inputs committed or readiness unknown/recovering; no useful guard or air attack.',
                'risk': 'Waiting while ready gives up opportunities.',
            },
            'approach': {
                'what': f'Walk towards opponent for {settings.movement_frames} frames.',
                'use_when': 'A short reposition improves the next attack. Compare ranged and travelling attacks before walking; repeated approach feedback calls for reassessment, not another automatic walk.',
                'risk': 'Walking offers no guard.',
            },
            'retreat': {
                'what': 'Hold away: walk back or stand-block, up to 0.6s.',
                'use_when': 'Create space or guard a jumping attack.',
                'risk': 'Low attacks beat standing guard.',
            },
            'crouch': {
                'what': 'Hold down.',
                'use_when': "Lower Ryu's stance without moving away.",
                'risk': 'Crouching alone does not block.',
            },
            'crouching_block': {
                'what': 'Hold down-away, up to 0.6s.',
                'use_when': 'Guard a ground projectile or a nearby grounded attack. For a nearby airborne attack choose retreat, which holds standing guard.',
                'risk': 'Jumping attacks beat crouching guard.',
            },
            'jump_forward': {
                'what': 'Jump towards opponent without attacking.',
                'use_when': 'Cross a gap or an early projectile.',
                'risk': 'Cannot block in the air.',
            },
            'jump_back': {
                'what': 'Jump away without attacking.',
                'use_when': 'Escape close pressure with room behind Ryu.',
                'risk': 'Gives up space; cannot block airborne.',
            },
            'jump_up': {
                'what': 'Jump vertically without attacking.',
                'use_when': 'Clear an early projectile while holding position.',
                'risk': 'Needs takeoff time; cannot block airborne.',
            },
            'jump_forward_punch': {
                'what': 'Jump forward, then automatically punch airborne.',
                'use_when': 'Close space and attack near the landing.',
                'risk': 'Opponent can intercept the jump.',
            },
            'jump_forward_kick': {
                'what': 'Jump forward, then automatically kick airborne.',
                'use_when': 'Enter with a kick from above when the landing can reach the opponent; can cross an early projectile with enough takeoff time.',
                'risk': 'Opponent can intercept the jump.',
            },
            'jump_back_punch': {
                'what': 'Jump away, then automatically punch airborne.',
                'use_when': 'Challenge a nearby pursuer while retreating.',
                'risk': 'Misses if separation grows too large.',
            },
            'jump_back_kick': {
                'what': 'Jump away, then automatically kick airborne.',
                'use_when': 'Kick a pursuer while creating space.',
                'risk': 'May miss; gives up ground.',
            },
            'jump_up_punch': {
                'what': 'Jump vertically, then automatically punch airborne.',
                'use_when': 'Challenge a nearby opponent without advancing.',
                'risk': 'Cannot reach distant opponents.',
            },
            'jump_up_kick': {
                'what': 'Jump vertically, then automatically kick airborne.',
                'use_when': 'Attack nearby while holding horizontal position.',
                'risk': 'Opponent can attack the landing.',
            },
            'punch': {
                'what': 'Standing punch.',
                'use_when': 'Challenge a nearby grounded opponent. Light suits close contact; medium can contest a larger poke gap. Compare the projected distance, not just an old close label.',
                'risk': 'Misses outside punch reach.',
            },
            'kick': {
                'what': 'Standing kick.',
                'use_when': 'Strike a grounded opponent within kick reach.',
                'risk': 'Reach and recovery vary with strength.',
            },
            'crouching_punch': {
                'what': 'Punch while crouching.',
                'use_when': 'Quick close attack from a low stance.',
                'risk': 'Limited reach; does not guard.',
            },
            'crouching_kick': {
                'what': 'Kick low; heavy strength sweeps.',
                'use_when': 'Contest a grounded opponent in close or poke range, including a standing guard. Medium low kick reaches beyond touching distance. It cannot hit an airborne target merely because horizontal distance is small.',
                'risk': 'Low guard can block it.',
            },
            'heavy_punch': {
                'what': 'Standing heavy punch, fixed strength.',
                'use_when': 'Strong close attack with time to connect.',
                'risk': 'A miss gives opponent time to respond.',
            },
            'sweep': {
                'what': 'Crouching heavy kick to knock down.',
                'use_when': 'Opponent body_position must be grounded: sweep nearby idle or reacting legs. An airborne reaction is not a sweep target. At touching distance against guard, prefer a throw.',
                'risk': 'Can be blocked low; vulnerable if missed.',
            },
            'air_punch': {
                'what': "Punch during Ryu's existing jump.",
                'use_when': 'air_attack_ready_estimate=true and opponent within reach.',
                'risk': 'Misses if timed too early or late.',
            },
            'air_kick': {
                'what': "Kick during Ryu's existing jump.",
                'use_when': 'air_attack_ready_estimate=true and opponent within reach.',
                'risk': 'Misses if timed too early or late.',
            },
            'throw_forward': {
                'what': 'Throw a touching opponent towards the side Ryu faces.',
                'use_when': 'Touching a grounded guarding opponent: a throw challenges guard directly, unlike a punch or sweep.',
                'risk': 'May produce a heavy punch if the grab fails.',
            },
            'throw_back': {
                'what': 'Throw a touching opponent behind Ryu.',
                'use_when': 'Grab through guard and exchange sides.',
                'risk': 'May produce a heavy punch if the grab fails.',
            },
            'crouch_kick_fireball': {
                'what': 'Crouching medium kick immediately into fireball.',
                'use_when': 'Grounded opponent in kick reach; no own projectile and fireball_pace is not ease_off. Includes one fireball attempt; use a single low kick when pacing calls for fewer projectiles.',
                'risk': 'Commits both inputs even if kick misses.',
            },
            'fireball': {
                'what': 'Stay in place and launch a projectile towards opponent.',
                'use_when': 'Paced ranged pressure against a grounded opponent, with no own projectile and fireball_pace=available (or no pacing data). At ease_off, choose useful movement or a different reachable attack; never wait just for the allowance to refill.',
                'risk': 'A nearby opponent can jump over and attack during recovery.',
            },
            'dragon_punch': {
                'what': 'Rise with an uppercut; knock down on a successful air interception.',
                'use_when': 'Ready Ryu facing an approaching jump: prefer this interception when the enemy is rising into reach and the reply delay permits it. Standing guard is the fallback for late or unavailable interception.',
                'risk': 'Missing leaves Ryu exposed while landing.',
            },
            'hurricane_kick': {
                'what': 'Whirlwind kick: travel forward spinning and kicking; knock down on hit.',
                'use_when': 'Attack across an entry gap against a grounded opponent, especially when repeated walking has not produced an attack. A close normal is quicker when already in reach; consider opponent movement and guard.',
                'risk': 'An opponent who blocks or avoids it may counterattack.',
            },
        },
    }


def questions_spec(settings):
    return {"next_action": question_spec(settings), "attack_strength": {
        "type": "choice",
        "instructions": "Which attack strength fits the distance, timing and risk? Decide independently of next_action. Light is useful at close contact; poke/entry gaps often call for medium rather than a short light jab. Low health does not make an out-of-range light attack useful. Explicit heavy moves and throws stay heavy; the combo starts with medium kick.",
        "criteria": {"light": "Quick normal at close contact, or a special whose shorter commitment/travel suits this position.",
                     "medium": "Contest a poke gap with a medium punch/low kick, or cover an entry gap with a travelling attack. Balance useful reach and commitment.",
                     "heavy": "A strong normal or jump attack with time to connect, or a faster projectile. More commitment can leave Ryu exposed."}},
            **diagnostic_questions()}


def parse_diagnostics(response):
    answers, errors = {}, {}
    for name, spec in diagnostic_questions().items():
        answer = response.choices.get(name)
        if answer is None:
            errors[name] = "not_returned"
            continue
        probabilities = answer.probabilities
        if (answer.choice not in spec["criteria"] or set(probabilities) != set(spec["criteria"]) or
            any(not math.isfinite(v) or not 0 <= v <= 1 for v in probabilities.values()) or
            abs(sum(probabilities.values()) - 1) > 0.02 or
            not math.isfinite(answer.confidence) or not 0 <= answer.confidence <= 1):
            errors[name] = "invalid_answer"
            continue
        answers[name] = {"choice": answer.choice, "confidence": answer.confidence,
                         "probabilities": probabilities}
    return answers, errors


def parse_response(response):
    answer = response.choices.get("next_action")
    if answer is None or answer.choice not in ActionId:
        raise ValueError("No valid next_action choice was returned.")
    probabilities = answer.probabilities
    if (set(probabilities) != set(ActionId) or
        any(not math.isfinite(v) or not 0 <= v <= 1 for v in probabilities.values()) or
        abs(sum(probabilities.values()) - 1) > 0.02 or
        not math.isfinite(answer.confidence) or not 0 <= answer.confidence <= 1):
        raise ValueError("The response probabilities are invalid.")
    strength = response.choices.get("attack_strength")
    if strength is None or strength.choice not in Strength or set(strength.probabilities) != set(Strength):
        raise ValueError("No valid attack_strength choice was returned.")
    if (any(not math.isfinite(v) or not 0 <= v <= 1 for v in strength.probabilities.values()) or
        abs(sum(strength.probabilities.values()) - 1) > 0.02 or
        not math.isfinite(strength.confidence) or not 0 <= strength.confidence <= 1):
        raise ValueError("The strength response probabilities are invalid.")
    diagnostics, diagnostic_errors = parse_diagnostics(response)
    return {"action": answer.choice, "confidence": answer.confidence,
            "strength": strength.choice, "strength_confidence": strength.confidence,
            "strength_probabilities": strength.probabilities,
            "probabilities": probabilities, "model": response.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "diagnostics": diagnostics, "diagnostic_errors": diagnostic_errors}


async def request_choice(client, state, settings, timeout=None):
    async with asyncio.timeout(timeout or settings.request_timeout):
        response = await client.system_one(
            state=decision_context(state), questions=questions_spec(settings), model=settings.model)
    return parse_response(response)


def classify_error(error):
    from typesafe_sdk import TypeSafeAPIError, TypeSafeAPIConnectionError
    if isinstance(error, TypeSafeAPIError):
        status = error.status
        if status in (400, 401, 403, 404, 422):
            messages = {401: "TypeSafe rejected the API key.", 403: "This key cannot access the service.",
                        404: "TypeSafe could not find the requested model or endpoint."}
            return "api_error", messages.get(status, "TypeSafe rejected the request."), True, None
        delay_ms = getattr(error, "retry_after_ms", None)
        if delay_ms is None:
            from email.utils import parsedate_to_datetime
            raw = error.headers.get("retry-after")
            if raw is not None:
                try:
                    delay_ms = float(raw) * 1000
                except ValueError:
                    with suppress(ValueError, TypeError, OverflowError):
                        delay_ms = (parsedate_to_datetime(raw).timestamp() - time.time()) * 1000
        delay = max(0, delay_ms / 1000) if delay_ms is not None and math.isfinite(delay_ms) else None
        return ("rate_limit" if status == 429 else "api_error"), f"TypeSafe returned HTTP {status}.", False, delay
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "timeout", "The decision request timed out.", False, None
    if isinstance(error, TypeSafeAPIConnectionError):
        return "connection", "Could not connect to TypeSafe.", False, None
    if isinstance(error, ValueError):
        return "invalid_response", "TypeSafe did not return a valid action.", False, None
    # Do not serialize arbitrary SDK errors, headers or request bodies into logs.
    return "worker_error", f"Decision worker failed ({type(error).__name__}).", True, None


async def mock_choice(job, delay):
    await asyncio.sleep(delay)
    actions = [a.value for a in ActionId]
    action = actions[(job.request_id - 1) % len(actions)]
    return {"action": action, "strength": list(Strength)[(job.request_id - 1) % 3].value, "confidence": 1.0,
            "probabilities": {a: float(a == action) for a in actions}, "model": "offline-mock",
            "input_tokens": None, "output_tokens": None}


async def run_worker(settings, requests, results, stop, mock_delay=None):
    client = None
    if mock_delay is None:
        from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
        client = AsyncTypeSafeClient(model=settings.model, retry=RetryPolicy(max_retries=0),
                                     timeout=settings.request_timeout)
    parent = mp.parent_process()

    async def watch_parent():
        while not stop.is_set() and (parent is None or parent.is_alive()):
            await asyncio.sleep(0.05)

    watcher = asyncio.create_task(watch_parent())
    failures = 0
    next_eligible = 0.0
    try:
        results.put({"kind": "ready"}, timeout=0.5)
        while not watcher.done():
            try:
                job = requests.get(timeout=0.025)
            except Empty:
                await asyncio.sleep(0)
                continue
            now = time.monotonic()
            if now < next_eligible or now - job.snapshot.captured > settings.queue_age:
                results.put(DecisionResult(job.request_id, job.snapshot, now, now,
                                           next_eligible, "queue_stale", attempted=False), timeout=0.5)
                continue
            started = time.monotonic()

            async def decide():
                if mock_delay is not None:
                    async with asyncio.timeout(settings.request_timeout):
                        return await mock_choice(job, mock_delay)
                return await request_choice(client, job.snapshot.state, settings)

            task = asyncio.create_task(decide())
            completed, _ = await asyncio.wait((task, watcher), return_when=asyncio.FIRST_COMPLETED)
            if watcher in completed:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
                received = time.monotonic()
                results.put(DecisionResult(job.request_id, job.snapshot, started, received,
                                           received, "cancelled", error="Request cancelled during shutdown."), timeout=0.5)
                break
            payload = {}
            try:
                payload = task.result()
                outcome, error, fatal, delay = "ok", None, False, 0.0
                failures = 0
            except Exception as exc:
                outcome, error, fatal, delay = classify_error(exc)
                if outcome == "timeout":
                    error = f"TypeSafe did not reply within the {settings.request_timeout:g}-second request limit."
                failures += 1
                if delay is None:
                    delay = (1.0 if outcome == "rate_limit" else 0.5) * 2 ** min(failures - 1, 4)
                fatal = fatal or failures >= 3
            received = time.monotonic()
            next_eligible = max(started + 1 / settings.requests_per_second, received + delay)
            results.put(DecisionResult(job.request_id, job.snapshot, started, received,
                                       next_eligible, outcome, error=error, fatal=fatal, **payload), timeout=0.5)
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
        if client is not None:
            await client.aclose()


def worker_entry(settings, requests, results, stop, mock_delay=None):
    import signal
    # The main process turns Ctrl-C into an orderly stop for both processes.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        asyncio.run(run_worker(settings, requests, results, stop, mock_delay))
    except Exception as exc:
        with suppress(Full):
            results.put({"kind": "failed", "error": f"Worker stopped ({type(exc).__name__})."}, timeout=0.1)


class Worker:
    def __init__(self, settings, mock_delay=None):
        self.context = mp.get_context("spawn")
        self.requests = self.context.Queue(maxsize=1)
        self.results = self.context.Queue(maxsize=4)
        self.stop_event = self.context.Event()
        self.process = self.context.Process(target=worker_entry,
            args=(settings, self.requests, self.results, self.stop_event, mock_delay), daemon=True)
        self.process.start()

    def send(self, job):
        try:
            self.requests.put_nowait(job)
            return True
        except Full:
            return False

    def drain(self):
        while True:
            try:
                yield self.results.get_nowait()
            except Empty:
                return

    def stop(self):
        self.stop_event.set()

    def close(self, force=False):
        if force and self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=0)
        if self.process.is_alive():
            return False
        for channel in (self.requests, self.results):
            channel.cancel_join_thread()
            channel.close()
        self.process.close()
        return True
