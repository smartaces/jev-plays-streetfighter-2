from collections import deque
from .contracts import ActionId, Strength
from .observation import GROUND_Y

JUMP_ATTACKS = {a for a in ActionId if a.value.startswith('jump_') and a.value.endswith(('_punch', '_kick'))}
MOVEMENT = {ActionId.NEUTRAL, ActionId.APPROACH, ActionId.RETREAT, ActionId.BLOCK, ActionId.CROUCH}


class ActionExecutor:
    def __init__(self, buttons, settings):
        self.buttons = tuple(buttons)
        self.settings = settings
        needed = {'UP', 'DOWN', 'LEFT', 'RIGHT', 'X', 'Y', 'Z', 'A', 'B', 'C', settings.punch, settings.kick}
        if not needed <= set(buttons):
            raise ValueError('The emulator is missing required controller buttons.')
        self.sequence = deque()
        self.jump_plan = None
        self.events = deque()
        self.current = ActionId.NEUTRAL
        self.last_action = None
        self.strength = Strength.LIGHT
        self.direction = 0

    @property
    def remaining(self):
        # Includes the bounded airborne wait, not just the remaining button pulses.
        return len(self.sequence) + (max(0, 48 - self.jump_plan['age']) if self.jump_plan else 0)

    @property
    def locked(self):
        return bool(self.jump_plan or self.sequence) and self.current not in MOVEMENT

    def execution_state(self):
        return {'action': self.current.value, 'strength': self.strength.value,
                'input_sequence_active': self.locked,
                'phase': 'waiting_for_air_attack' if self.jump_plan and not self.sequence else
                         'entering_buttons' if self.locked else 'holding_input' if self.sequence else 'idle',
                'frames_remaining_upper_bound': self.remaining}

    def clear(self):
        if self.locked:
            self.events.append({'kind': 'sequence_cancelled', 'action': self.current.value})
        self.sequence.clear()
        self.jump_plan = None
        self.current = ActionId.NEUTRAL
        self.direction = 0

    def accept(self, action, values, strength='light'):
        action, strength = ActionId(action), Strength(strength)
        dx = values['opponent_x'] - values['player_x']
        direction = (dx > 0) - (dx < 0)
        directional = {ActionId.APPROACH, ActionId.RETREAT, ActionId.JUMP, ActionId.JUMP_BACK,
                       ActionId.BLOCK, ActionId.FIREBALL, ActionId.DRAGON, ActionId.HURRICANE,
                       ActionId.THROW_FORWARD, ActionId.THROW_BACK, ActionId.KICK_FIREBALL} | {a for a in JUMP_ATTACKS if 'up_' not in a}
        if action in directional and direction == 0:
            return False
        towards = 'RIGHT' if direction > 0 else 'LEFT'
        away = 'LEFT' if direction > 0 else 'RIGHT'
        punch = {'light': self.settings.punch, 'medium': 'Y', 'heavy': 'Z'}[strength]
        kick = {'light': self.settings.kick, 'medium': 'B', 'heavy': 'C'}[strength]
        p, movement = self.settings.pulse_frames, self.settings.movement_frames
        defence = max(movement, round(self.settings.fps * 0.6))
        sequences = {
            ActionId.NEUTRAL: [()] * movement,
            ActionId.APPROACH: [(towards,)] * movement,
            ActionId.RETREAT: [(away,)] * defence,
            ActionId.CROUCH: [('DOWN',)] * movement,
            ActionId.BLOCK: [('DOWN', away)] * defence,
            ActionId.JUMP: [('UP', towards)] * p + [()],
            ActionId.JUMP_BACK: [('UP', away)] * p + [()],
            ActionId.JUMP_UP: [('UP',)] * p + [()],
            ActionId.PUNCH: [()] * p + [(punch,)] * p + [()],
            ActionId.STANDING_KICK: [()] * p + [(kick,)] * p + [()],
            ActionId.CROUCH_PUNCH: [('DOWN',)] * p + [('DOWN', punch)] * p + [()],
            ActionId.KICK: [('DOWN',)] * p + [('DOWN', kick)] * p + [()],
            ActionId.HEAVY_PUNCH: [()] * p + [('Z',)] * p + [()],
            ActionId.SWEEP: [('DOWN',)] * p + [('DOWN', 'C')] * p + [()],
            ActionId.AIR_PUNCH: [(punch,)] * p + [()],
            ActionId.AIR_KICK: [(kick,)] * p + [()],
            ActionId.THROW_FORWARD: [(towards,)] * p + [(towards, 'Z')] * p + [()],
            ActionId.THROW_BACK: [(away,)] * p + [(away, 'Z')] * p + [()],
            # Tested cancellable crouching medium kick into Hadouken; an attempt,
            # not an automatic hit confirm. Keep the quarter-circle continuous.
            ActionId.KICK_FIREBALL: [('DOWN',)] * p + [('DOWN', 'B')] * p
                                   + [('DOWN',)] * p + [('DOWN', towards)] * p
                                   + [(towards, punch)] * p + [()],
            ActionId.FIREBALL: [()] * p + [('DOWN',)] * p + [('DOWN', towards)] * p
                               + [(towards, punch)] * p + [()],
            ActionId.DRAGON: [()] * p + [(towards,)] * p + [('DOWN',)] * p
                             + [('DOWN', towards, punch)] * p + [()],
            ActionId.HURRICANE: [()] * p + [('DOWN',)] * p + [('DOWN', away)] * p
                                + [(away, kick)] * p + [()],
        }
        self.jump_plan = None
        if action in JUMP_ATTACKS:
            horizontal = towards if 'forward_' in action else away if 'back_' in action else None
            sequences[action] = [('UP', horizontal) if horizontal else ('UP',)] * p + [()]
            self.jump_plan = {'age': 0, 'airborne_seen': False, 'previous_y': values['player_y'],
                              'button': punch if action.value.endswith('_punch') else kick}
        self.sequence = deque(sequences[action])
        self.current = self.last_action = action
        self.strength = Strength.HEAVY if action in {
            ActionId.HEAVY_PUNCH, ActionId.SWEEP, ActionId.THROW_FORWARD, ActionId.THROW_BACK} else strength
        self.direction = direction
        return True

    def vector(self, names=()):
        names = set(names)
        if not names <= set(self.buttons) - {'START', 'MODE'}:
            raise ValueError('Unsupported gameplay button.')
        if {'LEFT', 'RIGHT'} <= names or {'UP', 'DOWN'} <= names:
            raise ValueError('Opposite directions cannot be held together.')
        return [int(button in names) for button in self.buttons]

    def next_buttons(self, values):
        if self.jump_plan:
            plan = self.jump_plan
            plan['age'] += 1
            jumping = values['player_status'] == 516 and values['player_y'] < GROUND_Y
            descending = values['player_y'] > plan['previous_y']
            plan['previous_y'] = values['player_y']
            plan['airborne_seen'] |= jumping
            if (plan['age'] > 48 or values['player_status'] in (526, 1024) or
                (plan['airborne_seen'] and not jumping) or
                (not plan['airborne_seen'] and plan['age'] > 12)):
                self.events.append({'kind': 'jump_attack_aborted', 'action': self.current.value,
                                    'age': plan['age'], 'status': values['player_status']})
                self.clear()
            elif not self.sequence and jumping:
                distance = abs(values['opponent_x'] - values['player_x'])
                # A bounded timing heuristic, not a collision/hit-confirm detector.
                if plan['age'] >= 8 and ((descending and distance <= 80) or plan['age'] >= 30):
                    self.events.append({'kind': 'air_attack_input', 'action': self.current.value,
                                        'age': plan['age'], 'y': values['player_y'],
                                        'distance': distance, 'button': plan['button']})
                    self.sequence = deque([(plan['button'],)] * self.settings.pulse_frames + [()])
                    self.jump_plan = None
        if self.current in {ActionId.APPROACH, ActionId.RETREAT, ActionId.BLOCK} and self.sequence:
            dx = values['opponent_x'] - values['player_x']
            if dx == 0:
                self.clear()
            else:
                # Follow the selected relative direction through a side switch.
                direction = 'RIGHT' if dx > 0 else 'LEFT'
                away = 'LEFT' if dx > 0 else 'RIGHT'
                self.sequence.popleft()
                return self.vector(('DOWN', away) if self.current == ActionId.BLOCK else
                                   (direction if self.current == ActionId.APPROACH else away,))
        if not self.sequence:
            if not self.jump_plan:
                self.current = ActionId.NEUTRAL
            return self.vector()
        return self.vector(self.sequence.popleft())
