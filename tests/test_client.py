from __future__ import annotations

import unittest
from unittest.mock import patch

from zhiyun_light_control import (
    Scene,
    UnconfirmedCommandError,
    ZhiyunLight,
    command_results_acknowledged,
    require_command_result,
)
from zhiyun_light_control.protocol import (
    RuntimeCommand,
    build_runtime_frame,
    first_frame,
)


class EchoAckTransport:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def exchange(self, tx: bytes, timeout: float = 0.8) -> bytes:
        del timeout
        self.sent.append(tx)
        frame = first_frame(tx)
        assert frame is not None
        return build_runtime_frame(frame.seq, frame.cmd, b"\x00")

    def close(self) -> None:
        return


class ExactEchoTransport:
    def exchange(self, tx: bytes, timeout: float = 0.8) -> bytes:
        del timeout
        return tx

    def close(self) -> None:
        return


class TimeoutTransport:
    def exchange(self, tx: bytes, timeout: float = 0.8) -> bytes:
        del tx, timeout
        return b""

    def close(self) -> None:
        return


class EchoThenAckTransport:
    def exchange(self, tx: bytes, timeout: float = 0.8) -> bytes:
        del timeout
        frame = first_frame(tx)
        assert frame is not None
        return tx + build_runtime_frame(frame.seq, frame.cmd, b"\x00")

    def close(self) -> None:
        return


class ClientTests(unittest.TestCase):
    def test_usb_factory_passes_lock_timeout_to_transport(self) -> None:
        transport = EchoAckTransport()

        with patch(
            "zhiyun_light_control.client.UsbTransport",
            return_value=transport,
        ) as make_transport:
            light = ZhiyunLight.usb(
                port="/dev/cu.test",
                timeout=2.0,
                lock_timeout=0.25,
            )

        self.assertIs(light.transport, transport)
        make_transport.assert_called_once_with(
            port="/dev/cu.test",
            timeout=2.0,
            lock_timeout=0.25,
        )

    def test_exchange_runtime_exposes_raw_bytes_and_ack(self) -> None:
        transport = EchoAckTransport()
        light = ZhiyunLight(transport)

        result = light.exchange_runtime(
            RuntimeCommand.REGISTER_DEFAULT_GROUP, b"\x00\x00\x00\x00"
        )

        self.assertTrue(result.acknowledged)
        self.assertFalse(result.timed_out)
        self.assertTrue(result.sent)
        self.assertEqual(result.transport_status, "acknowledged")
        self.assertEqual(result.command, RuntimeCommand.REGISTER_DEFAULT_GROUP)
        self.assertEqual(result.tx, transport.sent[0])
        self.assertEqual(result.ack.cmd, RuntimeCommand.REGISTER_DEFAULT_GROUP)
        self.assertFalse(result.echoed)

    def test_exchange_runtime_does_not_count_exact_echo_as_ack(self) -> None:
        light = ZhiyunLight(ExactEchoTransport())

        result = light.exchange_runtime(RuntimeCommand.BRIGHTNESS, b"\x01")

        self.assertFalse(result.acknowledged)
        self.assertTrue(result.echoed)
        self.assertEqual(result.transport_status, "echoed_write")

    def test_exchange_runtime_skips_echo_before_real_ack(self) -> None:
        light = ZhiyunLight(EchoThenAckTransport())

        result = light.exchange_runtime(RuntimeCommand.DEVICE_INFO)

        self.assertTrue(result.acknowledged)
        self.assertTrue(result.echoed)
        self.assertEqual(result.transport_status, "acknowledged")
        self.assertEqual(result.ack.payload, b"\x00")

    def test_exchange_frame_allows_custom_first_word_for_discovery(self) -> None:
        transport = EchoAckTransport()
        light = ZhiyunLight(transport)

        result = light.exchange_frame(0x0301, RuntimeCommand.DEVICE_INFO)

        self.assertTrue(result.acknowledged)
        self.assertEqual(first_frame(transport.sent[0]).first_word, 0x0301)

    def test_exchange_prebuilt_frame_preserves_supplied_bytes(self) -> None:
        transport = EchoAckTransport()
        light = ZhiyunLight(transport)
        frame = build_runtime_frame(17, RuntimeCommand.DEVICE_INFO)

        result = light.exchange_prebuilt_frame(frame, RuntimeCommand.DEVICE_INFO)

        self.assertTrue(result.acknowledged)
        self.assertEqual(result.tx, frame)
        self.assertEqual(transport.sent, [frame])
        self.assertEqual(first_frame(result.tx).seq, 17)

    def test_apply_scene_orders_media_workflow_commands(self) -> None:
        transport = EchoAckTransport()
        light = ZhiyunLight(transport)

        results = light.apply_scene(Scene(obj=1, sleep=0, brightness=25, kelvin=5600))

        self.assertEqual(
            [result.command for result in results], [0x1008, 0x1001, 0x1002]
        )
        sent_cmds = [first_frame(tx).cmd for tx in transport.sent]
        self.assertEqual(sent_cmds, [0x1008, 0x1001, 0x1002])
        self.assertEqual(
            [first_frame(tx).payload[2] for tx in transport.sent],
            [0x33, 0x33, 0x33],
        )

    def test_control_mode_can_use_legacy_operation_byte(self) -> None:
        transport = EchoAckTransport()
        light = ZhiyunLight(transport)

        result = light.set_brightness(1, 25, control_mode=0x01)

        frame = first_frame(transport.sent[0])
        self.assertTrue(result.acknowledged)
        self.assertEqual(result.command, RuntimeCommand.BRIGHTNESS)
        self.assertEqual(frame.payload[2], 0x01)

    def test_brightness_with_mode_uses_official_runtime_command(self) -> None:
        transport = EchoAckTransport()
        light = ZhiyunLight(transport)

        result = light.set_brightness_with_mode(1, 25, 2, control_mode=0x33)

        frame = first_frame(transport.sent[0])
        self.assertTrue(result.acknowledged)
        self.assertEqual(result.command, RuntimeCommand.BRIGHTNESS_WITH_MODE)
        self.assertEqual(frame.payload.hex(), "0100330000c84102")

    def test_confirmed_helpers_return_acknowledged_sdk_results(self) -> None:
        transport = EchoAckTransport()
        light = ZhiyunLight(transport)

        register = light.register_confirmed()
        brightness = light.set_brightness_confirmed(1, 25)
        scene = light.apply_scene_confirmed(Scene(obj=1, brightness=30, kelvin=5600))
        batches = light.transition_scene_confirmed(
            Scene(obj=1, brightness=0),
            Scene(obj=1, brightness=10),
            steps=2,
            duration=0,
        )

        self.assertTrue(register.acknowledged)
        self.assertTrue(brightness.acknowledged)
        self.assertTrue(command_results_acknowledged(scene))
        self.assertEqual(
            [result.command for batch in batches for result in batch],
            [RuntimeCommand.BRIGHTNESS, RuntimeCommand.BRIGHTNESS],
        )

    def test_confirmed_helpers_raise_for_unacknowledged_sdk_results(self) -> None:
        light = ZhiyunLight(TimeoutTransport())

        with self.assertRaises(UnconfirmedCommandError) as error:
            light.set_sleep_confirmed(1, 0)

        self.assertEqual(error.exception.action, "sleep")
        self.assertEqual(error.exception.statuses, ["sent_no_response"])
        self.assertEqual(error.exception.unconfirmed[0].command, RuntimeCommand.SLEEP)

        result = light.exchange_runtime(RuntimeCommand.BRIGHTNESS, b"\x01")
        with self.assertRaises(UnconfirmedCommandError):
            require_command_result(result, action="raw brightness")

    def test_apply_scene_requires_complete_rgb_tuple(self) -> None:
        light = ZhiyunLight(EchoAckTransport())

        with self.assertRaisesRegex(ValueError, "RGB"):
            light.apply_scene(Scene(obj=1, red=255))

    def test_transition_scene_applies_interpolated_updates(self) -> None:
        transport = EchoAckTransport()
        light = ZhiyunLight(transport)

        batches = light.transition_scene(
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
