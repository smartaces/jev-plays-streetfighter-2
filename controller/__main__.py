import argparse
import asyncio
import json
import math
import sys
import time

from .config import ROOT, GAME, STATE, ROM_SHA1, load_settings, require_api_key


def check(settings):
    from importlib.metadata import version
    from .game import source_rom, digest
    import stable_retro as retro
    path = source_rom(settings)
    try:
        imported = retro.data.get_romfile_path(GAME)
        imported_ok = digest(imported) == ROM_SHA1
    except FileNotFoundError:
        imported_ok = False
    return {"rom": str(path), "rom_matches": True, "rom_imported": imported_ok,
            "starting_state": STATE, "stable_retro": version("stable-retro"),
            "typesafe_sdk": version("typesafe-sdk"), "python_dotenv": version("python-dotenv"),
            "api_key_file": str(ROOT / ".env"), "api_connection_tested": False}


async def check_api(settings):
    from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
    from .jev import request_choice, classify_error
    from .game import GameSession
    from .observation import ObservationBuilder
    game = GameSession(settings)
    observer = ObservationBuilder()
    try:
        observer.update(game.reset(), time.monotonic())
        state = observer.model_state("neutral", 0, None)
    finally:
        game.close()
    started = time.monotonic()
    try:
        async with AsyncTypeSafeClient(model=settings.model, retry=RetryPolicy(max_retries=0), timeout=10) as client:
            result = await request_choice(client, state, settings, timeout=10)
    except Exception as exc:
        _, message, _, _ = classify_error(exc)
        raise ValueError(message) from None
    result["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
    result["diagnostic_only"] = True
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jev playing Street Fighter II on this Mac")
    parser.add_argument("mode", choices=["check", "prepare", "inspect", "check-api", "play", "mock"])
    parser.add_argument("--duration", type=float, help="Active seconds per play/mock session (configured default: 300)")
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--single-round", action="store_true", help="Pause at the first round ending")
    presentation = parser.add_mutually_exclusive_group()
    presentation.add_argument("--headless", action="store_true", help="Run without a game window (bounded automated run)")
    presentation.add_argument("--dashboard-only", action="store_true", help="Play in the browser dashboard with local Mac audio, without a native game window")
    parser.add_argument("--autostart", action="store_true", help="Start without pressing Space")
    parser.add_argument("--dashboard", action="store_true", help="Open local dashboard; allow play while game window is unfocused")
    parser.add_argument("--no-browser", action="store_true", help="Print dashboard URL without opening a browser")
    parser.add_argument("--no-audio", action="store_true", help="Disable local audio output")
    parser.add_argument("--exit-after", type=float, help="Close automatically after this many wall-clock seconds")
    parser.add_argument("--mock-delay", type=float, default=0.2, help="Offline mock response delay in seconds")
    args = parser.parse_args(argv)
    settings = load_settings(duration=args.duration, max_requests=args.max_requests,
                             continue_rounds=False if args.single_round else None,
                             audio=False if args.no_audio else None)
    if args.dashboard_only and args.mode not in {"play", "mock"}:
        parser.error("Dashboard-only presentation supports play and mock modes.")
    if not math.isfinite(args.mock_delay) or args.mock_delay < 0 or (args.exit_after is not None and
            (not math.isfinite(args.exit_after) or args.exit_after <= 0)):
        parser.error("Delay must be nonnegative and exit-after must be positive.")
    if args.headless and args.mode in {"inspect", "play", "mock"} and (not args.autostart or args.exit_after is None):
        parser.error("Headless runs require --autostart and --exit-after.")
    if args.mode in {"play", "check-api"}:
        require_api_key()
    if args.mode == "check":
        result = check(settings)
    elif args.mode == "prepare":
        from .game import prepare
        result = {"imported_rom": str(prepare(settings)), "sha1": ROM_SHA1}
    elif args.mode == "check-api":
        result = asyncio.run(check_api(settings))
    else:
        from .app import Application
        app = Application(settings, args.mode, mock_delay=args.mock_delay, headless=args.headless,
                          autostart=args.autostart, exit_after=args.exit_after,
                          dashboard=args.dashboard or args.dashboard_only, open_browser=not args.no_browser,
                          dashboard_only=args.dashboard_only)
        app.run()
        return 1 if app.fault else 0
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
