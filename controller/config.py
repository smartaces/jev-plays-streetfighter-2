from dataclasses import asdict, dataclass, fields
from pathlib import Path
import os
import tomllib

ROOT = Path(__file__).resolve().parent.parent
GAME = "StreetFighterIISpecialChampionEdition-Genesis-v0"
STATE = "Champion.Level1.RyuVsGuile"
RULESET = "champion"  # Verified starting-state provenance, not inferred from the ROM title.
ROM_SHA1 = "a5aad1d108046d9388e33247610dafb4c6516e0b"


@dataclass(frozen=True)
class Settings:
    rom: str = "roms/Street_Fighter_II_Special_Champion_Edition_USA.md"
    model: str = "jev-1.13.0"
    fps: float = 60.0
    requests_per_second: float = 4.0
    duration: float = 30.0
    max_requests: int = 120
    continue_rounds: bool = True
    movement_frames: int = 12
    pulse_frames: int = 2
    settle_frames: int = 180
    max_age: float = 0.75
    queue_age: float = 0.1
    request_timeout: float = 3.0
    watchdog: float = 4.0
    punch: str = "X"
    kick: str = "A"
    audio: bool = True
    volume: float = 0.7
    coaching: bool = True

    def public(self):
        return asdict(self)

    def validate(self):
        if type(self.continue_rounds) is not bool:
            raise ValueError("continue_rounds must be true/false.")
        if type(self.coaching) is not bool:
            raise ValueError("coaching must be true/false.")
        if type(self.audio) is not bool or not isinstance(self.volume, (int, float)) or not 0 <= self.volume <= 1:
            raise ValueError("Audio must be true/false and volume must be between 0 and 1.")
        integer_fields = ("max_requests", "movement_frames", "pulse_frames", "settle_frames")
        for name in integer_fields:
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "settle_frames" else 1):
                raise ValueError(f"{name} must be a positive integer (settle_frames may be zero).")
        import math
        for name in ("fps", "requests_per_second", "duration", "max_age", "queue_age", "request_timeout", "watchdog"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number.")
        if self.watchdog <= self.request_timeout:
            raise ValueError("watchdog must exceed request_timeout.")
        if self.punch not in "ABCXYZ" or len(self.punch) != 1 or self.kick not in "ABCXYZ" or len(self.kick) != 1:
            raise ValueError("Punch and kick must be named Genesis face buttons.")
        if self.punch == self.kick:
            raise ValueError("Punch and kick must be different buttons.")
        if not self.model or not isinstance(self.rom, str):
            raise ValueError("A model name and ROM path are required.")
        return self


def load_settings(**overrides):
    path = ROOT / "config/controller.toml"
    values = tomllib.loads(path.read_text()) if path.exists() else {}
    unknown = set(values) - {f.name for f in fields(Settings)}
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    values.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**values).validate()


def require_api_key():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key or key in {"your_actual_key_here", "replace_with_your_api_key"}:
        raise ValueError(f"Put your TypeSafe key in {ROOT / '.env'} as TYPESAFE_API_KEY=your_key")
    # The child inherits this value. It is never part of a job or public config.
    os.environ["TYPESAFE_API_KEY"] = key
