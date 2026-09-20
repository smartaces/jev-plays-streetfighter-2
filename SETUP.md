# Setup on a new Mac

Use the [README quick start](README.md#quick-start-on-macos) for the complete sequence. This page explains the requirements and common failures. All commands assume Terminal is in your cloned repository folder.

## Requirements

- An Apple Silicon Mac. The controller was developed and checked on native arm64 Python 3.14.6 and macOS 26.6.2. Intel macOS, Windows and Linux have not been validated.
- Git and native Python 3.14. If needed, install Python from [python.org](https://www.python.org/downloads/macos/) or your existing package manager. Avoid running an Intel Python under Rosetta for this setup.
- A matching, separately supplied USA Genesis / Mega Drive ROM for Street Fighter II: Special Champion Edition.
- Internet access and your own TypeSafe key for live Jev play. Offline modes do not require that key.

Stable Retro supplies the emulator, RAM interface and game integration. There is no separate emulator application to install. TypeSafe hosts the model; there are no local model weights. See [Stable Retro's installation documentation](https://stable-retro.farama.org/getting_started/).

## Python environment

Check the interpreter:

```sh
python3.14 -c 'import platform; print(platform.python_version(), platform.machine())'
```

Expect Python 3.14 and `arm64` on Apple Silicon. Then create the project environment and install the tested versions:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip check
```

The main packages are Stable Retro 1.0.1, TypeSafe SDK 0.6.0, python-dotenv 1.2.3 and sounddevice 0.5.6. [requirements.in](requirements.in) lists direct dependencies; [requirements.lock.txt](requirements.lock.txt) pins the tested environment. It is not a hash-locked cross-platform specification.

If an environment created by another tool has no pip, use that tool's installer or run `.venv/bin/python -m ensurepip --upgrade` before installing. If pip cannot find a matching binary wheel, first check Python version and architecture rather than substituting arbitrary package versions.

## ROM and starting state

Place the uncompressed ROM at `roms/Street_Fighter_II_Special_Champion_Edition_USA.md`. Create the `roms` folder if it does not exist. The `.md` suffix here means a Mega Drive ROM, not a text file.

Check its checksum:

```sh
shasum -a 1 roms/Street_Fighter_II_Special_Champion_Edition_USA.md
```

Expected SHA-1: `a5aad1d108046d9388e33247610dafb4c6516e0b`.

Then run:

```sh
.venv/bin/python -m controller prepare
.venv/bin/python -m controller check
```

Preparation checks the source ROM and imports a copy into Stable Retro's installation under `.venv`. Keep the source in `roms` too: the controller verifies both copies. `check` should report `rom_matches: true` and `rom_imported: true`. It does not test the API.

The integration is `StreetFighterIISpecialChampionEdition-Genesis-v0`, using the `Champion.Level1.RyuVsGuile` starting state supplied by Stable Retro. This repository distributes neither the ROM nor a save-state file. Other regional revisions, Super Street Fighter II and arcade ROMs will fail validation. Renaming a file cannot correct a checksum mismatch.

You may change `rom` in [config/controller.toml](config/controller.toml) to another local path for the same matching file. Keep ROM files outside tracked source; the default `roms` folder is ignored in full.

## Key and first launch

Copy `.env.example` to `.env` once, then set `TYPESAFE_API_KEY` in `.env` using your own key. The template contains no credential. Existing environment variables override the file. Restart the launcher after changing it.

Start with `.venv/bin/python -m controller inspect` to check manual gameplay, or `.venv/bin/python -m controller mock --dashboard-only` to check the dashboard. Both need the ROM but make no TypeSafe requests.

For live play, double-click **Play with Dashboard.command**, or run `.venv/bin/python -m controller play --dashboard-only`. Press **Resume** in the dashboard. Live requests are paid. The optional `check-api` mode makes one paid request and never applies the returned action; it is not required to launch the game.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Launcher cannot find Python | Create `.venv` in this repository and install its dependencies. Finder launchers use `.venv/bin/python`. |
| ROM missing or mismatched | Check the exact source path, uncompressed file and checksum above, then run `prepare`. |
| ROM not imported / installed ROM differs | Run `prepare` again using the current `.venv`. |
| Starting state missing | Confirm Stable Retro 1.0.1 and the named integration are installed. |
| Missing key, rejected key or unavailable model | Check `.env`, any overriding environment variable, and model access in your TypeSafe account. The configured model is `jev-1.13.0`. |
| Game is stationary after launch | It starts paused. Use Resume in the dashboard or Space in the native window. |
| Native game pauses when switching apps | Native-window mode pauses on loss of focus. The dashboard mode supports playing while you inspect the browser. |
| No sound | Check the Mac output device and volume, dashboard mute, and `audio` / `volume` settings. Relaunch after changing output devices. |
| Repeated request timeouts | Check connectivity and TypeSafe availability. The current request timeout is three seconds, but answers older than 0.75 seconds are discarded. Restart after correcting a connection or key problem. |
| Dashboard has no saved runs | A fresh clone contains no logs. Complete a local inspection, mock or live session first. Saved replay contains state and inputs, not recorded game video. |

## Offline verification

```sh
.venv/bin/python -m unittest discover -s tests
```

These tests use fixtures and simulated API responses; they need neither a ROM nor a key. Dashboard server tests need permission to bind a localhost port.

Test results establish controller behaviour, not a guarantee of Jev's fighting ability. See [RUNNING.md](RUNNING.md) for offline mock gameplay and reviewing your own sessions.
