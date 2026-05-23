from __future__ import annotations

import json
import threading
import unittest
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from zhiyun_light_control.models import CommandResult, Scene
from zhiyun_light_control.presets import ScenePresetLibrary
from zhiyun_light_control.protocol import (
    RuntimeCommand,
    build_frame,
    build_runtime_frame,
    first_frame,
)
from zhiyun_light_control.server import LightHttpServer


@dataclass(frozen=True)
class FakeProbe:
    def to_dict(self):
        return {"firmware": "test", "device_id": 1}


class FakeLight:
    def __init__(self) -> None:
        self.commands: list[int] = []
        self.frame_exchanges: list[tuple[int, int, bytes, float]] = []

    def __enter__(self) -> FakeLight:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return

    def probe(self) -> FakeProbe:
        return FakeProbe()

    def exchange_runtime(self, cmd: int, payload: bytes = b"", *, timeout: float = 0.8):
        del payload, timeout
        self.commands.append(cmd)
        tx = build_runtime_frame(1, cmd)
        payload_by_cmd = {
            RuntimeCommand.DEVICE_INFO: b"device-test\x00pl103\x00",
            RuntimeCommand.FIRMWARE: b"1.6.4\x00",
            RuntimeCommand.VOLTAGE: b"\x65",
            RuntimeCommand.DEVICE_ID: b"\x00\x00",
        }
        rx = build_runtime_frame(1, cmd, payload_by_cmd.get(cmd, b"\x00"))
        ack = first_frame(rx, cmd=cmd)
        return CommandResult(cmd, tx, rx, (ack,), ack)

    def exchange_frame(
        self,
        first_word: int,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ):
        self.frame_exchanges.append((first_word, cmd, payload, timeout))
        tx = build_frame(first_word, 1, cmd, payload)
        rx = build_frame(first_word, 1, cmd, b"\x00")
        ack = first_frame(rx, cmd=cmd)
        return CommandResult(cmd, tx, rx, (ack,), ack)

    def apply_scene(self, scene: Scene):
        results = []
        if scene.brightness is not None:
            results.append(self.exchange_runtime(0x1001))
        if scene.kelvin is not None:
            results.append(self.exchange_runtime(0x1002))
        return results

    def transition_scene(
        self,
        start: Scene,
        end: Scene,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
    ):
        del start, duration, easing
        batches = []
        for index in range(1, steps + 1):
            brightness = None
            if end.brightness is not None:
                brightness = end.brightness * index / steps
            batches.append(
                self.apply_scene(
                    Scene(obj=end.obj, brightness=brightness, kelvin=end.kelvin)
                )
            )
        return batches


class FakeUnconfirmedLight(FakeLight):
    def exchange_runtime(self, cmd: int, payload: bytes = b"", *, timeout: float = 0.8):
        del payload, timeout
        self.commands.append(cmd)
        tx = build_runtime_frame(1, cmd)
        return CommandResult(cmd, tx, b"", (), None)


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
            state = json.loads(urlopen(f"{base}/state", timeout=3).read())
            self.assertIsNone(state["scene"])

            request = Request(
                f"{base}/scene",
                data=json.dumps({"obj": 1, "brightness": 30, "kelvin": 5600}).encode(),
                headers={"content-type": "application/json"},
                method="POST",
            )
            scene = json.loads(urlopen(request, timeout=3).read())
            self.assertEqual(
                [result["command"] for result in scene["results"]], [0x1001, 0x1002]
            )
            self.assertEqual(light.commands, [0x1001, 0x1002])
            state = json.loads(urlopen(f"{base}/state", timeout=3).read())
            self.assertEqual(state["source"], "http")
            self.assertEqual(state["action"], "scene")
            self.assertTrue(state["applied"])
            self.assertEqual(state["scene"]["brightness"], 30.0)
        finally:
            server.shutdown()
            server.server_close()

    def test_http_state_marks_unacknowledged_control_unapplied(self) -> None:
        light = FakeUnconfirmedLight()
        server = LightHttpServer(
            ("127.0.0.1", 0),
            allow_control=True,
            light_factory=lambda: light,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            request = Request(
                f"{base}/brightness",
                data=json.dumps({"obj": 1, "value": 30}).encode(),
                headers={"content-type": "application/json"},
                method="POST",
            )
            result = json.loads(urlopen(request, timeout=3).read())
            self.assertFalse(result["acknowledged"])

            state = json.loads(urlopen(f"{base}/state", timeout=3).read())
            self.assertEqual(state["source"], "http")
            self.assertEqual(state["action"], "brightness")
            self.assertFalse(state["applied"])
            self.assertEqual(state["reason"], "sent_no_response")
            self.assertEqual(state["result_statuses"], ["sent_no_response"])
        finally:
            server.shutdown()
            server.server_close()

    def test_http_lists_and_applies_preset(self) -> None:
        light = FakeLight()
        library = ScenePresetLibrary.from_mapping(
            {"scenes": {"key": {"brightness": 40, "kelvin": 5200}}}
        )
        server = LightHttpServer(
            ("127.0.0.1", 0),
            allow_control=True,
            light_factory=lambda: light,
            preset_library=library,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            presets = json.loads(urlopen(f"{base}/presets", timeout=3).read())
            self.assertEqual(sorted(presets["scenes"]), ["key"])

            request = Request(
                f"{base}/preset",
                data=json.dumps({"name": "key", "brightness": 55}).encode(),
                headers={"content-type": "application/json"},
                method="POST",
            )
            response = json.loads(urlopen(request, timeout=3).read())
            self.assertEqual(response["preset"], "key")
            self.assertEqual(response["scene"]["brightness"], 55.0)
            self.assertEqual(response["scene"]["kelvin"], 5200)
            self.assertEqual(
                [result["command"] for result in response["results"]], [0x1001, 0x1002]
            )
            state = json.loads(urlopen(f"{base}/state", timeout=3).read())
            self.assertEqual(state["action"], "preset")
            self.assertEqual(state["scene"]["brightness"], 55.0)
        finally:
            server.shutdown()
            server.server_close()

    def test_http_transition_uses_tracked_state_as_default_start(self) -> None:
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
            scene_request = Request(
                f"{base}/scene",
                data=json.dumps({"obj": 1, "brightness": 10}).encode(),
                headers={"content-type": "application/json"},
                method="POST",
            )
            json.loads(urlopen(scene_request, timeout=3).read())

            transition_request = Request(
                f"{base}/transition",
                data=json.dumps(
                    {"to": {"brightness": 30}, "steps": 2, "duration": 0}
                ).encode(),
                headers={"content-type": "application/json"},
                method="POST",
            )
            response = json.loads(urlopen(transition_request, timeout=3).read())
            self.assertEqual(response["from"]["brightness"], 10.0)
            self.assertEqual(response["scene"]["brightness"], 30.0)
            self.assertEqual(response["steps"], 2)
            self.assertEqual(len(response["batches"]), 2)

            state = json.loads(urlopen(f"{base}/state", timeout=3).read())
            self.assertEqual(state["action"], "transition")
            self.assertEqual(state["scene"]["brightness"], 30.0)
        finally:
            server.shutdown()
            server.server_close()

    def test_http_validate_read_only_without_control_gate(self) -> None:
        light = FakeLight()
        server = LightHttpServer(
            ("127.0.0.1", 0),
            allow_control=False,
            light_factory=lambda: light,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            commands = json.loads(urlopen(f"{base}/commands", timeout=3).read())
            self.assertIn("/status", commands["get"])
            self.assertIn("/validate", commands["get"])
            self.assertIn("/validate", commands["post"])

            status = json.loads(urlopen(f"{base}/status", timeout=3).read())
            self.assertTrue(status["connection_confirmed"])
            self.assertEqual(status["device_identifier"], "device-test")
            self.assertEqual(status["generation"], "pl103")
            self.assertEqual(status["firmware"], "1.6.4")
            self.assertEqual(status["voltage_status"], 0x65)
            self.assertEqual(status["device_id"], 0)
            self.assertTrue(status["commands"]["voltage"]["acknowledged"])

            report = json.loads(urlopen(f"{base}/validate", timeout=3).read())
            self.assertTrue(report["connection_confirmed"])
            self.assertFalse(report["control_enabled"])
            self.assertEqual(report["unconfirmed"], [])
            self.assertEqual([check["name"] for check in report["checks"]], ["probe"])

            request = Request(
                f"{base}/validate",
                data=json.dumps({"allow_control": True}).encode(),
                headers={"content-type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=3).read()
            self.assertEqual(raised.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()

    def test_http_validate_can_run_control_checks_when_enabled(self) -> None:
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
            request = Request(
                f"{base}/validate",
                data=json.dumps(
                    {
                        "allow_control": True,
                        "include_object_reads": True,
                        "brightness": 32,
                        "kelvin": 5400,
                    }
                ).encode(),
                headers={"content-type": "application/json"},
                method="POST",
            )
            report = json.loads(urlopen(request, timeout=3).read())
            self.assertTrue(report["connection_confirmed"])
            self.assertTrue(report["control_enabled"])
            self.assertEqual(report["unconfirmed"], [])
            self.assertTrue(report["all_attempted_confirmed"])
            names = [check["name"] for check in report["checks"]]
            self.assertIn("read_brightness", names)
            self.assertIn("register_default_group", names)
            self.assertIn("set_brightness", names)
        finally:
            server.shutdown()
            server.server_close()

    def test_http_frame_exchange_uses_raw_frame_api(self) -> None:
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
            request = Request(
                f"{base}/frame",
                data=json.dumps(
                    {
                        "first_word": "0x0100",
                        "command": "0x2001",
                        "payload_hex": "00 01",
                        "timeout": 0.25,
                    }
                ).encode(),
                headers={"content-type": "application/json"},
                method="POST",
            )
            result = json.loads(urlopen(request, timeout=3).read())

            self.assertTrue(result["acknowledged"])
            self.assertEqual(result["command"], 0x2001)
            self.assertEqual(
                light.frame_exchanges,
                [(0x0100, 0x2001, b"\x00\x01", 0.25)],
            )
        finally:
            server.shutdown()
            server.server_close()

    def test_http_exposes_openapi_schema_and_configurable_cors(self) -> None:
        light = FakeLight()
        server = LightHttpServer(
            ("127.0.0.1", 0),
            light_factory=lambda: light,
            cors_origin="http://studio.local",
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            response = urlopen(f"{base}/openapi.json", timeout=3)
            schema = json.loads(response.read())
            self.assertEqual(
                response.headers["access-control-allow-origin"],
                "http://studio.local",
            )
            self.assertEqual(schema["openapi"], "3.1.0")
            self.assertIn("/scene", schema["paths"])
            self.assertIn("/status", schema["paths"])
            self.assertIn("/frame", schema["paths"])
            self.assertIn("FrameRequest", schema["components"]["schemas"])
            self.assertIn("CommandResult", schema["components"]["schemas"])
            self.assertIn("Status", schema["components"]["schemas"])

            commands = json.loads(urlopen(f"{base}/commands", timeout=3).read())
            self.assertIn("/openapi.json", commands["get"])
            self.assertIn("/status", commands["get"])
            self.assertIn("/frame", commands["post"])

            options = Request(f"{base}/scene", method="OPTIONS")
            options_response = urlopen(options, timeout=3)
            self.assertEqual(options_response.status, 204)
            self.assertEqual(
                options_response.headers["access-control-allow-methods"],
                "GET, POST, OPTIONS",
            )
        finally:
            server.shutdown()
            server.server_close()

    def test_http_cors_can_be_disabled(self) -> None:
        light = FakeLight()
        server = LightHttpServer(
            ("127.0.0.1", 0),
            light_factory=lambda: light,
            cors_origin=None,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            response = urlopen(f"{base}/health", timeout=3)
            self.assertIsNone(response.headers.get("access-control-allow-origin"))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
