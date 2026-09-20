# Running Jev with Street Fighter II

Complete [SETUP.md](SETUP.md) first. All commands below run from the cloned repository folder. Live play needs your own matching ROM and TypeSafe key. Manual and mock gameplay need the ROM but make no API requests.

## Play with the dashboard

Double-click **Play with Dashboard.command**, or run:

```sh
.venv/bin/python -m controller play --dashboard-only
```

The game starts paused, with Ryu facing Guile. Press **Resume** in the dashboard to begin paid Jev inference. Use **Pause**, **Mute/Unmute** and **Stop** to control the session. Audio comes from the Mac's selected output device. Closing the browser tab does not stop the game: use Stop or press Ctrl-C in the launcher terminal.

The left side shows live video and controller inputs. The right side contains fighter proximity, Jev's latest decisions, response timings and rounds, and scrolls independently on a wide screen.

- **Jev saw** shows the observation captured for the selected request, including its recorded instructions and answers.
- **Game state** shows the current observation, which can be newer than the selected decision. Live video continues while you inspect an older request.
- **Action and strength** are Jev's selected controls. The displayed applied strength can differ for moves with a fixed strength.
- **Threat and opening** are independent diagnostic answers, not a transcript of the model's reasoning or overrides of its selected action.
- **Applied/discarded** tells you whether the controller accepted a reply. Pressed buttons do not prove a move connected; probabilities are not hit or win rates.

The dashboard binds to localhost and is intended for use on the same Mac. It does not make extra model requests. Add `--no-browser` to print the address without opening a browser, or `--no-audio` to disable sound.

## Rounds and session limits

Jev continues through rounds, advances to the next opponent after a win, and retries the current opponent after a loss. The opponent's identity and matchup context update with the fight. Bonus stages time out without model requests.

The default limit is **300 active seconds or 1,200 requests** across all rounds and retries. Paused time does not consume the duration; between-round animations do, but no new requests are sent until fighting resumes. Resets and rematches do not replenish the limits.

Change `duration` and `max_requests` in [config/controller.toml](config/controller.toml), or override them at launch:

```sh
.venv/bin/python -m controller play --dashboard-only --duration 120 --max-requests 300
```

Longer live sessions may cost more. Add `--single-round` to pause at the first round ending. Restart after changing settings, instructions or `.env`. An existing `TYPESAFE_API_KEY` environment variable overrides `.env`.

## Native window and manual controls

**Play.command** opens Jev in a separate game window. **Inspect.command** opens the same window with local keyboard controls and no TypeSafe calls:

```sh
.venv/bin/python -m controller inspect
```

| Key | Effect |
| --- | --- |
| Space | Start or pause |
| R | Reset to the initial Guile match; remain paused |
| M | Mute or unmute |
| Escape | Close and finish recording |
| Arrow keys, Inspect only | Move, jump or crouch |
| Z / X, Inspect only | Punch / kick |
| 1–6, Inspect only | Neutral, approach, retreat, jump forward, punch, crouching kick |
| 7 / 8 / 9, Inspect only | Crouching block / heavy punch / sweep |
| F / D / H, Inspect only | Fireball / dragon punch / hurricane kick |
| J / K, Inspect only | Forward jump with kick / punch |
| V / N, Inspect only | Vertical / backward empty jump |
| T / B, Inspect only | Forward / backward throw attempt |
| F1–F6, Inspect only | Diagnostic pulses for Genesis A/B/C/X/Y/Z |

Inspect action shortcuts use light strength except explicit heavy attacks and throws. Native-window mode pauses on loss of focus; return to the window and press Space to resume. To keep playing while interacting with a browser, use dashboard mode instead. `play --dashboard` opens both the native window and dashboard.

The native display is resizable and supports Retina scaling. Audio uses the Mac's selected output device. Change `volume` (0–1) or `audio` in the controller settings, and relaunch after changing output devices.

## Review a session

Open **Dashboard.command**, or run:

```sh
.venv/bin/python -m controller.dashboard
```

Choose a saved run to replay its state, decisions and inputs. Replay supports seeking, decision stepping, round navigation and comparing action shares. Live video is streamed rather than recorded, so saved sessions have no game video. A fresh clone has no saved runs.

For a standalone report of the latest completed live session, open **Review Decisions.command**. You can also select a run directly:

```sh
.venv/bin/python scripts/decision_report.py runs/YOUR_RUN_FOLDER --open
```

Review makes no model calls and does not need a ROM or API key. Close the offline dashboard server with Ctrl-C in its terminal.

Each session writes an ignored `runs/` folder with a manifest, `events.jsonl` and a final summary. These record the supplied state and instructions, model answers, timings, applied buttons and discarded replies. `complete: true` means recording finished, not that Jev won or played well. Close the game normally to finish the summary.

## Offline checks and optional API diagnostic

| Mode | Behaviour |
| --- | --- |
| `prepare` | Verify and import the supplied ROM; no API call |
| `check` | Check installed packages and the ROM; no API call |
| `inspect` | Manual game controls; no API call |
| `mock` | Simulated decisions; no API call |
| `play` | Live Jev control; starts paused |
| `check-api` | One paid inference; never applies the answer |

Use each mode after `.venv/bin/python -m controller`. For a mock game with the dashboard, run:

```sh
.venv/bin/python -m controller mock --dashboard-only
```

For a bounded offline smoke test without a window:

```sh
.venv/bin/python -m controller mock --headless --autostart --duration 5 --exit-after 7
```

Both mock modes require the ROM. Headless modes require `--autostart` and a finite `--exit-after`. `--mock-delay 0.8` simulates stale replies; `--mock-delay 3.2` exceeds the current three-second request timeout. Simulated delays do not measure TypeSafe latency.

The optional `.venv/bin/python -m controller check-api` makes one paid diagnostic request with a prepared observation and allows ten seconds for its reply. Live gameplay permits three seconds per request, with a four-second worker watchdog. Replies older than 0.75 seconds are discarded, even if the request succeeds. After repeated failures the controller releases inputs and stops active play; fix the connection or key problem and relaunch.

Run the automated tests without a ROM or key:

```sh
.venv/bin/python -m unittest discover -s tests
```

See [SETUP.md](SETUP.md) for troubleshooting and [README.md](README.md#how-jev-is-prompted) for the guide, action descriptions and controller code.
