from __future__ import annotations

import unittest

from zhiyun_light_control import Scene, SceneStateTracker


class FakeResult:
    transport_status = "acknowledged"
    acknowledged = True


class FakeUnconfirmedResult:
    transport_status = "sent_no_response"
    acknowledged = False


class StateTests(unittest.TestCase):
    def test_tracker_records_scene_and_statuses(self) -> None:
        tracker = SceneStateTracker()
        scene = Scene(obj=1, brightness=35)

        state = tracker.record(
            scene,
            source="test",
            action="scene",
            applied=True,
            results=[FakeResult()],
        )

        self.assertIs(tracker.snapshot(), state)
        payload = tracker.to_dict()
        self.assertEqual(payload["scene"]["brightness"], 35)
        self.assertEqual(payload["source"], "test")
        self.assertEqual(payload["action"], "scene")
        self.assertTrue(payload["applied"])
        self.assertEqual(payload["result_statuses"], ["acknowledged"])

    def test_tracker_infers_unconfirmed_results(self) -> None:
        tracker = SceneStateTracker()

        tracker.record(
            Scene(obj=1, brightness=35),
            source="test",
            action="brightness",
            results=[FakeUnconfirmedResult()],
        )

        payload = tracker.to_dict()
        self.assertFalse(payload["applied"])
        self.assertEqual(payload["reason"], "sent_no_response")
        self.assertEqual(payload["result_statuses"], ["sent_no_response"])

    def test_empty_tracker_payload_is_stable(self) -> None:
        self.assertEqual(SceneStateTracker().to_dict(), {"scene": None})


if __name__ == "__main__":
    unittest.main()
