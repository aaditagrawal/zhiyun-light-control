from __future__ import annotations

import unittest

from zhiyun_light_control.artnet import (
    ArtNetLightDispatcher,
    DmxMapping,
    decode_artdmx,
    encode_artdmx,
    scene_from_dmx,
)
from zhiyun_light_control.state import SceneStateTracker


class FakeResult:
    acknowledged = True
    transport_status = "acknowledged"


class FakeUnconfirmedResult:
    acknowledged = False
    transport_status = "sent_no_response"


class FakeLight:
    def __init__(self) -> None:
        self.scenes = []

    def __enter__(self) -> FakeLight:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return

    def apply_scene(self, scene):
        self.scenes.append(scene)
        return [FakeResult()]


class FakeUnconfirmedLight(FakeLight):
    def apply_scene(self, scene):
        self.scenes.append(scene)
        return [FakeUnconfirmedResult()]


class ArtNetTests(unittest.TestCase):
    def test_encode_decode_artdmx_round_trip(self) -> None:
        payload = bytes([0, 128, 255])

        packet = decode_artdmx(
            encode_artdmx(payload, universe=0x0102, sequence=7, physical=3)
        )

        self.assertEqual(packet.universe, 0x0102)
        self.assertEqual(packet.sequence, 7)
        self.assertEqual(packet.physical, 3)
        self.assertEqual(packet.data[:3], payload)

    def test_scene_from_dmx_maps_brightness_and_cct(self) -> None:
        scene = scene_from_dmx(
            bytes([128, 255]),
            DmxMapping(obj=4, brightness_channel=1, cct_channel=2),
        )

        self.assertEqual(scene.obj, 4)
        self.assertAlmostEqual(scene.brightness, 128 / 255 * 100)
        self.assertEqual(scene.kelvin, 6500)
        self.assertIsNone(scene.sleep)

    def test_scene_from_dmx_sleep_is_explicit_opt_in(self) -> None:
        scene = scene_from_dmx(
            bytes([255, 0, 0]),
            DmxMapping(sleep_channel=3),
        )

        self.assertEqual(scene.sleep, 1)

    def test_dispatch_ignores_other_universe(self) -> None:
        light = FakeLight()
        dispatcher = ArtNetLightDispatcher(
            lambda: light, universe=2, allow_control=True
        )
        packet = decode_artdmx(encode_artdmx(bytes([255, 255]), universe=1))

        result = dispatcher.dispatch(packet)

        self.assertFalse(result.applied)
        self.assertEqual(result.reason, "universe_ignored")
        self.assertEqual(light.scenes, [])

    def test_dispatch_applies_changed_scene_once(self) -> None:
        light = FakeLight()
        tracker = SceneStateTracker()
        dispatcher = ArtNetLightDispatcher(
            lambda: light,
            universe=0,
            allow_control=True,
            state_tracker=tracker,
        )
        packet = decode_artdmx(encode_artdmx(bytes([255, 255]), universe=0))

        first = dispatcher.dispatch(packet)
        second = dispatcher.dispatch(packet)

        self.assertTrue(first.applied)
        self.assertFalse(second.applied)
        self.assertEqual(second.reason, "unchanged")
        self.assertEqual(len(light.scenes), 1)
        state = tracker.to_dict()
        self.assertEqual(state["source"], "artnet")
        self.assertTrue(state["applied"])
        self.assertEqual(state["scene"]["brightness"], 100.0)

    def test_dispatch_marks_unacknowledged_scene_unapplied(self) -> None:
        light = FakeUnconfirmedLight()
        tracker = SceneStateTracker()
        dispatcher = ArtNetLightDispatcher(
            lambda: light,
            universe=0,
            allow_control=True,
            state_tracker=tracker,
        )
        packet = decode_artdmx(encode_artdmx(bytes([255, 255]), universe=0))

        result = dispatcher.dispatch(packet)

        self.assertFalse(result.applied)
        self.assertEqual(result.reason, "sent_no_response")
        state = tracker.to_dict()
        self.assertFalse(state["applied"])
        self.assertEqual(state["reason"], "sent_no_response")
        self.assertEqual(state["result_statuses"], ["sent_no_response"])


if __name__ == "__main__":
    unittest.main()
