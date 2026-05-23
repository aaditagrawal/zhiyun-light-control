from __future__ import annotations

import unittest

from zhiyun_light_control.artnet import DmxMapping
from zhiyun_light_control.sacn import (
    SacnError,
    SacnLightDispatcher,
    decode_sacn,
    encode_sacn,
    sacn_multicast_address,
)


class FakeLight:
    def __init__(self) -> None:
        self.scenes = []

    def __enter__(self) -> "FakeLight":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return

    def apply_scene(self, scene):
        self.scenes.append(scene)
        return []


class SacnTests(unittest.TestCase):
    def test_encode_decode_sacn_round_trip(self) -> None:
        payload = bytes([0, 128, 255])

        packet = decode_sacn(
            encode_sacn(
                payload,
                universe=7,
                sequence=9,
                priority=120,
                source_name="test-source",
            )
        )

        self.assertEqual(packet.universe, 7)
        self.assertEqual(packet.sequence, 9)
        self.assertEqual(packet.priority, 120)
        self.assertEqual(packet.source_name, "test-source")
        self.assertEqual(packet.data, payload)

    def test_sacn_multicast_address(self) -> None:
        self.assertEqual(sacn_multicast_address(1), "239.255.0.1")
        self.assertEqual(sacn_multicast_address(0x1234), "239.255.18.52")

    def test_decode_rejects_nonzero_dmx_start_code(self) -> None:
        packet = bytearray(encode_sacn(bytes([255]), universe=1))
        packet[125] = 1

        with self.assertRaisesRegex(SacnError, "start code"):
            decode_sacn(bytes(packet))

    def test_dispatch_applies_changed_scene_once(self) -> None:
        light = FakeLight()
        dispatcher = SacnLightDispatcher(lambda: light, universe=1, allow_control=True)
        packet = decode_sacn(encode_sacn(bytes([255, 255]), universe=1))

        first = dispatcher.dispatch(packet)
        second = dispatcher.dispatch(packet)

        self.assertTrue(first.applied)
        self.assertFalse(second.applied)
        self.assertEqual(second.reason, "unchanged")
        self.assertEqual(len(light.scenes), 1)

    def test_dispatch_ignores_other_universe(self) -> None:
        light = FakeLight()
        dispatcher = SacnLightDispatcher(
            lambda: light,
            universe=3,
            mapping=DmxMapping(obj=2),
            allow_control=True,
        )
        packet = decode_sacn(encode_sacn(bytes([255, 255]), universe=2))

        result = dispatcher.dispatch(packet)

        self.assertFalse(result.applied)
        self.assertEqual(result.reason, "universe_ignored")
        self.assertEqual(light.scenes, [])


if __name__ == "__main__":
    unittest.main()
