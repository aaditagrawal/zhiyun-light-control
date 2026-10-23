"""Command-line client and bridge entry point."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from aiohttp import web

from .bridge import Bridge, default_token_path, ensure_token, usb_info
from .client import LightClient
from .transport import discover


async def run_command(args):
    if args.command == "scan":
        return {"devices": await discover(args.timeout)}
    if args.command == "usb-info":
        return await usb_info(args.port)
    if args.command == "token-path":
        return {"tokenFile": str(default_token_path())}
    async with LightClient(args.address) as light:
        if args.command == "state":
            return light.state.to_dict()
        return (
            await light.set(
                brightness=args.brightness,
                cct=args.cct,
                power=None if args.power is None else args.power == "on",
            )
        ).to_dict()


def main():
    parser = argparse.ArgumentParser(description="Control ZHIYUN MOLUS G60 over Bluetooth")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Discover local BLE G60 and USB serial devices")
    scan.add_argument("--timeout", type=float, default=5)
    state = commands.add_parser("state", help="Read the actual G60 settings")
    state.add_argument("--address", required=True)
    set_command = commands.add_parser("set", help="Set and verify settings")
    set_command.add_argument("--address", required=True)
    set_command.add_argument("--brightness", type=float)
    set_command.add_argument("--cct", type=int)
    set_command.add_argument("--power", choices=["on", "off"])
    serial = commands.add_parser("usb-info", help="Read USB identity (not USB runtime control)")
    serial.add_argument("--port", default="/dev/ttyACM0")
    commands.add_parser("token-path", help="Print the local bridge credential file path")
    serve = commands.add_parser("serve", help="Run the authenticated HTTP/SSE bridge")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8877)
    serve.add_argument("--address", help="Automatically connect to this BLE address at startup")
    serve.add_argument("--token-file", type=Path, default=default_token_path())
    serve.add_argument(
        "--no-auth", action="store_true", help="Personal local mode: do not require a bearer token"
    )
    serve.add_argument("--allow-origin", action="append", default=[])
    serve.add_argument(
        "--allow-mock", action="store_true", help="Explicitly enable the test-only backend"
    )
    serve.add_argument(
        "--web-root",
        type=Path,
        default=Path(__file__).with_name("static")
        if Path(__file__).with_name("static").is_dir()
        else Path(__file__).resolve().parents[2] / "examples" / "browser",
    )
    args = parser.parse_args()
    try:
        if args.command == "serve":
            token = "" if args.no_auth else ensure_token(args.token_file)
            bridge = Bridge(
                token,
                auth_required=not args.no_auth,
                allow_origins=args.allow_origin,
                allow_mock=args.allow_mock,
                web_root=args.web_root,
            )
            if args.address:
                bridge.initial_address = args.address
            if not args.no_auth:
                print(f"Bridge token file: {args.token_file}", flush=True)
            # Bridge.on_shutdown cancels device work and wakes SSE; on_cleanup
            # drains owned operations and closes clients after this HTTP drain.
            web.run_app(
                bridge.app, host=args.host, port=args.port, access_log=None, shutdown_timeout=5
            )
        else:
            if args.command == "scan" and not 0.1 <= args.timeout <= 30:
                parser.error("--timeout must be between 0.1 and 30 seconds")
            print(json.dumps(asyncio.run(run_command(args)), indent=2))
    except KeyboardInterrupt:
        pass
    except (ConnectionError, OSError, ValueError) as exc:
        print(f"lighttime: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
