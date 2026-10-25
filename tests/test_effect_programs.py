"""Pure effect-program formulas, validation, determinism and endpoint semantics."""

import math
import random

import pytest

from lighttime import programs
from lighttime.client import LightState
from lighttime.effects import EffectConfig

MASK = 0xFFFFFFFF
BASE = LightState(connected=True, brightness=5, cct=5000)


def resolved(config, state=BASE):
    return EffectConfig.parse(config).resolve(state)


def unh32(x):
    """Independent inverse built from the documented shifts and multipliers."""
    x ^= x >> 16
    x = (x * pow(0x846CA68B, -1, 2**32)) & MASK
    x ^= (x >> 15) ^ (x >> 30)
    x = (x * pow(0x7FEB352D, -1, 2**32)) & MASK
    x ^= x >> 16
    return x


def test_h32_matches_documented_bijection_and_pinned_vectors():
    sample = [0, 1, 2, 3, 0x7FFFFFFF, 0x80000000, MASK] + random.Random(7).sample(range(2**32), 300)
    assert all(unh32(programs.h32(x)) == x for x in sample)
    # Cross-language seed vectors: TypeScript must produce identical integers.
    assert [programs.h32(x) for x in (0, 1, 2, 0x7FFFFFFF, MASK, 123456789)] == [
        0,
        1753845952,
        3507691905,
        2368339896,
        1734902346,
        2834422664,
    ]
    assert programs.h32(2**32 + 1) == programs.h32(1)


def test_counter_rnd_vectors_range_and_independence():
    assert programs.rnd(1, 0, 0) == 0.40834908187389374
    assert programs.rnd(1, 1, 0) == 0.6268993967678398
    assert programs.rnd(1, 1, 1) == 0.4974512110929936
    assert programs.rnd(0, 0, 0) == 0.0
    assert programs.rnd(MASK, 5, 123456) == 0.3603505159262568
    values = [
        programs.rnd(seed, stream, n) for seed in (1, 2) for stream in range(6) for n in range(50)
    ]
    assert all(0 <= value < 1 for value in values)
    assert len(set(values)) == len(values)
    # The definition is literal modular arithmetic on the stream/counter word.
    word = (3 * 0x9E3779B9 + 77) & MASK
    assert programs.rnd(9, 3, 77) == programs.h32(9 ^ programs.h32(word)) / 2**32


@pytest.mark.parametrize(
    "value,expected", [(2.5, 3), (2.4999, 2), (0.5, 1), (-0.3, 0), (99.5, 100), (100.6, 100)]
)
def test_brightness_quantization_rounds_half_up(value, expected):
    assert programs.quantize_brightness(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(4550, 4600), (4549.99, 4500), (3650, 3700), (2600, 2700), (6551, 6500), (2649.9, 2700)],
)
def test_cct_quantization_rounds_half_up_in_100_k_units(value, expected):
    assert programs.quantize_cct(value) == expected


def test_exp_gap_and_value_noise_formulas():
    assert programs.exp_gap(6, 0.0, 1.5, 24) == 1.5
    assert programs.exp_gap(6, 1 - 2**-32, 1.5, 24) == 24
    assert programs.exp_gap(2, 0.5, 0, 10) == pytest.approx(2 * math.log(2))
    a, b = programs.rnd(4, 9, 12), programs.rnd(4, 9, 13)
    assert programs.noise(4, 9, 12 * 0.25, 0.25) == a  # Knots are exact.
    assert programs.noise(4, 9, 12.5 * 0.25, 0.25) == pytest.approx((a + b) / 2)


def test_blink_duty_edges_and_phase_validation():
    config = resolved(
        {"pattern": "blink", "period": 0.2, "duty": 0.5, "maxBrightness": 10, "cct": 2700}
    )
    assert [config.target(t) for t in (0, 0.099, 0.1, 0.15, 0.2)] == [
        (10, 2700),
        (10, 2700),
        (0, 2700),
        (0, 2700),
        (10, 2700),
    ]
    assert config.next_change(0) == pytest.approx(0.1)
    assert config.next_change(0.15) == pytest.approx(0.2)
    short = resolved({"pattern": "blink", "period": 0.25, "duty": 0.4, "maxBrightness": 9})
    assert short.target(0.09)[0] == 9 and short.target(0.11)[0] == 0
    for invalid in ({"period": 0.2, "duty": 0.4}, {"period": 0.19}, {"duty": 0}, {"duty": 1}):
        with pytest.raises(ValueError):
            EffectConfig.parse({"pattern": "blink", **invalid})


def test_pulse_heartbeat_and_accented_beat_envelopes():
    heart = resolved({"pattern": "pulse", "minBrightness": 0, "maxBrightness": 100, "cct": 3000})
    # Defaults: heartbeat at 60 BPM, second pulse 0.33 s later at 65%, tau 0.12 s.
    assert [heart.target(t)[0] for t in (0, 0.2, 0.33, 0.5, 1)] == [100, 19, 65, 16, 100]
    assert heart.target(0.5)[1] == 3000
    assert heart.next_change(0.05) == pytest.approx(0.15)
    assert heart.next_change(0.3) == pytest.approx(0.33)
    beat = resolved(
        {
            "pattern": "pulse",
            "style": "beat",
            "bpm": 120,
            "accentEvery": 4,
            "accentRatio": 0.5,
            "maxBrightness": 100,
        }
    )
    # Beat 0 is accented: e^-1 after the onset; beats 1-3 start at accentRatio.
    assert [beat.target(t)[0] for t in (0, 0.15, 0.5, 2.0)] == [100, 37, 50, 100]
    assert beat.target(0.5 + 0.15)[0] == 18  # 0.5 * e^-1 after an unaccented onset.
    with pytest.raises(ValueError, match="120"):
        EffectConfig.parse({"pattern": "pulse", "bpm": 121})
    assert EffectConfig.parse({"pattern": "pulse", "style": "beat", "bpm": 180}).options


def test_flicker_formula_bounds_cct_coupling_and_gusts():
    # target() clamps at a finite duration; these programs are sampled past 10 s.
    config = {
        "pattern": "flicker",
        "minBrightness": 10,
        "maxBrightness": 40,
        "gusts": 0,
        "duration": None,
    }
    candle = resolved(config)
    for t in (0, 0.35, 1.7, 9.2):
        n = 0.6 * programs.noise(1, 1, t, 0.45) + 0.4 * programs.noise(1, 2, t, 0.2)
        assert candle.target(t) == (programs.quantize_brightness(40 - 30 * 0.25 * n), 2700)
    fire = resolved({**config, "style": "fire", "speed": 2})
    times = [i * 0.07 for i in range(400)]
    outputs = [fire.target(t) for t in times]
    assert all(10 <= b <= 40 and 2700 <= k <= 3200 and k % 100 == 0 for b, k in outputs)
    assert len({k for _, k in outputs}) > 1  # Fire couples warmth by default.
    steady = resolved({**config, "depth": 0})
    assert {steady.target(t) for t in times} == {(40, 2700)}
    # A gust peak dips depth-free output by 0.35 * gusts of the range.
    gusty = resolved(
        {"pattern": "flicker", "depth": 0, "gusts": 1, "maxBrightness": 100, "duration": None}
    )
    assert gusty.program is not None
    first = gusty.program.gusts.locate(0).following  # type: ignore[attr-defined]
    length = 0.3 + 0.5 * programs.rnd(1, 4, 0)
    assert gusty.target(first + length / 2) == (65, 2700)
    assert gusty.target(first - 0.01) == (100, 2700)


def test_sunrise_and_sunset_endpoints_curves_and_defaults():
    rise = resolved({"pattern": "sunrise", "maxBrightness": 60})
    assert rise.duration == 900 and rise.restoreOnComplete is False and rise.curve == "perceptual"
    assert rise.target(0) == (0, 2700)
    assert rise.target(450) == (13, 4600)  # 60 * 0.5 ** 2.2; smoothstep midpoint CCT.
    assert rise.target(900) == rise.target(5000) == (60, 6500)
    sunset = resolved({"pattern": "sunrise", "direction": "set", "maxBrightness": 60})
    assert sunset.target(0) == (60, 6500) and sunset.target(900) == (0, 2700)
    assert sunset.target(450) == (13, 4600)
    linear = resolved({"pattern": "sunrise", "maxBrightness": 60, "curve": "linear"})
    assert linear.target(450) == (30, 4600)
    with pytest.raises(ValueError):
        EffectConfig.parse({"pattern": "sunrise", "duration": None})


def test_lightning_strike_structure_follows_seeded_timeline():
    config = resolved({"pattern": "lightning", "maxBrightness": 80, "duration": None})
    program = config.program
    assert program is not None
    strikes = program.strikes  # type: ignore[attr-defined]
    first = strikes.locate(0).following
    assert first == programs.exp_gap(6, programs.rnd(1, 1, 0), 1.5, 24) == 5.915443091672376
    assert config.target(first - 0.01) == (0, 6500)
    count = 1 + math.floor(3 * programs.rnd(1, 2, 0))
    begin = first
    for flash in range(count):
        level = 80 * (0.6 + 0.4 * programs.rnd(1, 3, flash))
        assert config.target(begin + 0.05) == (programs.quantize_brightness(level), 6500)
        begin += 0.1
        if flash < count - 1:
            assert config.target(begin + 0.05) == (0, 6500)
            begin += 0.1 + 0.15 * programs.rnd(1, 4, flash)
    assert config.target(begin + 0.05) == (20, 6500)  # Afterglow at 25% of the range.
    assert config.target(begin + 0.15) == (0, 6500)
    starts = [strikes.locate(t).start for t in range(0, 600, 1)]
    unique = sorted({start for start in starts if start is not None})
    assert all(b - a >= 1.5 for a, b in zip(unique, unique[1:], strict=False))


def test_emergency_alternates_sides_with_dark_cct_switch():
    config = resolved({"pattern": "emergency", "maxBrightness": 50, "duration": None})
    expected = {
        0: (50, 2700),
        0.15: (0, 2700),
        0.3: (50, 2700),
        0.45: (0, 2700),
        0.54: (0, 2700),
        0.55: (0, 6500),  # 0.1 s after the last flash: CCT changes while dark.
        0.9: (50, 6500),
        1.05: (0, 6500),
        1.2: (50, 6500),
        1.44: (0, 6500),
        1.46: (0, 2700),
        1.8: (50, 2700),
    }
    assert {t: config.target(t) for t in expected} == expected
    previous = config.target(0)
    t = 0.0
    for _ in range(40):
        nxt = config.next_change(t)
        assert nxt is not None and nxt > t
        current = config.target(nxt + 1e-6)
        # Brightness and CCT never change at the same edge.
        assert current[0] == previous[0] or current[1] == previous[1]
        previous, t = current, nxt + 1e-6


def test_paparazzi_flash_burst_and_rest_levels():
    for burst in (0, 1):
        config = resolved(
            {
                "pattern": "paparazzi",
                "interval": 5,
                "burst": burst,
                "maxBrightness": 60,
                "duration": None,
            }
        )
        assert config.program is not None
        located = config.program.flashes.locate(0)  # type: ignore[attr-defined]
        first = located.following
        assert first == programs.exp_gap(5, programs.rnd(1, 1, 0), 0.2, 30)
        level = programs.quantize_brightness(60 * (0.8 + 0.2 * programs.rnd(1, 2, 0)))
        assert config.target(first - 0.01) == (0, 5600)
        assert config.target(first + 0.05) == (level, 5600)
        assert config.target(first + 0.15) == (0, 5600)
        assert config.target(first + 0.25) == ((level if burst else 0), 5600)
        assert config.target(first + 0.35) == (0, 5600)


def test_television_scene_cuts_and_motion():
    still = resolved({"pattern": "tv", "maxBrightness": 40, "motion": 0, "duration": None})
    assert still.program is not None
    cut = still.program.cuts.locate(0).following  # type: ignore[attr-defined]
    level = 40 * (0.25 + 0.75 * programs.rnd(1, 2, 0))
    cct = programs.quantize_cct(5000 + 1500 * programs.rnd(1, 3, 0))
    assert still.target(0) == still.target(cut - 0.01) == (programs.quantize_brightness(level), cct)
    assert still.target(0) == (17, 6300)
    assert still.next_change(0) == cut
    moving = resolved(
        {"pattern": "tv", "minBrightness": 5, "maxBrightness": 40, "motion": 1, "duration": None}
    )
    outputs = [moving.target(i * 0.05) for i in range(400)]
    assert all(5 <= b <= 40 for b, _ in outputs)
    assert len({b for b, _ in outputs}) > len({k for _, k in outputs})


def test_morse_timing_natural_duration_and_loop_endpoint():
    assert programs.morse_spans("SOS") == (
        ((0, 1), (2, 3), (4, 5), (8, 11), (12, 15), (16, 19), (22, 23), (24, 25), (26, 27)),
        27,
    )
    assert programs.morse_spans("  e  E ") == (((0, 1), (8, 9)), 9)
    once = resolved({"pattern": "morse", "loop": False, "maxBrightness": 9, "cct": 3000})
    assert once.duration == pytest.approx(5.4) and once.restoreOnComplete is False
    assert [once.target(t)[0] for t in (0, 0.19, 0.2, 0.4, 1.6, 2.2, 5.39)] == [9, 9, 0, 9, 9, 0, 9]
    assert once.target(once.duration) == (0, 3000)  # Natural end: after the last dot.
    assert once.next_change(5.5) is None
    truncated = resolved({"pattern": "morse", "loop": False, "duration": 1, "maxBrightness": 9})
    assert truncated.duration == 1
    longer = resolved({"pattern": "morse", "loop": False, "duration": 60, "maxBrightness": 9})
    assert longer.duration == pytest.approx(5.4)
    period = 34 * 0.2
    looped = resolved({"pattern": "morse", "duration": period, "maxBrightness": 9})
    assert looped.restoreOnComplete is True
    assert looped.program is not None
    assert looped.program.sample(period)[0] == 9  # A running sample wraps to the next loop.
    assert looped.target(period)[0] == 0  # The finite endpoint does not wrap.
    assert looped.target(period + 0.1)[0] == 0  # Clamped to the endpoint.
    for text in ("SOS!", "", "   ", "é", "A" * 65, 5):
        with pytest.raises(ValueError):
            EffectConfig.parse({"pattern": "morse", "text": text})
    assert EffectConfig.parse({"pattern": "morse", "text": "hello world 73"}).options
    with pytest.raises(ValueError):
        EffectConfig.parse({"pattern": "morse", "loop": False, "duration": None})
    assert EffectConfig.parse({"pattern": "morse", "duration": None}).duration is None


def test_keyframe_eases_carry_forward_baseline_and_loops():
    frames = [{"at": 0, "brightness": 0, "cct": 2700}, {"at": 1, "brightness": 100, "cct": 6500}]
    linear = resolved({"pattern": "keyframes", "keyframes": frames, "loop": False})
    assert linear.duration == 1 and linear.restoreOnComplete is False
    assert [linear.target(t) for t in (0, 0.25, 0.5, 1, 7)] == [
        (0, 2700),
        (25, 3700),
        (50, 4600),
        (100, 6500),
        (100, 6500),
    ]
    smooth = resolved(
        {"pattern": "keyframes", "keyframes": [frames[0], {**frames[1], "ease": "smooth"}]}
    )
    assert smooth.target(0.25) == (16, 3300)
    perceptual = resolved(
        {"pattern": "keyframes", "keyframes": [frames[0], {**frames[1], "ease": "perceptual"}]}
    )
    assert perceptual.target(0.5) == (22, 3500)
    hold = resolved(
        {
            "pattern": "keyframes",
            "loop": False,
            "keyframes": [{"at": 0, "brightness": 10}, {"at": 1, "brightness": 50, "ease": "hold"}],
        }
    )
    assert hold.target(0.99) == (10, 5000) and hold.target(1) == (50, 5000)
    assert hold.next_change(0.2) == 1
    carried = resolved(
        {
            "pattern": "keyframes",
            "cct": 3300,  # Common fields are accepted but keyframes use absolute values.
            "maxBrightness": 90,
            "keyframes": [
                {"at": 0, "brightness": 10},
                {"at": 0.5, "cct": 3000},
                {"at": 1, "brightness": 20},
            ],
        }
    )
    assert carried.target(0) == (10, 5000)  # Omitted first CCT is the verified baseline.
    assert carried.target(0.25) == (10, 4000)
    assert carried.target(0.75) == (15, 3000)
    looped = resolved({"pattern": "keyframes", "keyframes": frames, "duration": 2})
    assert looped.restoreOnComplete is True
    assert looped.program is not None
    assert looped.program.sample(1.25) == linear.target(0.25)
    assert looped.program.sample(1.0) == (0, 2700)  # Running samples wrap at the last frame.
    assert looped.target(2) == (100, 6500)  # The exact finite endpoint does not.
    blank = resolved({"pattern": "keyframes", "keyframes": [{"at": 0}, {"at": 1}]})
    assert blank.target(0.5) == (5, 5000)


@pytest.mark.parametrize(
    "keyframes",
    [
        [{"at": 0}],
        [{"at": 0.1}, {"at": 1}],
        [{"at": 0}, {"at": 0.05}],
        [{"at": 0}, {"at": 1}, {"at": 0.5}],
        [{"at": 0}, {"at": 3601}],
        [{"at": 0}, {"at": True}],
        [{"at": 0}, {"brightness": 5}],
        [{"at": 0}, {"at": 1, "brightness": 5.5}],
        [{"at": 0}, {"at": 1, "cct": 4550}],
        [{"at": 0}, {"at": 1, "brightness": None}],
        [{"at": 0}, {"at": 1, "ease": "bounce"}],
        [{"at": 0}, {"at": 1, "extra": 1}],
        [{"at": 0}, 5],
        [{"at": i * 0.1} for i in range(257)],
        {"at": 0},
    ],
)
def test_invalid_keyframes_rejected(keyframes):
    with pytest.raises(ValueError):
        EffectConfig.parse({"pattern": "keyframes", "keyframes": keyframes})


def test_keyframes_require_frames_and_accept_float_spacing_tolerance():
    with pytest.raises(ValueError, match="required"):
        EffectConfig.parse({"pattern": "keyframes"})
    frames = [{"at": round(i * 0.1, 10)} for i in range(256)]
    options = EffectConfig.parse({"pattern": "keyframes", "keyframes": frames}).options
    assert isinstance(options, programs.KeyframesOptions) and len(options.keyframes) == 256


@pytest.mark.parametrize(
    "config",
    [
        {"pattern": "blink", "bpm": 60},
        {"pattern": "pulse", "duty": 0.5},
        {"pattern": "flicker", "interval": 2},
        {"pattern": "tv", "burst": 0.5},
        {"pattern": "keyframes", "keyframes": [{"at": 0}, {"at": 1}], "text": "SOS"},
        {"pattern": "morse", "keyframes": []},
        {"pattern": "sunrise", "style": "fire"},
        {"pattern": "emergency", "flashesMax": 2},
        {"pattern": "fade-in", "direction": "set"},
        {"pattern": "pulse", "allowRapidFlash": True},
        {"pattern": "pulse", "seed": -1},
        {"pattern": "pulse", "seed": 2**32},
        {"pattern": "pulse", "seed": 1.0},
        {"pattern": "pulse", "seed": True},
        {"pattern": "pulse", "curve": "cubic"},
        {"pattern": "pulse", "style": "lub-dub"},
        {"pattern": "pulse", "accentEvery": 17},
        {"pattern": "pulse", "accentEvery": 2.0},
        {"pattern": "pulse", "decay": 0.04},
        {"pattern": "flicker", "depth": 0.61},
        {"pattern": "flicker", "speed": 2.1},
        {"pattern": "lightning", "interval": 1.9},
        {"pattern": "lightning", "flashesMax": 5},
        {"pattern": "emergency", "flashOn": 0.09},
        {"pattern": "emergency", "gap": 0.19},
        {"pattern": "emergency", "flashes": 0},
        {"pattern": "paparazzi", "interval": 0.3},
        {"pattern": "paparazzi", "burst": 1.1},
        {"pattern": "tv", "interval": 0.4},
        {"pattern": "tv", "motion": -0.1},
        {"pattern": "morse", "unit": 0.09},
        {"pattern": "morse", "loop": "yes"},
        {"pattern": "keyframes", "keyframes": [{"at": 0}, {"at": 1}], "loop": 1},
        {"pattern": "breathe", "period": 0.5},
        {"pattern": "flicker", "period": 0.5},
        {"pattern": "fade-out", "duration": None},
    ],
)
def test_pattern_specific_validation(config):
    with pytest.raises(ValueError):
        EffectConfig.parse(config)


@pytest.mark.parametrize(
    "pattern,duration,restore,cct,end",
    [
        ("blink", 10, True, None, 6500),
        ("breathe", 10, True, None, 6500),
        ("fade-in", 10, False, None, 6500),
        ("fade-out", 10, False, None, 6500),
        ("cct-sweep", 10, True, None, 6500),
        ("pulse", 10, True, None, 6500),
        ("flicker", 10, True, 2700, 3200),
        ("sunrise", 900, False, 2700, 6500),
        ("lightning", 10, True, 6500, 6500),
        ("emergency", 10, True, 2700, 6500),
        ("paparazzi", 10, True, 5600, 6500),
        ("tv", 10, True, 5000, 6500),
        ("morse", 10, True, None, 6500),
    ],
)
def test_defaults_and_common_fields(pattern, duration, restore, cct, end):
    config = EffectConfig.parse({"pattern": pattern})
    assert (config.duration, config.restoreOnComplete, config.cct, config.cctEnd) == (
        duration,
        restore,
        cct,
        end,
    )
    assert config.seed == 1
    assert config.curve == ("perceptual" if pattern == "sunrise" else "linear")
    common = {"period": 4, "cctEnd": 4000, "seed": MASK, "curve": "linear", "duration": 5}
    assert EffectConfig.parse({"pattern": pattern, **common}).seed == MASK
    assert resolved({"pattern": pattern}).target(0)


STOCHASTIC = [
    {"pattern": "flicker", "maxBrightness": 50, "style": "fire", "seed": 7, "duration": None},
    {
        "pattern": "lightning",
        "maxBrightness": 50,
        "interval": 2,
        "flashesMax": 4,
        "seed": 7,
        "duration": None,
    },
    {"pattern": "paparazzi", "maxBrightness": 50, "interval": 0.33, "seed": 7, "duration": None},
    {"pattern": "tv", "maxBrightness": 50, "interval": 0.5, "seed": 7, "duration": None},
]


@pytest.mark.parametrize("config", STOCHASTIC, ids=lambda config: config["pattern"])
def test_seeded_output_is_independent_of_sampling_order_and_skips(config):
    times = [round(i * 0.037, 6) for i in range(3000)]
    # Each reference value comes from a fresh program with no cursor history.
    reference = {t: resolved(config).target(t) for t in times}
    assert len(set(reference.values())) > 2
    shuffled = times[:]
    random.Random(3).shuffle(shuffled)
    program = resolved(config)
    assert {t: program.target(t) for t in shuffled} == reference
    skipping = resolved(config)
    assert {t: skipping.target(t) for t in times[::97]} == {t: reference[t] for t in times[::97]}
    reseeded = resolved({**config, "seed": 8})
    assert [reseeded.target(t) for t in times] != list(reference.values())


@pytest.mark.parametrize("config", STOCHASTIC[1:], ids=lambda config: config["pattern"])
def test_event_cursor_memory_stays_bounded(config):
    program = resolved({**config, "duration": None}).program
    assert program is not None
    renewal = next(value for value in vars(program).values() if isinstance(value, programs.Renewal))
    size = len(vars(renewal))
    for t in range(0, 200_000, 37):
        program.sample(float(t))
    assert len(vars(renewal)) == size
    assert all(not isinstance(value, (list, dict, set)) for value in vars(renewal).values())


EVENT_PROGRAMS = [
    {"pattern": "blink", "period": 0.7, "duty": 0.3, "maxBrightness": 9},
    {"pattern": "lightning", "maxBrightness": 50, "interval": 2},
    {"pattern": "emergency", "maxBrightness": 50, "flashes": 3},
    {"pattern": "paparazzi", "maxBrightness": 50, "interval": 0.33, "burst": 1},
    {"pattern": "tv", "maxBrightness": 50, "motion": 0},
    {"pattern": "morse", "text": "CQ DX", "unit": 0.1, "maxBrightness": 9},
    {"pattern": "morse", "text": "E", "unit": 0.1, "loop": False, "maxBrightness": 9},
    {
        "pattern": "keyframes",
        "keyframes": [
            {"at": 0, "brightness": 1},
            {"at": 0.4, "brightness": 30, "ease": "hold"},
            {"at": 1.1, "cct": 3000, "ease": "hold"},
        ],
    },
]


@pytest.mark.parametrize("config", EVENT_PROGRAMS, ids=lambda config: config["pattern"])
def test_event_output_is_constant_until_next_change(config):
    program = resolved({**config, "duration": None} if config.get("loop") is not False else config)
    assert program.program is not None
    rng = random.Random(11)
    for _ in range(400):
        t = rng.uniform(0, 30)
        nxt = program.program.next_change(t)
        value = program.program.sample(t)
        if nxt is None:
            assert program.program.sample(t + 100) == value
            continue
        assert nxt > t
        for fraction in (0.01, 0.5, 0.999):
            assert program.program.sample(t + (nxt - t) * fraction) == value


@pytest.mark.parametrize("pattern", programs.PATTERNS)
def test_next_change_is_strictly_later_for_every_pattern(pattern):
    config = {"pattern": pattern, "maxBrightness": 20}
    if pattern == "keyframes":
        config["keyframes"] = [{"at": 0, "brightness": 1}, {"at": 0.3, "brightness": 9}]
    program = resolved(config)
    rng = random.Random(5)
    for _ in range(200):
        t = rng.uniform(0, 12)
        nxt = program.next_change(t)
        assert nxt is None or nxt > t
