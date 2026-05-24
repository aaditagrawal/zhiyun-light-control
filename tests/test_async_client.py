from __future__ import annotations

import unittest

from zhiyun_light_control import (
    AsyncProbeResult,
    AsyncZhiyunLight,
    Scene,
    UnconfirmedCommandError,
    command_results_acknowledged,
)
from zhiyun_light_control.protocol import (
    UPDATER_DEVICE,
    RuntimeCommand,
    UpdaterCommand,
    build_frame,
    first_frame,
)


class AsyncEchoAckTransport:
    def __init__(self, payload_by_cmd: dict[int, bytes] | None = None) -> None:
        self.sent: list[bytes] = []
        self.payload_by_cmd = payload_by_cmd or {}

    async def exchange(self, tx: bytes, timeout: float = 1.5) -> bytes:
        del timeout
        self.sent.append(tx)
        frame = first_frame(tx)
        assert frame is not None
        payload = self.payload_by_cmd.get(frame.cmd, b"\x00")
        return build_frame(frame.first_word, frame.seq, frame.cmd, payload)

    async def close(self) -> None:
        return


class AsyncTimeoutTransport:
    async def exchange(self, tx: bytes, timeout: float = 1.5) -> bytes:
        del tx, timeout
        return b""

    async def close(self) -> None:
        return


class AsyncClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_client_exports_probe_result_type(self) -> None:
        result = AsyncProbeResult(
            device_identifier="id",
            generation="pl103",
            firmware="1.6.4",
            voltage_status=101,
            device_id=1,
            address="AA",
        )

        self.assertEqual(result.to_dict()["address"], "AA")

    async def test_async_object_helpers_match_sync_command_surface(self) -> None:
        transport = AsyncEchoAckTransport()
        light = AsyncZhiyunLight(transport)

        await light.read_brightness(obj=7)
        await light.read_cct(obj=7)
        await light.read_sleep(obj=7)
        await light.get_object_voltage(obj=7)
        await light.get_object_mode(obj=7)
        await light.identify(obj=7)

        frames = [first_frame(tx) for tx in transport.sent]
        self.assertEqual(
            [frame.cmd for frame in frames],
            [
                RuntimeCommand.BRIGHTNESS,
                RuntimeCommand.CCT,
                RuntimeCommand.SLEEP,
                RuntimeCommand.VOLTAGE_BY_OBJECT,
                RuntimeCommand.DEVICE_MODE,
                RuntimeCommand.IDENTIFY,
            ],
        )
        self.assertEqual([frame.payload[2] for frame in frames[:3]], [0, 0, 0])
        self.assertEqual([frame.payload[:2] for frame in frames], [b"\x07\x00"] * 6)

    async def test_async_exchange_frame_allows_custom_first_word(self) -> None:
        transport = AsyncEchoAckTransport()
        light = AsyncZhiyunLight(transport)

        result = await light.exchange_frame(0x0301, RuntimeCommand.DEVICE_INFO)

        self.assertTrue(result.acknowledged)
        self.assertEqual(first_frame(transport.sent[0]).first_word, 0x0301)

    async def test_async_brightness_with_mode_uses_official_runtime_command(
        self,
    ) -> None:
        transport = AsyncEchoAckTransport()
        light = AsyncZhiyunLight(transport)

        result = await light.set_brightness_with_mode(
            1,
            25,
            2,
            control_mode=0x33,
        )

        frame = first_frame(transport.sent[0])
        self.assertTrue(result.acknowledged)
        self.assertEqual(result.command, RuntimeCommand.BRIGHTNESS_WITH_MODE)
        self.assertEqual(frame.payload.hex(), "0100330000c84102")

    async def test_async_exchange_prebuilt_frame_preserves_supplied_bytes(
        self,
    ) -> None:
        transport = AsyncEchoAckTransport()
        light = AsyncZhiyunLight(transport)
        frame = build_frame(0x0301, 17, RuntimeCommand.DEVICE_INFO)

        result = await light.exchange_prebuilt_frame(
            frame,
            RuntimeCommand.DEVICE_INFO,
        )

        self.assertTrue(result.acknowledged)
        self.assertEqual(result.tx, frame)
        self.assertEqual(transport.sent, [frame])
        self.assertEqual(first_frame(result.tx).seq, 17)

    async def test_async_updater_helpers_match_sync_status_surface(self) -> None:
        transport = AsyncEchoAckTransport(
            {
                UpdaterCommand.CHIP_SYNC: bytes.fromhex(
                    "0048444c0000010010030041054008a40065a36075"
                ),
                UpdaterCommand.READ_SN: bytes.fromhex("004105130110c1e009a408"),
            }
        )
        light = AsyncZhiyunLight(transport)

        result = await light.exchange_updater(UpdaterCommand.CHIP_SYNC, timeout=0.4)
        chip = await light.chip_sync()
        read_sn = await light.read_sn()

        self.assertTrue(result.acknowledged)
        self.assertEqual(
            [first_frame(tx).first_word for tx in transport.sent],
            [UPDATER_DEVICE, UPDATER_DEVICE, UPDATER_DEVICE],
        )
        self.assertEqual(chip.core_id, "HDL")
        self.assertEqual(chip.updater_firmware, "1.64")
        self.assertEqual(read_sn.product, 0x0541)
        self.assertEqual(read_sn.device_identifier, "08a409e0c1100113")

    async def test_isolated_ble_uses_crash_isolated_transport(self) -> None:
        light = AsyncZhiyunLight.isolated_ble(
            address="AA",
            name_contains="MOLUS",
            profile="legacy",
            service_uuid="service-test",
            write_uuid="write-test",
            notify_uuid="notify-test",
            timeout=1.0,
            python="python-test",
        )

        self.assertEqual(light.transport.address, "AA")
        self.assertEqual(light.transport.name_contains, "MOLUS")
        self.assertEqual(light.transport.profile, "legacy+custom")
        self.assertEqual(light.transport.service_uuid, "service-test")
        self.assertEqual(light.transport.write_uuid, "write-test")
        self.assertEqual(light.transport.notify_uuid, "notify-test")
        self.assertEqual(light.transport.timeout, 1.0)
        self.assertEqual(light.transport.python, "python-test")

    async def test_macos_ble_app_uses_native_app_transport(self) -> None:
        light = AsyncZhiyunLight.macos_ble_app(
            address="UUID-1",
            name_contains="PL103",
            profile="legacy",
            service_uuid="service-test",
            write_uuid="write-test",
            notify_uuid="notify-test",
            timeout=1.0,
        )

        self.assertEqual(light.transport.address, "UUID-1")
        self.assertEqual(light.transport.name_contains, "PL103")
        self.assertEqual(light.transport.profile, "legacy+custom")
        self.assertEqual(light.transport.service_uuid, "service-test")
        self.assertEqual(light.transport.write_uuid, "write-test")
        self.assertEqual(light.transport.notify_uuid, "notify-test")
        self.assertEqual(light.transport.timeout, 1.0)

    async def test_async_apply_scene_orders_media_workflow_commands(self) -> None:
        transport = AsyncEchoAckTransport()
        light = AsyncZhiyunLight(transport)

        results = await light.apply_scene(
            Scene(obj=1, sleep=0, brightness=25, kelvin=5600)
        )

        self.assertEqual(
            [result.command for result in results], [0x1008, 0x1001, 0x1002]
        )
        sent_cmds = [first_frame(tx).cmd for tx in transport.sent]
        self.assertEqual(sent_cmds, [0x1008, 0x1001, 0x1002])
        self.assertEqual(
            [first_frame(tx).payload[2] for tx in transport.sent],
            [0x33, 0x33, 0x33],
        )

    async def test_async_control_mode_can_use_legacy_operation_byte(self) -> None:
        transport = AsyncEchoAckTransport()
        light = AsyncZhiyunLight(transport)

        result = await light.set_sleep(1, 0, control_mode=0x01)

        frame = first_frame(transport.sent[0])
        self.assertTrue(result.acknowledged)
        self.assertEqual(result.command, RuntimeCommand.SLEEP)
        self.assertEqual(frame.payload[2], 0x01)

    async def test_async_confirmed_helpers_return_acknowledged_results(self) -> None:
        transport = AsyncEchoAckTransport()
        light = AsyncZhiyunLight(transport)

        register = await light.register_confirmed()
        sleep = await light.set_sleep_confirmed(1, 0)
        scene = await light.apply_scene_confirmed(
            Scene(obj=1, brightness=25, kelvin=5600)
        )
        batches = await light.transition_scene_confirmed(
            Scene(obj=1, brightness=0),
            Scene(obj=1, brightness=10),
            steps=2,
            duration=0,
        )

        self.assertTrue(register.acknowledged)
        self.assertTrue(sleep.acknowledged)
        self.assertTrue(command_results_acknowledged(scene))
        self.assertEqual(
            [result.command for batch in batches for result in batch],
            [RuntimeCommand.BRIGHTNESS, RuntimeCommand.BRIGHTNESS],
        )

    async def test_async_confirmed_helpers_raise_for_unacknowledged_results(
        self,
    ) -> None:
        light = AsyncZhiyunLight(AsyncTimeoutTransport())

        with self.assertRaises(UnconfirmedCommandError) as error:
            await light.set_brightness_confirmed(1, 25)

        self.assertEqual(error.exception.action, "brightness")
        self.assertEqual(error.exception.statuses, ["sent_no_response"])
        self.assertEqual(
            error.exception.unconfirmed[0].command,
            RuntimeCommand.BRIGHTNESS,
        )

    async def test_async_transition_scene_matches_sync_surface(self) -> None:
        transport = AsyncEchoAckTransport()
        light = AsyncZhiyunLight(transport)

        batches = await light.transition_scene(
            Scene(obj=1, brightness=0),
            Scene(obj=1, brightness=100),
            steps=2,
            duration=0,
        )

        self.assertEqual(
            [result.command for batch in batches for result in batch],
            [0x1001, 0x1001],
        )
        self.assertEqual(
            [first_frame(tx).cmd for tx in transport.sent], [0x1001, 0x1001]
        )


if __name__ == "__main__":
    unittest.main()
