from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from zhiyun_light_control import (
    LightBridgeClient,
    LightBridgeError,
    LightBridgeNotReady,
    bridge_response_applied,
    bridge_response_reason,
    bridge_response_statuses,
    command_result_acknowledged,
    command_result_status,
    devices_ble_authorization,
    devices_ble_blocker,
    devices_ble_scan_devices,
    devices_ble_scan_ok,
    devices_ble_state,
    devices_ble_status,
    devices_selected_usb_port,
    devices_usb_available,
    devices_usb_ports,
    readiness_actions_by_id,
    readiness_all_ready,
    readiness_pending_action_ids,
    readiness_ready,
    readiness_ready_for,
    readiness_require,
    readiness_requirement,
    readiness_requirements,
    readiness_unready_capabilities,
    readiness_warnings,
    validation_category,
    validation_ready,
    validation_ready_for,
    validation_summary,
    validation_unconfirmed_names,
)
from zhiyun_light_control.cues import CueLibrary
from zhiyun_light_control.models import CommandResult, Scene
from zhiyun_light_control.presets import ScenePresetLibrary
from zhiyun_light_control.protocol import (
    RuntimeCommand,
    brightness_payload,
    build_frame,
    build_runtime_frame,
    cct_payload,
    first_frame,
)
from zhiyun_light_control.server import LightHttpServer
from zhiyun_light_control.transports.ble import (
    BleCharacteristic,
    BleInspectResult,
    BleService,
)


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

    def exchange_frame(
        self,
        first_word: int,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ):
        del timeout
        self.commands.append((cmd, payload))
        tx = build_frame(first_word, 1, cmd, payload)
        rx = build_frame(first_word, 1, cmd, b"\x00")
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
        cue_library = CueLibrary.from_mapping(
            {"intro": {"steps": [{"scene": {"brightness": 18}}]}}
        )
        server = LightHttpServer(
            ("127.0.0.1", 0),
            allow_control=True,
            light_factory=lambda: light,
            preset_library=library,
            cue_library=cue_library,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = LightBridgeClient(f"http://127.0.0.1:{server.server_port}")
        try:
            self.assertTrue(client.health()["ok"])
            self.assertEqual(client.manifest()["setup"]["preflight"]["path"], "/ready")
            self.assertTrue(client.diagnostics()["connection_confirmed"])
            ready = client.ready()
            self.assertTrue(ready["ready_for"]["read_status"])
            self.assertTrue(ready["ready_for"]["control_requests"])
            self.assertFalse(ready["ready_for"]["confirmed_control"])
            self.assertTrue(readiness_ready(ready, "read_status"))
            self.assertTrue(readiness_ready_for(ready)["control_requests"])
            self.assertTrue(readiness_all_ready(ready, ["read_status"]))
            self.assertEqual(
                readiness_unready_capabilities(ready, ["confirmed_control"]),
                ["confirmed_control"],
            )
            with self.assertRaises(LightBridgeNotReady) as readiness_error:
                readiness_require(ready, ["confirmed_control"])
            self.assertEqual(
                readiness_error.exception.pending_action_ids,
                {"confirmed_control": ["confirm-control"]},
            )
            self.assertEqual(readiness_warnings(ready), ready["warnings"])
            requirements = readiness_requirements(ready)
            self.assertEqual(
                requirements["confirmed_control"]["pending_actions"],
                ["confirm-control"],
            )
            self.assertEqual(
                readiness_requirement(ready, "control_requests")["actions"],
                ["enable-control"],
            )
            self.assertEqual(
                readiness_pending_action_ids(
                    ready,
                    capability="confirmed_control",
                ),
                ["confirm-control"],
            )
            actions = readiness_actions_by_id(ready)
            self.assertTrue(actions["enable-control"]["ready"])
            self.assertFalse(actions["confirm-control"]["ready"])
            self.assertEqual(actions["confirm-control"]["blocked_by"], [])
            self.assertEqual(
                client.readiness_action("confirm-control")["path"],
                "/validate",
            )
            pending = {
                action["id"]: action for action in client.pending_readiness_actions()
            }
            self.assertEqual(list(pending), ["confirm-control"])
            self.assertEqual(
                client.readiness_actions(include_ready=False),
                pending,
            )
            self.assertTrue(client.readiness_ready("read_status"))
            self.assertEqual(
                client.readiness_pending_action_ids(
                    capability="confirmed_control",
                ),
                ["confirm-control"],
            )
            self.assertEqual(
                client.readiness_requirement("control_requests")["actions"],
                ["enable-control"],
            )
            self.assertEqual(client.readiness_warnings(), ready["warnings"])
            self.assertTrue(
                client.require_readiness("read_status")["ready_for"]["read_status"]
            )
            self.assertTrue(
                client.wait_until_ready(
                    "read_status",
                    timeout=0,
                    interval=0,
                )["ready_for"]["read_status"]
            )
            with self.assertRaises(LightBridgeNotReady) as wait_error:
                client.wait_until_ready(
                    "confirmed_control",
                    timeout=0,
                    interval=0,
                )
            self.assertEqual(
                wait_error.exception.pending_action_ids,
                {"confirmed_control": ["confirm-control"]},
            )
            self.assertIn("/brightness", client.commands()["post"])
            self.assertIn("brightness", client.capabilities()["scene_fields"])
            with patch(
                "zhiyun_light_control.devices.macos_ble_app_status",
                return_value={
                    "ok": False,
                    "state": "unauthorized",
                    "authorization": "denied",
                },
            ) as ble_status:
                devices = client.devices(include_ble_status=True, timeout=0.1)
            self.assertIn("usb", devices)
            self.assertFalse(devices["ble"]["included"])
            self.assertEqual(devices["ble"]["macos_status"]["state"], "unauthorized")
            ble_status.assert_called_once_with(timeout=0.1)
            self.assertEqual(sorted(client.cues()["cues"]), ["intro"])
            inspect_result = BleInspectResult(
                ok=True,
                address="UUID-1",
                services=(
                    BleService(
                        uuid="service",
                        characteristics=(
                            BleCharacteristic(
                                uuid="write",
                                properties=("write",),
                            ),
                        ),
                    ),
                ),
                worker_python="macos-app",
            )
            with patch(
                "zhiyun_light_control.server.inspect_ble_device",
                return_value=inspect_result,
            ) as inspect_ble:
                ble = client.inspect_ble(
                    backend="macos-app",
                    name_contains="PL103",
                    timeout=1,
                )
            self.assertTrue(ble["ok"])
            self.assertEqual(ble["address"], "UUID-1")
            self.assertEqual(ble["services"][0]["characteristics"][0]["uuid"], "write")
            inspect_ble.assert_called_once_with(
                backend="macos-app",
                timeout=1.0,
                address=None,
                name_contains="PL103",
                python=None,
            )

            class FakeEndpointReport:
                def to_dict(self):
                    return {
                        "ok": True,
                        "backend": "macos-app",
                        "tests": [{"acknowledged": True}],
                    }

            with patch(
                "zhiyun_light_control.server.test_ble_endpoint_candidates",
                return_value=FakeEndpointReport(),
            ) as test_ble:
                endpoint_test = client.test_ble_endpoints(
                    backend="macos-app",
                    name_contains="PL103",
                    timeout=1,
                    max_candidates=2,
                )
            self.assertTrue(endpoint_test["ok"])
            self.assertTrue(endpoint_test["tests"][0]["acknowledged"])
            test_ble.assert_called_once_with(
                backend="macos-app",
                timeout=1.0,
                address=None,
                name_contains="PL103",
                python=None,
                max_candidates=2,
            )

            plan = client.plan(
                {
                    "preset": "key",
                    "overrides": {"brightness": 44},
                    "control_mode": "0x01",
                }
            )
            self.assertTrue(plan["dry_run"])
            self.assertEqual(plan["action"], "preset")
            self.assertEqual(plan["control_mode"], 1)
            self.assertEqual(plan["scene"]["brightness"], 44.0)
            discovery = client.discover_usb(
                object_ids=[1],
                first_words=["0x0100"],
                timeout=0.1,
            )
            self.assertFalse(discovery["control_enabled"])
            self.assertEqual(discovery["object_ids"], [1])
            self.assertEqual(discovery["first_words"], [0x0100])
            post_register = client.discover_usb(
                allow_control=True,
                object_ids=[1],
                first_words=[0x0100],
                control_object_ids=[1],
                register_device_ids=[1],
                register_group_ids=[0],
                control_kinds=[],
                post_register_reads=True,
                timeout=0.1,
            )
            self.assertTrue(post_register["post_register_reads"])
            self.assertEqual(
                post_register["summary"]["post_register_reads"]["attempted"],
                9,
            )
            self.assertEqual(client.status()["firmware"], "1.6.4")

            brightness = client.set_brightness(35, obj=1, control_mode=0x01)
            self.assertTrue(brightness["acknowledged"])
            self.assertTrue(command_result_acknowledged(brightness))
            self.assertEqual(command_result_status(brightness), "acknowledged")
            self.assertTrue(bridge_response_applied(brightness))
            self.assertEqual(bridge_response_statuses(brightness), ["acknowledged"])
            self.assertEqual(light.commands[-1][1][2], 0x01)
            history = client.history(limit=1)
            self.assertEqual(history["version"], 1)
            self.assertEqual(history["events"][0]["state"]["action"], "brightness")
            self.assertEqual(
                history["events"][0]["state"]["result_summaries"][0]["command"],
                RuntimeCommand.BRIGHTNESS,
            )
            self.assertTrue(bridge_response_applied(history))
            self.assertEqual(bridge_response_statuses(history), ["acknowledged"])

            scene = client.apply_scene(Scene(obj=1, brightness=42, kelvin=5600))
            self.assertEqual(
                [item["command"] for item in scene["results"]],
                [0x1001, 0x1002],
            )
            self.assertTrue(bridge_response_applied(scene))
            self.assertEqual(
                bridge_response_statuses(scene),
                ["acknowledged", "acknowledged"],
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

            named_cue = client.run_named_cue("intro", control_mode=0x01)
            self.assertEqual(named_cue["cue"], "intro")
            self.assertTrue(named_cue["applied"])
            self.assertEqual(named_cue["steps"][0]["scene"]["brightness"], 18.0)

            validation = client.validate(allow_control=True, values={"brightness": 32})
            self.assertTrue(validation["connection_confirmed"])
            self.assertTrue(validation_ready(validation, "control_writes"))
            self.assertTrue(validation_ready_for(validation)["control_setup"])
            self.assertEqual(validation_category(validation, "control")["confirmed"], 4)
            self.assertEqual(validation_unconfirmed_names(validation), [])
            self.assertEqual(validation_summary(validation)["unconfirmed"], 0)
        finally:
            server.shutdown()
            server.server_close()

    def test_bridge_response_helpers_normalize_unconfirmed_payloads(self) -> None:
        command = {
            "acknowledged": False,
            "transport_status": "sent_no_response",
        }
        scene = {
            "applied": False,
            "reason": "sent_no_response",
            "results": [command],
        }

        self.assertFalse(command_result_acknowledged(command))
        self.assertEqual(command_result_status(command), "sent_no_response")
        self.assertFalse(bridge_response_applied(scene))
        self.assertEqual(bridge_response_reason(scene), "sent_no_response")
        self.assertEqual(bridge_response_statuses(scene), ["sent_no_response"])

        echoed = {"results": [{"transport_status": "echoed_write"}]}
        self.assertFalse(bridge_response_applied(echoed))
        self.assertEqual(bridge_response_reason(echoed), "echoed_write")

    def test_validation_helpers_normalize_summary_payloads(self) -> None:
        payload = {
            "unconfirmed": ["set_sleep"],
            "summary": {
                "unconfirmed": 1,
                "ready_for": {
                    "read_status": True,
                    "control_setup": True,
                    "control_writes": False,
                    "ignored": "yes",
                },
                "categories": {
                    "control": {
                        "confirmed": 1,
                        "unconfirmed_names": ["set_sleep"],
                    }
                },
            },
        }

        self.assertEqual(validation_summary(payload)["unconfirmed"], 1)
        self.assertEqual(
            validation_ready_for(payload),
            {
                "read_status": True,
                "control_setup": True,
                "control_writes": False,
            },
        )
        self.assertTrue(validation_ready(payload, "read_status"))
        self.assertFalse(validation_ready(payload, "control_writes"))
        self.assertFalse(validation_ready(payload, "object_reads"))
        self.assertEqual(validation_category(payload, "control")["confirmed"], 1)
        self.assertEqual(
            validation_unconfirmed_names(payload, category="control"),
            ["set_sleep"],
        )
        self.assertEqual(validation_unconfirmed_names(payload), ["set_sleep"])

    def test_readiness_helpers_derive_pending_action_chains(self) -> None:
        payload = {
            "ready_for": {"confirmed_control": False},
            "actions": [
                {
                    "id": "enable-control",
                    "label": "Enable control",
                    "ready": False,
                    "category": "control",
                    "required_for": ["control_requests"],
                },
                {
                    "id": "confirm-control",
                    "label": "Confirm control",
                    "ready": False,
                    "category": "control",
                    "required_for": ["confirmed_control"],
                    "blocked_by": ["enable-control"],
                },
            ],
            "warnings": ["No ACK-confirmed control request is recorded yet."],
        }

        self.assertFalse(readiness_ready(payload, "confirmed_control"))
        self.assertEqual(
            readiness_pending_action_ids(
                payload,
                capability="confirmed_control",
            ),
            ["enable-control", "confirm-control"],
        )
        self.assertEqual(readiness_warnings(payload), payload["warnings"])

    def test_device_helpers_normalize_discovery_and_readiness_payloads(self) -> None:
        devices = {
            "usb": {
                "available": True,
                "selected_port": "/dev/cu.usbmodem21301",
                "ports": [
                    {
                        "path": "/dev/cu.usbmodem21301",
                        "selected": True,
                    }
                ],
            },
            "ble": {
                "macos_status": {
                    "ok": False,
                    "authorization": "not_determined",
                    "state": "unauthorized",
                    "error": "Bluetooth state unauthorized: 3",
                },
                "scan": {
                    "ok": True,
                    "devices": [
                        {
                            "address": "UUID-1",
                            "name": "PL103_EDFE",
                        }
                    ],
                },
            },
        }
        readiness = {"devices": devices}

        self.assertTrue(devices_usb_available(devices))
        self.assertEqual(
            devices_selected_usb_port(readiness),
            "/dev/cu.usbmodem21301",
        )
        self.assertEqual(devices_usb_ports(readiness)[0]["selected"], True)
        self.assertEqual(
            devices_ble_status(readiness)["authorization"],
            "not_determined",
        )
        self.assertEqual(devices_ble_authorization(readiness), "not_determined")
        self.assertEqual(devices_ble_state(readiness), "unauthorized")
        self.assertEqual(
            devices_ble_blocker(readiness),
            "Bluetooth state unauthorized: 3",
        )
        self.assertTrue(devices_ble_scan_ok(readiness))
        self.assertEqual(
            devices_ble_scan_devices(readiness)[0]["address"],
            "UUID-1",
        )

        class FakeDevicesClient(LightBridgeClient):
            def devices(self, **_kwargs: object) -> dict[str, object]:
                return devices

        client = FakeDevicesClient("http://bridge.test")
        self.assertEqual(
            client.devices_selected_usb_port(),
            "/dev/cu.usbmodem21301",
        )
        self.assertEqual(client.devices_ble_authorization(), "not_determined")
        self.assertEqual(
            client.devices_ble_blocker(),
            "Bluetooth state unauthorized: 3",
        )

    def test_client_iterates_state_events(self) -> None:
        light = FakeLight()
        server = LightHttpServer(
            ("127.0.0.1", 0),
            allow_control=True,
            light_factory=lambda: light,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = LightBridgeClient(f"http://127.0.0.1:{server.server_port}")
        try:
            initial = next(client.state_events(limit=1, timeout=0.1))
            self.assertEqual(initial["version"], 0)
            self.assertIsNone(initial["state"]["scene"])

            events: list[dict[str, object]] = []

            def collect_events() -> None:
                events.extend(
                    client.state_events(limit=1, timeout=2.0, initial=False)
                )

            event_thread = threading.Thread(target=collect_events)
            event_thread.start()
            time.sleep(0.1)
            client.set_brightness(22)
            event_thread.join(timeout=3)

            self.assertFalse(event_thread.is_alive())
            self.assertEqual(events[0]["version"], 1)
            state = events[0]["state"]
            self.assertEqual(state["scene"]["brightness"], 22.0)
            self.assertEqual(state["action"], "brightness")
            self.assertEqual(state["result_summaries"][0]["command"], 0x1001)
            self.assertTrue(state["result_summaries"][0]["acknowledged"])
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
