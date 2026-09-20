from pathlib import Path
import hashlib
from .config import ROOT, GAME, STATE, ROM_SHA1


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha1").hexdigest()


def source_rom(settings):
    path = ROOT / settings.rom
    if not path.is_file():
        raise ValueError(f"ROM not found: {path}")
    if digest(path) != ROM_SHA1:
        raise ValueError("The ROM does not match Street Fighter II Special Champion Edition (USA).")
    return path


def prepare(settings):
    import stable_retro as retro
    path = source_rom(settings)
    retro.data.merge(str(path))
    imported = Path(retro.data.get_romfile_path(GAME))
    if digest(imported) != ROM_SHA1:
        raise ValueError("The imported ROM checksum does not match.")
    return imported


class GameSession:
    def __init__(self, settings):
        import stable_retro as retro
        self.settings = settings
        source_rom(settings)
        try:
            imported = retro.data.get_romfile_path(GAME)
        except FileNotFoundError:
            raise ValueError("Prepare the ROM first: .venv/bin/python -m controller prepare") from None
        if digest(imported) != ROM_SHA1:
            raise ValueError("The installed ROM differs. Run controller prepare again.")
        self.env = retro.make(GAME, state=STATE, players=1,
                              info=str(ROOT / "config/game-data.json"),
                              scenario=str(ROOT / "config/scenario.json"),
                              use_restricted_actions=retro.Actions.ALL,
                              obs_type=retro.Observations.IMAGE, render_mode="rgb_array")
        self.buttons = self.env.buttons
        self.zero = [0] * len(self.buttons)
        self.frame = 0
        self.image = None
        self.closed = False
        self.checkpoint = None

    def reset(self):
        self.env.reset()
        for _ in range(max(1, self.settings.settle_frames)):
            self.image, _, terminated, truncated, info = self.env.step(self.zero)
            if terminated or truncated:
                raise ValueError("The saved game ended during preparation.")
        self.frame = 0
        self.save_checkpoint()
        return info

    def save_checkpoint(self):
        """Keep the current opponent's opening in memory for a loss retry."""
        self.checkpoint = self.env.em.get_state()

    def retry_match(self):
        if self.checkpoint is None:
            raise ValueError("No match checkpoint is available.")
        self.env.em.set_state(self.checkpoint)
        self.env.data.reset()
        self.env.data.update_ram()
        self.frame = 0
        info, terminal = self.step(self.zero)
        if terminal:
            raise ValueError("The match checkpoint is not playable.")
        return info

    def step(self, buttons):
        self.image, _, terminated, truncated, info = self.env.step(buttons)
        self.frame += 1
        return info, terminated or truncated

    def release(self):
        if self.closed:
            return
        import numpy as np
        self.env.em.set_button_mask(np.zeros(len(self.buttons), dtype=np.uint8), 0)

    def audio_samples(self):
        return self.env.em.get_audio()

    @property
    def audio_rate(self):
        return self.env.em.get_audio_rate()

    def close(self):
        if self.closed:
            return
        self.release()
        self.env.close()
        self.closed = True
