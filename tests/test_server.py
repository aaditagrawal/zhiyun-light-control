from __future__ import annotations

import json
import threading
import unittest
from dataclasses import dataclass
from urllib.request import Request, urlopen

from zhiyun_light_control.models import CommandResult, Scene
from zhiyun_light_control.protocol import build_runtime_frame, first_frame
from zhiyun_light_control.server import LightHttpServer


@dataclass(frozen=True)
class FakeProbe:
    def to_dict(self):
        return {"firmware": "test", "device_id": 1}


class FakeLight:
    def __init__(self) -> None:
        self.commands: list[int] = []

    def __enter__(self) -> "FakeLight":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return

    def probe(self) -> FakeProbe:
        return FakeProbe()

    def exchange_runtime(self, cmd: int, payload: bytes = b"", *, timeout: float = 0.8):
        del payload, timeout
        self.commands.append(cmd)
        tx = build_runtime_frame(1, cmd)
        rx = build_runtime_frame(1, cmd, b"\x00")
        ack = first_frame(rx, cmd=cmd)
        return CommandResult(cmd, tx, rx, (ack,), ack)

    def apply_scene(self, scene: Scene):
        results = []
        if scene.brightness is not None:
            results.append(self.exchange_runtime(0x1001))
        if scene.kelvin is not None:
            results.append(self.exchange_runtime(0x1002))
        return results


class ServerTests(unittest.TestCase):
    def test_http_probe_and_scene(self) -> None:
        light = FakeLight()
        server = LightHttpServer(
            ("127.0.0.1", 0),
            allow_control=True,
            light_factory=lambda: light,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            probe = json.loads(urlopen(f"{base}/probe", timeout=3).read())
            self.assertEqual(probe["firmware"], "test")

            request = Request(
                f"{base}/scene",
                data=json.dumps({"obj": 1, "brightness": 30, "kelvin": 5600}).encode(),
                headers={"content-type": "application/json"},
                method="POST",
            )
            scene = json.loads(urlopen(request, timeout=3).read())
            self.assertEqual([result["command"] for result in scene["results"]], [0x1001, 0x1002])
            self.assertEqual(light.commands, [0x1001, 0x1002])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()

