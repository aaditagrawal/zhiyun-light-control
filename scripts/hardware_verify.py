"""Explicit opt-in G60 hardware test; always attempts to restore initial settings."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from lighttime import LightClient


async def capture(camera: str, destination: Path):
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "v4l2",
        "-video_size",
        "640x480",
        "-i",
        camera,
        "-vf",
        r"select=gte(n\,10)",
        "-frames:v",
        "1",
        "-update",
        "1",
        "-y",
        str(destination),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, error = await asyncio.wait_for(process.communicate(), 10)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(error.decode(errors="replace"))


async def main(args):
    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=True)
    report = {"date": datetime.now(UTC).isoformat(), "steps": [], "restored": False}
    try:
        async with LightClient(args.address) as light:
            baseline = light.state
            report["baseline"] = baseline.to_dict()
            try:
                steps = [
                    ("dim", {"brightness": 2, "cct": 5000}),
                    ("bright", {"brightness": 12}),
                    ("off", {"power": False}),
                    ("warm", {"brightness": 5, "cct": 2700}),
                    ("cool", {"cct": 6500}),
                ]
                for name, changes in steps:
                    state = await light.set(**changes)
                    print(name, json.dumps(state.to_dict()), flush=True)
                    step = {"name": name, "requested": changes, "state": state.to_dict()}
                    report["steps"].append(step)
                    if args.camera:
                        image_path = destination / f"{name}.jpg"
                        await capture(args.camera, image_path)
                        step["image"] = str(image_path)
            finally:
                state = await light.set(brightness=baseline.brightness, cct=baseline.cct)
                report["restored"] = True
                report["final"] = state.to_dict()
                print("restored", json.dumps(state.to_dict()), flush=True)
    finally:
        (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--camera", help="Linux V4L2 path, e.g. /dev/video0")
    parser.add_argument("--output", default="artifacts/hardware")
    asyncio.run(main(parser.parse_args()))
