"""Cross-language effect vectors also exercise rewind and skipped-time sampling."""

import json
from pathlib import Path

import pytest

from lighttime.client import LightState
from lighttime.effects import EffectConfig

VECTORS = json.loads(
    Path(__file__).with_name("fixtures").joinpath("effect-vectors.json").read_text()
)


@pytest.mark.parametrize("case", VECTORS["cases"])
def test_shared_effect_vectors(case):
    state = LightState(connected=True, **VECTORS["baseline"])
    resolved = EffectConfig.parse(case["config"]).resolve(state)
    samples = case["samples"]
    # The same function must agree when rendering continuously, after a long
    # scheduler overrun, and after seeking backwards for an editor preview.
    for sequence in (samples, list(reversed(samples)), samples[::3] + samples[1::3]):
        for elapsed, brightness, cct in sequence:
            assert resolved.target(elapsed) == (brightness, cct)
