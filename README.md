# Jev plays Street Fighter II

An experimental agent that lets [TypeSafe's Jev](https://typesafe.ai/) play Ryu in **Street Fighter II: Special Champion Edition (USA)** for Genesis / Mega Drive, with a local browser dashboard showing what the model sees and chooses.

[Connect with me on LinkedIn](https://www.linkedin.com/in/jamesbentleyai/).

[![Watch Jev play Street Fighter II on YouTube](https://img.youtube.com/vi/lz_JW_F2lJk/hqdefault.jpg)](https://youtu.be/lz_JW_F2lJk)

[Watch the gameplay demo on YouTube](https://youtu.be/lz_JW_F2lJk).

The emulator runs on your Mac. Game RAM becomes a structured observation; Jev selects a move and strength; a local controller executes the button sequence. The game continues while requests are pending. Jev receives text/JSON, not game video. There is no training or learning between rounds.

This is a gameplay experiment, not an expert Street Fighter bot. Movement, attacks, defence and specials work, but choices and timing can be inconsistent.

## What is included

- Live game video and audio, fighter proximity views, selected actions, controller inputs and response timings.
- 28 available actions, including jump attacks, sweeps, throws, fireball, dragon punch and hurricane / whirlwind kick, with three attack strengths where supported.
- A concise Ryu guide, opponent-specific context and recent-action feedback.
- Continuation through rounds, new opponents after wins and retries after losses.
- Local session logs and an offline dashboard for reviewing decisions. Live video is not recorded.

**No game ROM, API key or emulator save-state file is included in this repository.** Supply your own legally obtained matching ROM and TypeSafe API key. The starting-state integration comes with Stable Retro. Live Jev play uses paid TypeSafe requests; inspection, mock play and saved-run review do not.

## Quick start on macOS

The tested environment is **Apple Silicon, Python 3.14.6, Stable Retro 1.0.1 and TypeSafe SDK 0.6.0**. No local AI model or GPU setup is required. Other operating systems and Intel Macs have not been validated for this controller. See [detailed setup and troubleshooting](SETUP.md).

### 1. Install the dependencies

With Git and native Python 3.14 installed, open Terminal:

```sh
git clone https://github.com/smartaces/jev-plays-streetfighter-2.git
cd jev-plays-streetfighter-2
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
mkdir -p roms
```

Keep `.venv` inside the project: the Finder launchers use it. The dependency pins describe the tested environment, rather than a cross-platform lock file.

### 2. Add your ROM

Copy your uncompressed **Street Fighter II: Special Champion Edition (USA)** Genesis / Mega Drive ROM to:

```text
roms/Street_Fighter_II_Special_Champion_Edition_USA.md
```

Here `.md` is the Mega Drive ROM extension, not a Markdown document. Renaming a different game or regional revision will not make it compatible. The required SHA-1 is:

```text
a5aad1d108046d9388e33247610dafb4c6516e0b
```

Import it and verify the local setup:

```sh
.venv/bin/python -m controller prepare
.venv/bin/python -m controller check
```

Neither command calls TypeSafe. `prepare` validates the ROM and copies it into Stable Retro inside your ignored virtual environment. The initial match uses its `Champion.Level1.RyuVsGuile` state.

### 3. Add your own TypeSafe API key

Create the local key file once:

```sh
cp -n .env.example .env
open -e .env
```

Enter your key in `.env` and save:

```dotenv
TYPESAFE_API_KEY=your_actual_key_here
```

Obtain a key through [TypeSafe](https://typesafe.ai/); see its [Python SDK documentation](https://docs.typesafe.ai/sdk/python/). `.env` is ignored by Git. Keep the key out of source files, screenshots and issues. An already-set `TYPESAFE_API_KEY` environment variable takes precedence over this file.

### 4. Start playing

Double-click **Play with Dashboard.command** in Finder, or run:

```sh
.venv/bin/python -m controller play --dashboard-only
```

The dashboard opens paused. Press **Resume** to let Jev play. Use **Pause**, **Mute** and **Stop** in the dashboard; audio comes from your Mac. Stop the session when finished so its final summary is saved.

The default limit is **300 active seconds or 1,200 requests**, whichever is reached first. Paused time does not consume the session duration. Settings, including the pinned `jev-1.13.0` model, live in [config/controller.toml](config/controller.toml). Check your account's model access and current TypeSafe pricing before live play.

## Other ways to use it

Run commands from the repository folder:

| Mode | Command | TypeSafe requests |
| --- | --- | --- |
| Manual controls | `.venv/bin/python -m controller inspect` | None |
| Mock agent with dashboard | `.venv/bin/python -m controller mock --dashboard-only` | None |
| Jev in a separate game window | `.venv/bin/python -m controller play` | Paid, after starting |
| Review saved sessions | `.venv/bin/python -m controller.dashboard` | None |
| One optional connection check | `.venv/bin/python -m controller check-api` | One paid inference |
| Automated tests | `.venv/bin/python -m unittest discover -s tests` | None; no ROM needed |

Manual and mock gameplay still need the matching ROM. A fresh clone has no recorded sessions. In the native game window, **Space** starts/pauses, **R** resets, **M** mutes and **Escape** closes. Inspection adds keyboard controls; see [RUNNING.md](RUNNING.md).

## How Jev is prompted

Every request contains current game context and four questions evaluated together: next action, attack strength, main threat and opening. Only action and strength control the game. The other answers are diagnostic labels, not a transcript of the model's reasoning.

| Part | Where to read or edit it |
| --- | --- |
| Ryu fighting guide | [config/ryu-fighting-guide.md](config/ryu-fighting-guide.md) |
| Action choices, descriptions and examples sent to Jev | [controller/jev.py](controller/jev.py) |
| Button sequences and move execution | [controller/actions.py](controller/actions.py) |
| RAM observations and compact context | [controller/observation.py](controller/observation.py), [controller/context.py](controller/context.py) |
| Opponent information and contextual coaching | [controller/characters.py](controller/characters.py), [controller/coaching.py](controller/coaching.py) |

Restart after editing instructions or settings. Each run records its own instructions so older sessions remain interpretable.

## Limitations and local data

The emulator targets 60 fps; the controller caps requests at four per second with one outstanding request at a time. Actual decisions are slower when replies or input sequences take longer. This is not a ten-decisions-per-second benchmark.

RAM supplies health, positions, broad fighter states and projectile tracking. Exact hitboxes, named opponent specials, reliable dizzy detection and precise attack phases are not fully mapped. A selected attack or pressed button does not prove that the move connected.

ROMs, `.env`, virtual environments, logs and runtime save states stay local and are ignored by Git. The dashboard binds to `127.0.0.1` and is intended for use on the same Mac. Live play sends structured game observations and instructions to TypeSafe; the ROM and game video are not sent.

Street Fighter II and its game assets belong to their respective owners; this project is an independent experiment.

See [SETUP.md](SETUP.md) for installation and troubleshooting, and [RUNNING.md](RUNNING.md) for dashboard controls, manual play and session review. The current checkout contains runtime files and core tests; planning notes, research outputs and manual backups are local-only. Older Git commits and tags retain the historical versions.
