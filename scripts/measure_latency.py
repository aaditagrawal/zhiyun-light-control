"""Opt-in HTTP latency benchmark of an already-connected G60 bridge.

Run with uv run --frozen python scripts/measure_latency.py --live.
Never opens BLE, scans, connects/disconnects a device, or controls a service.
"""

import argparse
import asyncio
import json
import math
import platform
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from time import perf_counter_ns
from typing import Any
from urllib.parse import urlsplit

from lighttime import BridgeClient
from lighttime.protocol import validate_brightness, validate_cct

State = dict[str, Any]
Identity = tuple[str, str, int]
MODES = ("brightness", "cct", "combined")


class BenchmarkError(RuntimeError):
    """A local precondition or readback check failed."""


def identity(state: State) -> Identity:
    device = state.get("device")
    if (
        state.get("connected") is not True
        or state.get("transport") not in ("ble", "usb+ble")
        or not isinstance(state.get("address"), str)
        or not state["address"]
        or not isinstance(device, dict)
        or device.get("model", "").upper() != "PL103"
        or not isinstance(device.get("serial"), str)
        or type(device.get("id")) is not int
    ):
        raise BenchmarkError("An already-connected BLE PL103 is required")
    return state["address"], device["serial"], device["id"]


def check_state(
    state: State, expected_device: Identity | None = None, target: State | None = None
) -> State:
    actual_device = identity(state)
    if expected_device is not None and actual_device != expected_device:
        raise BenchmarkError("Bridge device changed; refusing further writes")
    brightness = validate_brightness(state.get("brightness"))
    cct = validate_cct(state.get("cct"))
    if state.get("power") is not (brightness > 0):
        raise BenchmarkError("Inconsistent power readback")
    if target is not None and any(state.get(key) != value for key, value in target.items()):
        raise BenchmarkError("Readback differs from the requested target")
    # Deliberately omit URLs, credentials, server messages and device identifiers.
    return {"brightness": brightness, "cct": cct, "power": brightness > 0}


def summarize(rows: list[State]) -> State:
    result: State = {}
    for mode in MODES:
        for operation in ("write", "read"):
            selected = [
                row
                for row in rows
                if row["phase"] == "samples"
                and row["mode"] == mode
                and row["operation"] == operation
            ]
            if not selected:
                continue
            values = sorted(row["latencyMs"] for row in selected if row["status"] == "ok")
            summary: State = {
                "count": len(values),
                "failed": sum(row["status"] != "ok" for row in selected),
            }
            if values:
                # Nearest-rank percentiles; p95 is the maximum when n < 20.
                summary.update(
                    minMs=values[0],
                    meanMs=sum(values) / len(values),
                    p50Ms=values[math.ceil(0.50 * len(values)) - 1],
                    p95Ms=values[math.ceil(0.95 * len(values)) - 1],
                    maxMs=values[-1],
                )
            result[f"{mode}.{operation}"] = summary
    return result


class Recorder:
    def __init__(self, path: Path, config: State):
        self.path = path
        self.report: State = {
            "schemaVersion": 1,
            "startedAt": datetime.now(UTC).isoformat(),
            "status": "preflight",
            "scope": "HTTP SDK round trip, including bridge queue and BLE readback; not optical",
            "python": platform.python_version(),
            "config": config,
            "samples": [],
            "restoration": {"status": "not_needed"},
        }
        # Reserve a writable report before contacting the bridge. Never overwrite a run.
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(self.report, indent=2) + "\n")

    def save(self) -> None:
        self.path.write_text(json.dumps(self.report, indent=2) + "\n", encoding="utf-8")

    async def measure(
        self,
        phase: str,
        mode: str,
        operation: str,
        action: Callable[[], Awaitable[State]],
        *,
        target: State | None = None,
        expected_device: Identity | None = None,
        sample: int | None = None,
    ) -> State:
        row: State = {"phase": phase, "mode": mode, "operation": operation}
        if sample is not None:
            row["sample"] = sample
        if target is not None:
            row["requested"] = dict(target)
        started = perf_counter_ns()
        try:
            state = await action()
            row["latencyMs"] = (perf_counter_ns() - started) / 1_000_000
            row["confirmed"] = check_state(state, expected_device, target)
            row["status"] = "ok"
            return state
        except BaseException as exc:
            row.setdefault("latencyMs", (perf_counter_ns() - started) / 1_000_000)
            row["status"] = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            # Exception text can contain URLs, headers or server-controlled strings.
            row["errorKind"] = type(exc).__name__
            if isinstance(exc, BenchmarkError):
                row["reason"] = str(exc)
            raise
        finally:
            self.report["samples"].append(row)
            self.save()
            print(
                f"{phase:12} {mode:10} {operation:5} {row['latencyMs']:9.3f} ms {row['status']}",
                flush=True,
            )


async def benchmark(args: argparse.Namespace, bridge: BridgeClient, recorder: Recorder) -> None:
    baseline: State | None = None
    expected_device: Identity | None = None
    write_attempted = False
    try:
        effect = await bridge.effect()
        if effect.get("running") is not False:
            raise BenchmarkError("Stop the running effect before benchmarking")
        initial = await recorder.measure("baseline", "baseline", "read", bridge.refresh)
        baseline = check_state(initial)
        expected_device = identity(initial)
        if baseline["brightness"] > args.max_brightness:
            raise BenchmarkError("Baseline exceeds the brightness cap; refusing to write")
        recorder.report["baseline"] = baseline
        recorder.report["transport"] = initial["transport"]
        recorder.report["status"] = "running"
        recorder.save()
        for mode in args.modes:
            for index in range(args.warmup + args.samples):
                high = index % 2 == 1
                target: State = {}
                if mode in ("brightness", "combined"):
                    target["brightness"] = args.max_brightness if high else args.min_brightness
                if mode in ("cct", "combined"):
                    target["cct"] = args.cct_high if high else args.cct_low
                phase = "warmup" if index < args.warmup else "samples"
                sample = index + 1 if phase == "warmup" else index - args.warmup + 1
                # Set before sending: even a failed or cancelled PATCH may reach hardware.
                write_attempted = True
                await recorder.measure(
                    phase,
                    mode,
                    "write",
                    partial(bridge.set, **target),
                    target=target,
                    expected_device=expected_device,
                    sample=sample,
                )
                await recorder.measure(
                    phase,
                    mode,
                    "read",
                    bridge.refresh,
                    target=target,
                    expected_device=expected_device,
                    sample=sample,
                )
                await asyncio.sleep(args.interval_ms / 1000)
        recorder.report["status"] = "completed"
    except BaseException as exc:
        recorder.report.update(
            status="interrupted" if isinstance(exc, asyncio.CancelledError) else "failed",
            errorKind=type(exc).__name__,
        )
        if isinstance(exc, BenchmarkError):
            recorder.report["reason"] = str(exc)
        raise
    finally:
        if baseline is not None and write_attempted:
            recorder.report["restoration"] = {"status": "pending"}
            try:
                # Do not restore the old light's output onto a replacement device.
                await recorder.measure(
                    "restore", "baseline", "read", bridge.refresh, expected_device=expected_device
                )
                recorder.report["restoration"]["status"] = "attempted"
                await recorder.measure(
                    "restore",
                    "baseline",
                    "write",
                    lambda: bridge.set(brightness=baseline["brightness"], cct=baseline["cct"]),
                    target=baseline,
                    expected_device=expected_device,
                )
                await recorder.measure(
                    "restore",
                    "baseline",
                    "read",
                    bridge.refresh,
                    target=baseline,
                    expected_device=expected_device,
                )
                recorder.report["restoration"]["status"] = "verified"
            except BaseException as exc:
                recorder.report["restoration"].update(status="failed", errorKind=type(exc).__name__)
                recorder.report["status"] = "failed"
                if not isinstance(exc, Exception):
                    raise
        recorder.save()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--live", action="store_true", help="Opt in to real bridge reads/writes")
    result.add_argument("--base-url", default="http://127.0.0.1:8877")
    result.add_argument(
        "--token-file", type=Path, help="Optional bearer-token file; no auth by default"
    )
    result.add_argument(
        "--samples", type=int, default=10, help="Measured pairs per mode (default: 10)"
    )
    result.add_argument(
        "--warmup", type=int, default=1, help="Unscored pairs per mode (default: 1)"
    )
    result.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    result.add_argument("--min-brightness", type=int, default=1)
    result.add_argument(
        "--max-brightness", type=int, default=10, help="Test/restoration cap (default: 10)"
    )
    result.add_argument("--cct-low", type=int, default=3200)
    result.add_argument("--cct-high", type=int, default=5600)
    result.add_argument(
        "--interval-ms", type=float, default=100, help="Untimed pause after each pair"
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    result.add_argument("--output", type=Path, default=Path(f"artifacts/latency/{stamp}.json"))
    return result


def validate_args(args: argparse.Namespace, cli: argparse.ArgumentParser) -> None:
    if not args.live:
        cli.error("--live is required; no requests were made")
    url = urlsplit(args.base_url)
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
    ):
        cli.error("base URL must be HTTP(S) without credentials, query or fragment")
    if not 1 <= args.samples <= 1000 or not 0 <= args.warmup <= 100:
        cli.error("samples must be 1..1000 and warmup 0..100")
    if not 0 <= args.min_brightness < args.max_brightness <= 100:
        cli.error("brightness must satisfy 0 <= min < max <= 100; default max is 10")
    if not 2700 <= args.cct_low < args.cct_high <= 6500 or any(
        value % 100 for value in (args.cct_low, args.cct_high)
    ):
        cli.error("CCT bounds must be distinct 100 K steps from 2700 through 6500")
    if not math.isfinite(args.interval_ms) or not 0 <= args.interval_ms <= 60_000:
        cli.error("interval-ms must be finite and between 0 and 60000")
    args.modes = list(dict.fromkeys(args.modes))


async def run(args: argparse.Namespace, recorder: Recorder) -> None:
    token = None
    if args.token_file is not None:
        token = args.token_file.read_text(encoding="utf-8").strip()
        if not token or "\r" in token or "\n" in token:
            raise BenchmarkError("Token file must contain one nonempty token")
    # One SDK ClientSession reuses HTTP connections. All calls are awaited serially.
    async with BridgeClient(args.base_url, token=token) as bridge:
        await benchmark(args, bridge, recorder)


def main() -> int:
    cli = parser()
    args = cli.parse_args()
    validate_args(args, cli)
    config = {
        key: getattr(args, key)
        for key in (
            "samples",
            "warmup",
            "modes",
            "min_brightness",
            "max_brightness",
            "cct_low",
            "cct_high",
            "interval_ms",
        )
    }
    try:
        recorder = Recorder(args.output, config)
    except OSError as exc:
        print(f"Cannot create report ({type(exc).__name__}); no requests made", file=sys.stderr)
        return 1
    exit_code = 0
    try:
        asyncio.run(run(args, recorder))
    except KeyboardInterrupt:
        recorder.report["status"] = "interrupted"
        exit_code = 130
    except Exception as exc:
        recorder.report.update(status="failed", errorKind=type(exc).__name__)
        reason = str(exc) if isinstance(exc, BenchmarkError) else type(exc).__name__
        print(f"Benchmark stopped ({reason}); inspect report", file=sys.stderr)
        exit_code = 1
    finally:
        recorder.report["finishedAt"] = datetime.now(UTC).isoformat()
        recorder.report["summary"] = summarize(recorder.report["samples"])
        recorder.save()
    for label, summary in recorder.report["summary"].items():
        if summary["count"]:
            print(
                f"{label:18} n={summary['count']} failed={summary['failed']} "
                f"p50={summary['p50Ms']:.3f} ms p95={summary['p95Ms']:.3f} ms"
            )
    print(
        f"Status: {recorder.report['status']}; restoration: {recorder.report['restoration']['status']}"
    )
    return exit_code or int(recorder.report["status"] != "completed")


if __name__ == "__main__":
    raise SystemExit(main())
