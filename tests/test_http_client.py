from __future__ import annotations

import threading
import unittest

from zhiyun_light_control import LightBridgeClient, LightBridgeError
from zhiyun_light_control.models import CommandResult, Scene
from zhiyun_light_control.presets import ScenePresetLibrary
from zhiyun_light_control.protocol import (
    RuntimeCommand,
    brightness_payload,
    build_runtime_frame,
    cct_payload,
    first_frame,
)
from zhiyun_light_control.server import LightHttpServer


class FakeProbe:
    def to_dict(self):
        return {
            "device_identifier": "device-test",
            "firmware": "1.6.4",
            "generation": "pl103",
            "device_id": 0,
            "voltage_status": 101,
        }


class FakeLight:
    def __init__(self) -> None:
        self.commands: list[tuple[int, bytes]] = []

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return

    def probe(self) -> FakeProbe:
        return FakeProbe()

    def exchange_runtime(self, cmd: int, payload: bytes = b"", *, timeout: float = 0.8):
        del timeout
        self.commands.append((cmd, payload))
        payload_by_cmd = {
            RuntimeCommand.DEVICE_INFO: b"device-test\x00pl103\x00",
            RuntimeCommand.FIRMWARE: b"1.6.4\x00",
            RuntimeCommand.VOLTAGE: b"\x65",
            RuntimeCommand.DEVICE_ID: b"\x00\x00",
        }
        tx = build_runtime_frame(1, cmd, payload)
        rx = build_runtime_frame(1, cmd, payload_by_cmd.get(cmd, b"\x00"))
        ack = first_frame(rx, cmd=cmd)
        return CommandResult(cmd, tx, rx, (ack,), ack)

    def apply_scene(self, scene: Scene, *, control_mode: int = 0x33):
        results = []
        if scene.brightness is not None:
            results.append(
                self.exchange_runtime(
                    RuntimeCommand.BRIGHTNESS,
                    brightness_payload(
                        scene.obj,
                        scene.brightness,
                        control_mode=control_mode,
                    ),
                )
            )
        if scene.kelvin is not None:
            results.append(
                self.exchange_runtime(
                    RuntimeCommand.CCT,
                    cct_payload(scene.obj, scene.kelvin, control_mode=control_mode),
                )
            )
        return results

    def transition_scene(
        self,
        start: Scene,
        end: Scene,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
        control_mode: int = 0x33,
    ):
        del start, duration, easing
        return [
            self.apply_scene(
                Scene(obj=end.obj, brightness=end.brightness, kelvin=end.kelvin),
                control_mode=control_mode,
            )
            for _ in range(steps)
        ]


class HttpClientTests(unittest.TestCase):
    def test_client_reads_metadata_and_sends_control_payloads(self) -> None:
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
        client = LightBridgeClient(f"http://127.0.0.1:{server.server_port}")
        try:
            self.assertTrue(client.health()["ok"])
            self.assertTrue(client.diagnostics()["connection_confirmed"])
            self.assertIn("/brightness", client.commands()["post"])
            self.assertIn("brightness", client.capabilities()["scene_fields"])
            self.assertEqual(client.status()["firmware"], "1.6.4")

            brightness = client.set_brightness(35, obj=1, control_mode=0x01)
            self.assertTrue(brightness["acknowledged"])
            self.assertEqual(light.commands[-1][1][2], 0x01)

            scene = client.apply_scene(Scene(obj=1, brightness=42, kelvin=5600))
            self.assertEqual(
                [item["command"] for item in scene["results"]],
                [0x1001, 0x1002],
            )

            transition = client.transition(
                {"brightness": 20},
                from_scene={"brightness": 10},
                steps=2,
                duration=0,
            )
            self.assertEqual(transition["steps"], 2)

            preset = client.apply_preset("key", overrides={"brightness": 55})
            self.assertEqual(preset["scene"]["brightness"], 55.0)

            sequence = client.run_sequence(
                [
                    {"scene": {"brightness": 10}},
                    {"preset": "key", "overrides": {"brightness": 45}},
                ],
                control_mode=0x01,
            )
            self.assertTrue(sequence["applied"])
            self.assertEqual(
                [step["action"] for step in sequence["steps"]],
                ["scene", "preset"],
            )

            cue = client.run_cue(
                {
                    "steps": [
                        {"scene": {"brightness": 12}},
                        {"preset": "key", "overrides": {"brightness": 35}},
                    ],
                    "stop_on_unconfirmed": True,
                }
            )
            self.assertTrue(cue["applied"])
            self.assertFalse(cue["stopped"])

            validation = client.validate(allow_control=True, values={"brightness": 32})
            self.assertTrue(validation["connection_confirmed"])
        finally:
            server.shutdown()
            server.server_close()

    def test_client_raises_structured_error(self) -> None:
        server = LightHttpServer(("127.0.0.1", 0), allow_control=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = LightBridgeClient(f"http://127.0.0.1:{server.server_port}")
        try:
            with self.assertRaises(LightBridgeError) as raised:
                client.set_sleep(1)
            self.assertEqual(raised.exception.status, 403)
            self.assertIn("control endpoints", raised.exception.payload["error"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
