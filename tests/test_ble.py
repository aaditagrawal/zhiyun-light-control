from __future__ import annotations

import json
import plistlib
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from zhiyun_light_control.macos_ble_app import (
    APP_BUNDLE_ID,
    BLUETOOTH_USAGE,
    MacosBleAppRun,
    ensure_macos_ble_app,
    macos_ble_app_authorize,
    macos_ble_app_info,
    macos_ble_app_status,
)
from zhiyun_light_control.protocol import (
    RuntimeCommand,
    build_runtime_frame,
    first_frame,
)
from zhiyun_light_control.transports.ble import (
    BLE_PROFILE_NAMES,
    DIRECT_ZY_NOTIFY_UUID,
    DIRECT_ZY_SERVICE_UUID,
    DIRECT_ZY_WRITE_UUID,
    LEGACY_ZY_NOTIFY_UUID,
    LEGACY_ZY_SERVICE_UUID,
    LEGACY_ZY_WRITE_UUID,
    MESH_PROVISIONING_NOTIFY_UUID,
    MESH_PROVISIONING_SERVICE_UUID,
    MESH_PROVISIONING_WRITE_UUID,
    YC_SERVICE_UUID,
    BleCharacteristic,
    BleEndpointCandidate,
    BleExchangeResult,
    BleInspectResult,
    BleProfile,
    BleService,
    BleTransport,
    CrashIsolatedBleTransport,
    MacosBleAppTransport,
    exchange_zhiyun_ble_macos_app,
    exchange_zhiyun_ble_safe,
    exchange_zhiyun_ble_sequence_macos_app,
    inspect_zhiyun_ble_macos_app,
    inspect_zhiyun_ble_safe,
    inspect_zhiyun_device,
    open_zhiyun_ble_ipc_macos_app,
    resolve_ble_profile,
    scan_zhiyun_devices,
    scan_zhiyun_devices_macos_app,
    scan_zhiyun_devices_safe,
    suggest_ble_endpoint_candidates,
    suggest_ble_profile,
)


class FakeDevice:
    def __init__(self, address: str, name: str | None):
        self.address = address
        self.name = name


class FakeAdvertisement:
    def __init__(
        self,
        *,
        service_uuids: list[str] | None = None,
        local_name: str | None = None,
        rssi: int | None = None,
    ):
        self.service_uuids = service_uuids or []
        self.local_name = local_name
        self.rssi = rssi


class SafeBleScanTests(unittest.TestCase):
    def test_macos_ble_app_info_describes_cached_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_path = Path(tmp) / "ZhiyunBleScan.app"
            with (
                patch(
                    "zhiyun_light_control.macos_ble_app._bundle_root",
                    return_value=app_path,
                ),
                patch("zhiyun_light_control.macos_ble_app.sys.platform", "darwin"),
            ):
                info = macos_ble_app_info()

        self.assertTrue(info["ok"])
        self.assertTrue(info["available"])
        self.assertEqual(info["bundle_id"], APP_BUNDLE_ID)
        self.assertEqual(info["usage_description"], BLUETOOTH_USAGE)
        self.assertEqual(info["app_path"], str(app_path))
        self.assertFalse(info["exists"])
        self.assertIn("Privacy & Security", info["settings_hint"])
        self.assertEqual(info["status_command"], "zlight ble-helper --status --json")

    def test_macos_ble_app_status_runs_status_mode(self) -> None:
        run_result = MacosBleAppRun(
            ok=False,
            payload={
                "ok": False,
                "state": "unauthorized",
                "state_raw": 3,
                "authorization": "denied",
                "authorization_raw": 2,
                "error": "Bluetooth state unauthorized: 3",
            },
            returncode=1,
            command=("open", "-W", "ZhiyunBleScan.app"),
        )

        with patch(
            "zhiyun_light_control.macos_ble_app.run_macos_ble_app",
            return_value=run_result,
        ) as run:
            status = macos_ble_app_status(timeout=1.25)

        self.assertFalse(status["ok"])
        self.assertEqual(status["state"], "unauthorized")
        self.assertEqual(status["authorization"], "denied")
        self.assertEqual(status["bundle_id"], APP_BUNDLE_ID)
        self.assertIn("Privacy & Security", status["settings_hint"])
        self.assertEqual(status["command"], ["open", "-W", "ZhiyunBleScan.app"])
        run.assert_called_once_with(
            ["status", "--timeout", "1.25"],
            timeout=1.25,
            bundle_name="ZhiyunBleScan",
            bundle_id=APP_BUNDLE_ID,
        )

    def test_macos_ble_app_status_names_pending_bluetooth_prompt(self) -> None:
        run_result = MacosBleAppRun(
            ok=False,
            payload={
                "ok": False,
                "state": "unknown",
                "state_raw": 0,
                "authorization": "not_determined",
                "authorization_raw": 0,
                "error": "Bluetooth status timed out",
            },
            returncode=0,
        )

        with patch(
            "zhiyun_light_control.macos_ble_app.run_macos_ble_app",
            return_value=run_result,
        ):
            status = macos_ble_app_status(timeout=1.25)

        self.assertEqual(status["pending_action"], "allow_bluetooth_prompt")
        self.assertIn("permission prompt", status["error"])

    def test_macos_ble_app_authorize_waits_for_prompt_decision(self) -> None:
        run_result = MacosBleAppRun(
            ok=True,
            payload={
                "ok": True,
                "state": "powered on",
                "state_raw": 5,
                "authorization": "allowed",
                "authorization_raw": 3,
                "error": None,
            },
            returncode=0,
        )

        with patch(
            "zhiyun_light_control.macos_ble_app.run_macos_ble_app",
            return_value=run_result,
        ) as run:
            status = macos_ble_app_authorize(timeout=30.0)

        self.assertTrue(status["ok"])
        self.assertEqual(status["authorization"], "allowed")
        self.assertEqual(status["authorize_timeout"], 30.0)
        run.assert_called_once_with(
            ["authorize", "--timeout", "30.0"],
            timeout=30.0,
            bundle_name="ZhiyunBleScan",
            bundle_id=APP_BUNDLE_ID,
        )

    def test_macos_ble_app_ensure_compiles_visible_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_path = Path(tmp) / "ZhiyunBleScan.app"
            compile_calls = []
            sign_calls = []

            def fake_run(args, **_kwargs):
                if args[0] == "/usr/bin/swiftc":
                    compile_calls.append(args)
                    Path(args[-1]).write_bytes(b"mach-o")
                elif args[0] == "/usr/bin/codesign":
                    sign_calls.append(args)
                return types.SimpleNamespace(returncode=0, stderr="", stdout="")

            with (
                patch(
                    "zhiyun_light_control.macos_ble_app._bundle_root",
                    return_value=app_path,
                ),
                patch(
                    "zhiyun_light_control.macos_ble_app.shutil.which",
                    side_effect=lambda name: f"/usr/bin/{name}",
                ),
                patch("zhiyun_light_control.macos_ble_app.subprocess.run", fake_run),
            ):
                result = ensure_macos_ble_app(swift_path="/usr/bin/swiftc")

            info = plistlib.loads((app_path / "Contents" / "Info.plist").read_bytes())
            self.assertEqual(result, app_path)
            self.assertEqual(compile_calls[0][0], "/usr/bin/swiftc")
            self.assertEqual(
                sign_calls[0][:4],
                ["/usr/bin/codesign", "--force", "--deep", "--sign"],
            )
            self.assertIn("--requirements", sign_calls[0])
            self.assertIn(
                f'=designated => identifier "{APP_BUNDLE_ID}"',
                sign_calls[0],
            )
            self.assertEqual(sign_calls[0][-1], str(app_path))
            self.assertEqual(info["CFBundleIdentifier"], APP_BUNDLE_ID)
            self.assertNotIn("LSBackgroundOnly", info)
            self.assertTrue(
                (app_path / "Contents" / "MacOS" / "ZhiyunBleScan").exists()
            )

    def test_macos_ble_app_can_use_fresh_bundle_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_path = Path(tmp) / "ZhiyunBleScan2.app"
            sign_calls = []

            def fake_run(args, **_kwargs):
                if args[0] == "/usr/bin/swiftc":
                    Path(args[-1]).write_bytes(b"mach-o")
                elif args[0] == "/usr/bin/codesign":
                    sign_calls.append(args)
                return types.SimpleNamespace(returncode=0, stderr="", stdout="")

            with (
                patch(
                    "zhiyun_light_control.macos_ble_app._bundle_root",
                    return_value=app_path,
                ),
                patch(
                    "zhiyun_light_control.macos_ble_app.shutil.which",
                    side_effect=lambda name: f"/usr/bin/{name}",
                ),
                patch("zhiyun_light_control.macos_ble_app.subprocess.run", fake_run),
            ):
                ensure_macos_ble_app(
                    bundle_name="ZhiyunBleScan2",
                    bundle_id="local.zhiyun-light-control.ble-scan2",
                    swift_path="/usr/bin/swiftc",
                )

            info = plistlib.loads((app_path / "Contents" / "Info.plist").read_bytes())
            self.assertEqual(
                info["CFBundleIdentifier"],
                "local.zhiyun-light-control.ble-scan2",
            )
            self.assertIn(
                '=designated => identifier "local.zhiyun-light-control.ble-scan2"',
                sign_calls[0],
            )

    def test_safe_scan_parses_worker_devices(self) -> None:
        payload = {
            "devices": [
                {
                    "address": "AA:BB",
                    "name": "MOLUS G60",
                    "rssi": -55,
                    "services": [DIRECT_ZY_SERVICE_UUID.upper()],
                },
            ]
        }
        proc = types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

        with patch(
            "zhiyun_light_control.transports.ble.subprocess.run", return_value=proc
        ):
            result = scan_zhiyun_devices_safe(timeout=1.0, python="python-test")

        self.assertTrue(result.ok)
        self.assertEqual(result.devices[0].address, "AA:BB")
        self.assertEqual(result.devices[0].name, "MOLUS G60")
        self.assertEqual(result.devices[0].rssi, -55)
        self.assertEqual(result.devices[0].services, (DIRECT_ZY_SERVICE_UUID,))
        self.assertEqual(result.devices[0].suggested_profile, "direct")
        self.assertEqual(
            result.devices[0].to_dict()["services"],
            [DIRECT_ZY_SERVICE_UUID],
        )
        self.assertEqual(result.devices[0].to_dict()["suggested_profile"], "direct")
        self.assertEqual(result.worker_python, "python-test")

    def test_safe_scan_reports_worker_abort(self) -> None:
        proc = types.SimpleNamespace(
            returncode=-6,
            stdout="",
            stderr="",
        )

        with patch(
            "zhiyun_light_control.transports.ble.subprocess.run", return_value=proc
        ):
            result = scan_zhiyun_devices_safe(timeout=1.0)

        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, -6)
        self.assertEqual(result.signal_name, "SIGABRT")
        self.assertEqual(result.error, "worker terminated by signal 6 (SIGABRT)")
        self.assertIn("SIGABRT", result.to_dict()["signal"])

    def test_safe_scan_uses_worker_json_error(self) -> None:
        proc = types.SimpleNamespace(
            returncode=1,
            stdout=json.dumps(
                {
                    "devices": [],
                    "error": "BLE support requires installing the 'ble' extra",
                }
            ),
            stderr="",
        )

        with patch(
            "zhiyun_light_control.transports.ble.subprocess.run", return_value=proc
        ):
            result = scan_zhiyun_devices_safe(timeout=1.0)

        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.error, "BLE support requires installing the 'ble' extra"
        )

    def test_macos_app_scan_parses_devices(self) -> None:
        run_result = MacosBleAppRun(
            ok=True,
            payload={
                "devices": [
                    {
                        "address": "UUID-1",
                        "name": "PL103_EDFE",
                        "rssi": -47,
                        "services": [LEGACY_ZY_SERVICE_UUID.upper()],
                    },
                    {"address": "UUID-2", "name": "Keyboard", "rssi": -52},
                ]
            },
            returncode=0,
        )

        with patch(
            "zhiyun_light_control.macos_ble_app.run_macos_ble_app",
            return_value=run_result,
        ) as run:
            result = scan_zhiyun_devices_macos_app(
                timeout=1.0,
                name_contains="PL103",
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.worker_python, "macos-app")
        self.assertEqual(len(result.devices), 1)
        self.assertEqual(result.devices[0].address, "UUID-1")
        self.assertEqual(result.devices[0].name, "PL103_EDFE")
        self.assertEqual(result.devices[0].services, (LEGACY_ZY_SERVICE_UUID,))
        self.assertEqual(result.devices[0].suggested_profile, "legacy")
        self.assertEqual(
            run.call_args.args[0],
            ["scan", "--timeout", "1.0", "--name-contains", "PL103"],
        )

    def test_macos_app_scan_can_include_all_advertisements(self) -> None:
        run_result = MacosBleAppRun(
            ok=True,
            payload={
                "devices": [
                    {"address": "UUID-1", "name": "Keyboard", "rssi": -52},
                    {"address": "UUID-2", "name": None, "rssi": -61},
                ]
            },
            returncode=0,
        )

        with patch(
            "zhiyun_light_control.macos_ble_app.run_macos_ble_app",
            return_value=run_result,
        ) as run:
            result = scan_zhiyun_devices_macos_app(
                timeout=1.0,
                include_all=True,
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            [device.address for device in result.devices],
            ["UUID-1", "UUID-2"],
        )
        self.assertIn("--include-all", run.call_args.args[0])

    def test_macos_app_scan_reports_helper_error(self) -> None:
        run_result = MacosBleAppRun(
            ok=False,
            payload={},
            error="Bluetooth is not powered on",
            returncode=1,
        )

        with patch(
            "zhiyun_light_control.macos_ble_app.run_macos_ble_app",
            return_value=run_result,
        ):
            result = scan_zhiyun_devices_macos_app(timeout=1.0)

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "Bluetooth is not powered on")
        self.assertEqual(result.returncode, 1)

    def test_inspect_result_serializes_services(self) -> None:
        result = BleInspectResult(
            ok=True,
            address="UUID-1",
            services=(
                BleService(
                    uuid=DIRECT_ZY_SERVICE_UUID,
                    characteristics=(
                        BleCharacteristic(
                            uuid=DIRECT_ZY_WRITE_UUID,
                            properties=("write-without-response",),
                            handle=7,
                        ),
                        BleCharacteristic(
                            uuid=DIRECT_ZY_NOTIFY_UUID,
                            properties=("notify",),
                        ),
                    ),
                ),
            ),
            worker_python="macos-app",
        )

        payload = result.to_dict()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["services"][0]["uuid"], DIRECT_ZY_SERVICE_UUID)
        self.assertEqual(
            payload["services"][0]["characteristics"][0]["properties"],
            ["write-without-response"],
        )
        self.assertEqual(payload["services"][0]["characteristics"][0]["handle"], 7)
        self.assertEqual(payload["endpoint_candidates"][0]["profile"], "direct")
        self.assertEqual(
            payload["endpoint_candidates"][0]["cli_args"],
            [
                "--ble-profile",
                "direct",
                "--ble-service-uuid",
                DIRECT_ZY_SERVICE_UUID,
                "--ble-write-uuid",
                DIRECT_ZY_WRITE_UUID,
                "--ble-notify-uuid",
                DIRECT_ZY_NOTIFY_UUID,
            ],
        )

    def test_endpoint_candidate_serializes_cli_args(self) -> None:
        candidate = BleEndpointCandidate(
            profile="direct",
            service_uuid="service-test",
            write_uuid="write-test",
            notify_uuid="notify-test",
            confidence="property-pair",
            confidence_score=45,
            reason="test",
        )

        payload = candidate.to_dict()
        self.assertEqual(
            payload["cli_args"],
            [
                "--ble-profile",
                "direct",
                "--ble-service-uuid",
                "service-test",
                "--ble-write-uuid",
                "write-test",
                "--ble-notify-uuid",
                "notify-test",
            ],
        )

    def test_safe_inspect_parses_worker_services(self) -> None:
        payload = {
            "ok": True,
            "address": "AA:BB",
            "services": [
                {
                    "uuid": DIRECT_ZY_SERVICE_UUID.upper(),
                    "characteristics": [
                        {
                            "uuid": DIRECT_ZY_WRITE_UUID.upper(),
                            "properties": ["WRITE"],
                            "handle": 3,
                        },
                        {
                            "uuid": DIRECT_ZY_NOTIFY_UUID.upper(),
                            "properties": ["Notify"],
                        },
                    ],
                }
            ],
        }
        proc = types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

        with patch(
            "zhiyun_light_control.transports.ble.subprocess.run", return_value=proc
        ) as run:
            result = inspect_zhiyun_ble_safe(
                timeout=1.0,
                address="AA:BB",
                python="python-test",
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.address, "AA:BB")
        self.assertEqual(result.worker_python, "python-test")
        self.assertEqual(result.services[0].uuid, DIRECT_ZY_SERVICE_UUID)
        self.assertEqual(
            result.services[0].characteristics[0].uuid,
            DIRECT_ZY_WRITE_UUID,
        )
        self.assertEqual(
            result.services[0].characteristics[0].properties,
            ("write",),
        )
        self.assertEqual(
            result.to_dict()["endpoint_candidates"][0]["profile"],
            "direct",
        )
        worker_args = run.call_args.args[0]
        self.assertIn("inspect", worker_args)
        self.assertIn("--address", worker_args)

    def test_macos_app_inspect_parses_services(self) -> None:
        run_result = MacosBleAppRun(
            ok=True,
            payload={
                "address": "UUID-1",
                "services": [
                    {
                        "uuid": LEGACY_ZY_SERVICE_UUID.upper(),
                        "characteristics": [
                            {
                                "uuid": LEGACY_ZY_WRITE_UUID.upper(),
                                "properties": ["write-without-response"],
                            }
                        ],
                    }
                ],
            },
            returncode=0,
        )

        with patch(
            "zhiyun_light_control.macos_ble_app.run_macos_ble_app",
            return_value=run_result,
        ) as run:
            result = inspect_zhiyun_ble_macos_app(
                timeout=1.0,
                name_contains="PL103",
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.address, "UUID-1")
        self.assertEqual(result.worker_python, "macos-app")
        self.assertEqual(result.services[0].uuid, LEGACY_ZY_SERVICE_UUID)
        self.assertEqual(
            run.call_args.args[0],
            ["inspect", "--timeout", "1.0", "--name-contains", "PL103"],
        )


class SafeBleExchangeTests(unittest.TestCase):
    def test_profile_names_include_known_zhiyun_surfaces(self) -> None:
        self.assertEqual(
            BLE_PROFILE_NAMES,
            ("direct", "legacy", "yc", "mesh-provisioning", "mesh-proxy"),
        )

    def test_resolve_ble_profile_supports_legacy_and_custom_overrides(self) -> None:
        legacy = resolve_ble_profile("legacy")
        self.assertEqual(legacy.service_uuid, LEGACY_ZY_SERVICE_UUID)
        self.assertEqual(legacy.write_uuid, LEGACY_ZY_WRITE_UUID)
        self.assertEqual(legacy.notify_uuid, LEGACY_ZY_NOTIFY_UUID)

        custom = resolve_ble_profile(legacy, write_uuid="write-test")
        self.assertEqual(custom.name, "legacy+custom")
        self.assertEqual(custom.service_uuid, LEGACY_ZY_SERVICE_UUID)
        self.assertEqual(custom.write_uuid, "write-test")
        self.assertEqual(custom.notify_uuid, LEGACY_ZY_NOTIFY_UUID)

    def test_custom_profile_resolves_without_known_name(self) -> None:
        custom = resolve_ble_profile(
            BleProfile(
                name="bench",
                service_uuid="service-test",
                write_uuid="write-test",
                notify_uuid="notify-test",
            )
        )

        self.assertEqual(custom.name, "bench")
        self.assertEqual(custom.write_uuid, "write-test")

    def test_suggest_ble_profile_maps_advertised_services(self) -> None:
        self.assertEqual(
            suggest_ble_profile([DIRECT_ZY_SERVICE_UUID.upper()]),
            "direct",
        )
        self.assertEqual(
            suggest_ble_profile(["00001827-0000-1000-8000-00805f9b34fb"]),
            "mesh-provisioning",
        )

    def test_suggest_ble_endpoint_candidates_maps_known_and_custom_pairs(self) -> None:
        services = (
            BleService(
                uuid=LEGACY_ZY_SERVICE_UUID,
                characteristics=(
                    BleCharacteristic(
                        uuid=LEGACY_ZY_WRITE_UUID,
                        properties=("write-without-response",),
                    ),
                    BleCharacteristic(
                        uuid=LEGACY_ZY_NOTIFY_UUID,
                        properties=("notify",),
                    ),
                ),
            ),
            BleService(
                uuid=MESH_PROVISIONING_SERVICE_UUID,
                characteristics=(
                    BleCharacteristic(
                        uuid=MESH_PROVISIONING_WRITE_UUID,
                        properties=("write",),
                    ),
                    BleCharacteristic(
                        uuid=MESH_PROVISIONING_NOTIFY_UUID,
                        properties=("notify",),
                    ),
                ),
            ),
        )

        candidates = suggest_ble_endpoint_candidates(services)

        self.assertEqual(
            {candidate.profile for candidate in candidates[:2]},
            {"legacy", "mesh-provisioning"},
        )
        self.assertEqual(candidates[0].confidence, "known-profile")
        self.assertEqual(candidates[1].confidence, "known-profile")

    def test_safe_exchange_parses_worker_response(self) -> None:
        tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
        rx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO, b"\x00")
        proc = types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"address": "AA:BB", "rx_hex": rx.hex()}),
            stderr="",
        )

        with patch(
            "zhiyun_light_control.transports.ble.subprocess.run", return_value=proc
        ):
            result = exchange_zhiyun_ble_safe(
                tx,
                address="AA:BB",
                timeout=1.0,
                python="python-test",
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.address, "AA:BB")
        self.assertEqual(result.rx, rx)
        self.assertEqual(result.worker_python, "python-test")
        self.assertEqual(result.to_dict()["rx_hex"], rx.hex())

    def test_safe_exchange_passes_profile_and_characteristics_to_worker(self) -> None:
        tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
        proc = types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"address": "AA:BB", "rx_hex": ""}),
            stderr="",
        )

        with patch(
            "zhiyun_light_control.transports.ble.subprocess.run", return_value=proc
        ) as run:
            result = exchange_zhiyun_ble_safe(
                tx,
                profile="legacy",
                service_uuid="service-test",
                write_uuid="write-test",
                notify_uuid="notify-test",
                timeout=1.0,
            )

        self.assertTrue(result.ok)
        worker_args = run.call_args.args[0]
        self.assertIn("--profile", worker_args)
        self.assertEqual(worker_args[worker_args.index("--profile") + 1], "legacy")
        self.assertEqual(
            worker_args[worker_args.index("--service-uuid") + 1],
            "service-test",
        )
        self.assertEqual(
            worker_args[worker_args.index("--write-uuid") + 1],
            "write-test",
        )
        self.assertEqual(
            worker_args[worker_args.index("--notify-uuid") + 1],
            "notify-test",
        )

    def test_safe_exchange_reports_worker_abort(self) -> None:
        tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
        proc = types.SimpleNamespace(
            returncode=-6,
            stdout="",
            stderr="",
        )

        with patch(
            "zhiyun_light_control.transports.ble.subprocess.run", return_value=proc
        ):
            result = exchange_zhiyun_ble_safe(tx, timeout=1.0)

        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, -6)
        self.assertEqual(result.signal_name, "SIGABRT")
        self.assertEqual(result.error, "worker terminated by signal 6 (SIGABRT)")

    def test_macos_app_exchange_uses_resolved_characteristics(self) -> None:
        tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
        rx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO, b"\x00")
        run_result = MacosBleAppRun(
            ok=True,
            payload={"address": "UUID-1", "rx_hex": rx.hex()},
            returncode=0,
        )

        with patch(
            "zhiyun_light_control.macos_ble_app.run_macos_ble_app",
            return_value=run_result,
        ) as run:
            result = exchange_zhiyun_ble_macos_app(
                tx,
                address="UUID-1",
                profile="legacy",
                timeout=1.0,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.address, "UUID-1")
        self.assertEqual(result.rx, rx)
        self.assertEqual(result.worker_python, "macos-app")
        helper_args = run.call_args.args[0]
        self.assertEqual(helper_args[0], "exchange-raw")
        self.assertIn(LEGACY_ZY_SERVICE_UUID, helper_args)
        self.assertIn(LEGACY_ZY_WRITE_UUID, helper_args)
        self.assertIn(LEGACY_ZY_NOTIFY_UUID, helper_args)

    def test_macos_app_exchange_reports_invalid_rx_hex(self) -> None:
        tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
        run_result = MacosBleAppRun(
            ok=True,
            payload={"address": "UUID-1", "rx_hex": "not-hex"},
            returncode=0,
        )

        with patch(
            "zhiyun_light_control.macos_ble_app.run_macos_ble_app",
            return_value=run_result,
        ):
            result = exchange_zhiyun_ble_macos_app(tx, timeout=1.0)

        self.assertFalse(result.ok)
        self.assertIn("could not parse macOS BLE app rx_hex", result.error)

    def test_macos_app_sequence_exchange_reports_per_write_rx(self) -> None:
        run_result = MacosBleAppRun(
            ok=True,
            payload={
                "address": "UUID-1",
                "rx_hex": "aabbcc",
                "rx_hexes": ["aa", "", "bbcc"],
            },
            returncode=0,
        )

        with patch(
            "zhiyun_light_control.macos_ble_app.run_macos_ble_app",
            return_value=run_result,
        ) as run:
            result = exchange_zhiyun_ble_sequence_macos_app(
                (b"\x01", b"\x02", b"\x03"),
                address="UUID-1",
                profile="mesh-provisioning",
                timeout=4.0,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.rx, (b"\xaa", b"", b"\xbb\xcc"))
        self.assertEqual(result.rx_combined, b"\xaa\xbb\xcc")
        helper_args = run.call_args.args[0]
        self.assertEqual(helper_args[0], "exchange-sequence")
        self.assertIn("--tx-hexes", helper_args)
        self.assertIn("01,02,03", helper_args)

    def test_macos_app_ipc_session_uses_mesh_profile(self) -> None:
        with patch(
            "zhiyun_light_control.macos_ble_app.MacosBleIpcSession",
        ) as session_class:
            session = open_zhiyun_ble_ipc_macos_app(
                address="UUID-1",
                profile="mesh-provisioning",
                timeout=4.0,
            )

        self.assertIs(session, session_class.return_value)
        helper_args = session_class.call_args.args[0]
        self.assertEqual(helper_args[0], "exchange-ipc")
        self.assertIn(MESH_PROVISIONING_SERVICE_UUID, helper_args)
        self.assertIn(MESH_PROVISIONING_WRITE_UUID, helper_args)
        self.assertIn(MESH_PROVISIONING_NOTIFY_UUID, helper_args)
        self.assertIn("UUID-1", helper_args)


class AsyncBleTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_filters_likely_zhiyun_devices(self) -> None:
        class FakeScanner:
            @staticmethod
            async def discover(timeout: float, return_adv: bool):
                self.assertEqual(timeout, 1.0)
                self.assertTrue(return_adv)
                return {
                    "one": (
                        FakeDevice("AA", None),
                        FakeAdvertisement(local_name="MOLUS G60", rssi=-40),
                    ),
                    "two": (
                        FakeDevice("BB", "Keyboard"),
                        FakeAdvertisement(rssi=-50),
                    ),
                    "three": (
                        FakeDevice("CC", "Unknown"),
                        FakeAdvertisement(
                            service_uuids=["6E400001-B5A3-F393-E0A9-E50E24DCCA9E"],
                            rssi=-60,
                        ),
                    ),
                    "four": (
                        FakeDevice("DD", "Unknown"),
                        FakeAdvertisement(
                            service_uuids=[YC_SERVICE_UUID.upper()],
                            rssi=-65,
                        ),
                    ),
                    "five": (
                        FakeDevice("EE", "PL103_EDFE"),
                        FakeAdvertisement(rssi=-70),
                    ),
                }

        with patch(
            "zhiyun_light_control.transports.ble._load_bleak",
            return_value=(object, FakeScanner),
        ):
            devices = await scan_zhiyun_devices(timeout=1.0)

        self.assertEqual(len(devices), 4)
        self.assertEqual(devices[0].address, "AA")
        self.assertEqual(devices[0].name, "MOLUS G60")
        self.assertEqual(devices[0].services, ())
        self.assertIsNone(devices[0].suggested_profile)
        self.assertEqual(devices[1].address, "CC")
        self.assertEqual(devices[1].services, (DIRECT_ZY_SERVICE_UUID,))
        self.assertEqual(devices[1].suggested_profile, "direct")
        self.assertEqual(devices[2].address, "DD")
        self.assertEqual(devices[2].services, (YC_SERVICE_UUID,))
        self.assertEqual(devices[2].suggested_profile, "yc")
        self.assertEqual(devices[3].address, "EE")
        self.assertEqual(devices[3].name, "PL103_EDFE")

    async def test_scan_name_filter_keeps_python_matches_before_profile_filter(
        self,
    ) -> None:
        class FakeScanner:
            @staticmethod
            async def discover(timeout: float, return_adv: bool):
                return {
                    "one": (
                        FakeDevice("AA", "Desk Light PL103"),
                        FakeAdvertisement(rssi=-42),
                    ),
                    "two": (
                        FakeDevice("BB", "Keyboard"),
                        FakeAdvertisement(rssi=-50),
                    ),
                }

        with patch(
            "zhiyun_light_control.transports.ble._load_bleak",
            return_value=(object, FakeScanner),
        ):
            devices = await scan_zhiyun_devices(
                timeout=1.0,
                name_contains="PL103",
            )

        self.assertEqual([device.address for device in devices], ["AA"])

    async def test_inspect_device_reads_gatt_services(self) -> None:
        class FakeCharacteristic:
            uuid = DIRECT_ZY_WRITE_UUID.upper()
            properties = ["write", "notify"]
            handle = 9

        class FakeService:
            uuid = DIRECT_ZY_SERVICE_UUID.upper()
            characteristics = [FakeCharacteristic()]

        class FakeClient:
            def __init__(self, address: str):
                self.address = address
                self.services = [FakeService()]
                self.disconnected = False

            async def connect(self) -> None:
                return

            async def disconnect(self) -> None:
                self.disconnected = True

        with patch(
            "zhiyun_light_control.transports.ble._load_bleak",
            return_value=(FakeClient, object),
        ):
            result = await inspect_zhiyun_device(address="AA", timeout=1.0)

        self.assertTrue(result.ok)
        self.assertEqual(result.address, "AA")
        self.assertEqual(result.worker_python, "direct")
        self.assertEqual(result.services[0].uuid, DIRECT_ZY_SERVICE_UUID)
        self.assertEqual(
            result.services[0].characteristics[0].uuid,
            DIRECT_ZY_WRITE_UUID,
        )
        self.assertEqual(
            result.services[0].characteristics[0].properties,
            ("write", "notify"),
        )

    async def test_transport_exchange_uses_write_and_notify_characteristics(
        self,
    ) -> None:
        class FakeClient:
            callback = None
            writes: list[tuple[str, bytes, bool]] = []

            def __init__(self, address: str):
                self.address = address

            async def connect(self) -> None:
                return

            async def disconnect(self) -> None:
                return

            async def start_notify(self, uuid: str, callback) -> None:
                self.__class__.callback = callback
                self.notify_uuid = uuid

            async def stop_notify(self, uuid: str) -> None:
                self.stopped_uuid = uuid

            async def write_gatt_char(
                self, uuid: str, tx: bytes, response: bool
            ) -> None:
                self.__class__.writes.append((uuid, tx, response))
                frame = first_frame(tx)
                assert frame is not None
                ack = build_runtime_frame(frame.seq, frame.cmd, b"\x00")
                self.__class__.callback(uuid, bytearray(ack))

        with patch(
            "zhiyun_light_control.transports.ble._load_bleak",
            return_value=(FakeClient, object),
        ):
            async with BleTransport(address="AA", timeout=1.0) as transport:
                tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
                rx = await transport.exchange(tx, timeout=1.0)

        self.assertTrue(rx)
        self.assertEqual(FakeClient.writes[0][0], DIRECT_ZY_WRITE_UUID)
        self.assertFalse(FakeClient.writes[0][2])
        self.assertEqual(first_frame(rx).cmd, RuntimeCommand.DEVICE_INFO)

    async def test_transport_exchange_uses_response_for_write_only_characteristic(
        self,
    ) -> None:
        class FakeWriteCharacteristic:
            uuid = DIRECT_ZY_WRITE_UUID
            properties = ["write"]

        class FakeNotifyCharacteristic:
            uuid = DIRECT_ZY_NOTIFY_UUID
            properties = ["notify"]

        class FakeService:
            uuid = DIRECT_ZY_SERVICE_UUID
            characteristics = [FakeWriteCharacteristic(), FakeNotifyCharacteristic()]

        class FakeClient:
            callback = None
            writes: list[tuple[str, bytes, bool]] = []

            def __init__(self, address: str):
                self.address = address
                self.services = [FakeService()]

            async def connect(self) -> None:
                return

            async def disconnect(self) -> None:
                return

            async def start_notify(self, uuid: str, callback) -> None:
                self.__class__.callback = callback

            async def stop_notify(self, uuid: str) -> None:
                return

            async def write_gatt_char(
                self, uuid: str, tx: bytes, response: bool
            ) -> None:
                self.__class__.writes.append((uuid, tx, response))
                frame = first_frame(tx)
                assert frame is not None
                ack = build_runtime_frame(frame.seq, frame.cmd, b"\x00")
                self.__class__.callback(uuid, bytearray(ack))

        with patch(
            "zhiyun_light_control.transports.ble._load_bleak",
            return_value=(FakeClient, object),
        ):
            async with BleTransport(address="AA", timeout=1.0) as transport:
                tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
                rx = await transport.exchange(tx, timeout=1.0)

        self.assertTrue(rx)
        self.assertEqual(FakeClient.writes[0][0], DIRECT_ZY_WRITE_UUID)
        self.assertTrue(FakeClient.writes[0][2])

    async def test_transport_profile_selects_legacy_characteristics(self) -> None:
        class FakeClient:
            callback = None
            writes: list[tuple[str, bytes, bool]] = []

            def __init__(self, address: str):
                self.address = address

            async def connect(self) -> None:
                return

            async def disconnect(self) -> None:
                return

            async def start_notify(self, uuid: str, callback) -> None:
                self.__class__.callback = callback
                self.notify_uuid = uuid

            async def stop_notify(self, uuid: str) -> None:
                self.stopped_uuid = uuid

            async def write_gatt_char(
                self, uuid: str, tx: bytes, response: bool
            ) -> None:
                self.__class__.writes.append((uuid, tx, response))
                frame = first_frame(tx)
                assert frame is not None
                ack = build_runtime_frame(frame.seq, frame.cmd, b"\x00")
                self.__class__.callback(uuid, bytearray(ack))

        with patch(
            "zhiyun_light_control.transports.ble._load_bleak",
            return_value=(FakeClient, object),
        ):
            async with BleTransport(
                address="AA",
                profile="legacy",
                timeout=1.0,
            ) as transport:
                tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
                rx = await transport.exchange(tx, timeout=1.0)

        self.assertTrue(rx)
        self.assertEqual(transport.profile, "legacy")
        self.assertEqual(transport.notify_uuid, LEGACY_ZY_NOTIFY_UUID)
        self.assertEqual(FakeClient.writes[0][0], LEGACY_ZY_WRITE_UUID)

    async def test_crash_isolated_transport_uses_safe_exchange(self) -> None:
        tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
        rx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO, b"\x00")

        with patch(
            "zhiyun_light_control.transports.ble.exchange_zhiyun_ble_safe",
            return_value=BleExchangeResult(ok=True, tx=tx, rx=rx, address="AA"),
        ) as exchange:
            async with CrashIsolatedBleTransport(
                address="AA",
                timeout=1.0,
                python="python-test",
            ) as transport:
                result = await transport.exchange(tx, timeout=1.0)

        self.assertEqual(result, rx)
        self.assertEqual(transport.address, "AA")
        exchange.assert_called_once()
        self.assertEqual(exchange.call_args.kwargs["profile"], "direct")
        self.assertEqual(
            exchange.call_args.kwargs["service_uuid"],
            DIRECT_ZY_SERVICE_UUID,
        )
        self.assertEqual(exchange.call_args.kwargs["python"], "python-test")

    async def test_macos_app_transport_uses_native_exchange(self) -> None:
        tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
        rx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO, b"\x00")

        with patch(
            "zhiyun_light_control.transports.ble.exchange_zhiyun_ble_macos_app",
            return_value=BleExchangeResult(ok=True, tx=tx, rx=rx, address="UUID-1"),
        ) as exchange:
            async with MacosBleAppTransport(
                address="UUID-1",
                profile="legacy",
                timeout=1.0,
            ) as transport:
                result = await transport.exchange(tx, timeout=1.0)

        self.assertEqual(result, rx)
        self.assertEqual(transport.address, "UUID-1")
        exchange.assert_called_once()
        self.assertEqual(exchange.call_args.kwargs["address"], "UUID-1")
        self.assertEqual(
            exchange.call_args.kwargs["service_uuid"],
            LEGACY_ZY_SERVICE_UUID,
        )


if __name__ == "__main__":
    unittest.main()
