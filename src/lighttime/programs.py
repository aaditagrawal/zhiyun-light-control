"""Pure, deterministic effect programs over whole-percent brightness and 100 K CCT.

A program maps elapsed seconds to one quantized ``(brightness, cct)`` target. It
performs no I/O. Randomness is the counter-based ``h32``/``rnd`` hash keyed by
seed, stream and event index, so the output at a time never depends on which
earlier times were sampled. Stochastic event timelines keep only a forward
cursor and rewind deterministically, which bounds memory for unbounded runs.
"""

from __future__ import annotations

import bisect
import math
import string
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import NamedTuple

from .protocol import validate_brightness, validate_cct

PATTERNS = (
    "blink",
    "breathe",
    "fade-in",
    "fade-out",
    "cct-sweep",
    "pulse",
    "flicker",
    "sunrise",
    "lightning",
    "emergency",
    "paparazzi",
    "tv",
    "morse",
    "keyframes",
)
COMMON_FIELDS = frozenset(
    {
        "pattern",
        "duration",
        "period",
        "minBrightness",
        "maxBrightness",
        "cct",
        "cctEnd",
        "restoreOnComplete",
        "seed",
        "curve",
    }
)
PATTERN_FIELDS: Mapping[str, frozenset[str]] = {
    "blink": frozenset({"duty"}),
    "breathe": frozenset(),
    "fade-in": frozenset(),
    "fade-out": frozenset(),
    "cct-sweep": frozenset(),
    "pulse": frozenset({"bpm", "style", "accentEvery", "accentRatio", "decay"}),
    "flicker": frozenset({"style", "depth", "speed", "gusts", "cctCoupling"}),
    "sunrise": frozenset({"direction"}),
    "lightning": frozenset({"interval", "flashesMax"}),
    "emergency": frozenset({"flashes", "flashOn", "flashOff", "gap"}),
    "paparazzi": frozenset({"interval", "burst"}),
    "tv": frozenset({"interval", "motion"}),
    "morse": frozenset({"text", "unit", "loop"}),
    "keyframes": frozenset({"keyframes", "loop"}),
}

SAMPLE_INTERVAL = 0.1  # Seconds between samples of continuously varying output.
MIN_STEP = 0.1  # Shortest blink phase and keyframe spacing, in seconds.
TOLERANCE = 1e-9  # Accepts decimal inputs whose binary product is just below MIN_STEP.
SEED_MAX = 0xFFFFFFFF

# ---------------------------------------------------------------------------
# Shared arithmetic. Python and TypeScript must evaluate these identically.

_MASK = 0xFFFFFFFF


def h32(x: int) -> int:
    """lowbias32 integer hash; every operation is modulo 2**32."""
    x &= _MASK
    x ^= x >> 16
    x = (x * 0x7FEB352D) & _MASK
    x ^= x >> 15
    x = (x * 0x846CA68B) & _MASK
    x ^= x >> 16
    return x


def rnd(seed: int, stream: int, n: int) -> float:
    """Counter-based uniform value in [0, 1) for event ``n`` of a stream."""
    return h32(seed ^ h32((stream * 0x9E3779B9 + n) & _MASK)) / 4294967296.0


def exp_gap(mean: float, u: float, low: float, high: float) -> float:
    return min(high, max(low, -mean * math.log(1.0 - u)))


def quantize_brightness(value: float) -> int:
    return min(100, max(0, math.floor(value + 0.5)))


def quantize_cct(value: float) -> int:
    return min(6500, max(2700, 100 * math.floor(value / 100 + 0.5)))


def linear(u: float) -> float:
    return u


def perceptual(u: float) -> float:
    return u**2.2


def smooth(u: float) -> float:
    return u * u * (3 - 2 * u)


def cosine(u: float) -> float:
    return (1 - math.cos(math.pi * u)) / 2


CURVES: Mapping[str, Callable[[float], float]] = {"linear": linear, "perceptual": perceptual}
EASES: Mapping[str, Callable[[float], float]] = {
    "linear": linear,
    "smooth": smooth,
    "perceptual": perceptual,
}


def noise(seed: int, stream: int, t: float, period: float) -> float:
    """Cosine-interpolated value noise with knots every ``period`` seconds."""
    x = t / period
    n = math.floor(x)
    a = rnd(seed, stream, n)
    return a + (rnd(seed, stream, n + 1) - a) * cosine(x - n)


class Located(NamedTuple):
    index: int  # Latest event at or before t; -1 before the first event.
    previous: float | None  # Start of event index - 1, if any.
    start: float | None  # Start of event index, if any.
    following: float  # Start of event index + 1.


class Renewal:
    """Event starts t_i = t_(i-1) + gap(i), from t_(-1) = 0, or t_0 = 0 if anchored.

    Only the cursor is retained. Seeking backwards recomputes from the first
    event in the same order, so results are identical for every access pattern.
    """

    _index: int
    _previous: float | None
    _start: float
    _next: float

    def __init__(self, gap: Callable[[int], float], *, anchored: bool = False):
        self._gap = gap
        self._anchored = anchored
        self._rewind()

    def _rewind(self) -> None:
        self._index = 0
        self._previous = None
        self._start = 0.0 if self._anchored else self._gap(0)
        self._next = self._start + self._gap(1)

    def locate(self, t: float) -> Located:
        if t < self._start:
            self._rewind()
            if t < self._start:
                return Located(-1, None, None, self._start)
        while self._next <= t:
            self._index += 1
            self._previous = self._start
            self._start = self._next
            self._next = self._start + self._gap(self._index + 1)
        return Located(self._index, self._previous, self._start, self._next)


def _first_after(t: float, candidates: Iterable[float]) -> float | None:
    best = None
    for candidate in candidates:
        if candidate > t and (best is None or candidate < best):
            best = candidate
    return best


# ---------------------------------------------------------------------------
# Validation helpers. Range checks precede float conversion so arbitrarily
# large JSON integers remain ordinary validation errors.


def number(name: str, value: object, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number from {low:g} to {high:g}")
    if not low <= value <= high or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and from {low:g} to {high:g}")
    return float(value)


def integer(name: str, value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be a whole number from {low} to {high}")
    return value


def choice(name: str, value: object, options: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in options:
        raise ValueError(f"{name} must be one of {', '.join(options)}")
    return value


def flag(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def brightness_value(name: str, value: object) -> float:
    if isinstance(value, int) and not isinstance(value, bool) and not 0 <= value <= 100:
        raise ValueError(f"{name} must be a whole percent from 0 to 100")
    try:
        return validate_brightness(value)
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}") from None


def cct_value(name: str, value: object) -> int:
    try:
        return validate_cct(value)
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}") from None


# ---------------------------------------------------------------------------
# Pattern-specific options.


@dataclass(frozen=True)
class NoOptions:
    pass


@dataclass(frozen=True)
class BlinkOptions:
    duty: float = 0.5


@dataclass(frozen=True)
class PulseOptions:
    bpm: float = 60
    style: str = "heartbeat"
    accentEvery: int = 0
    accentRatio: float = 0.6
    decay: float = 0.3


@dataclass(frozen=True)
class FlickerOptions:
    style: str = "candle"
    depth: float = 0.25
    speed: float = 1
    gusts: float = 0.15
    cctCoupling: float = 0


@dataclass(frozen=True)
class SunriseOptions:
    direction: str = "rise"


@dataclass(frozen=True)
class LightningOptions:
    interval: float = 6
    flashesMax: int = 3


@dataclass(frozen=True)
class EmergencyOptions:
    flashes: int = 2
    flashOn: float = 0.15
    flashOff: float = 0.15
    gap: float = 0.3


@dataclass(frozen=True)
class PaparazziOptions:
    interval: float = 0.8
    burst: float = 0.25


@dataclass(frozen=True)
class TelevisionOptions:
    interval: float = 2.5
    motion: float = 0.3


@dataclass(frozen=True)
class MorseOptions:
    text: str = "SOS"
    unit: float = 0.2
    loop: bool = True


@dataclass(frozen=True)
class Keyframe:
    at: float
    brightness: float | None = None
    cct: int | None = None
    ease: str = "linear"


@dataclass(frozen=True)
class KeyframesOptions:
    keyframes: tuple[Keyframe, ...] = ()
    loop: bool = True


type Options = (
    NoOptions
    | BlinkOptions
    | PulseOptions
    | FlickerOptions
    | SunriseOptions
    | LightningOptions
    | EmergencyOptions
    | PaparazziOptions
    | TelevisionOptions
    | MorseOptions
    | KeyframesOptions
)

# Defaults for omitted common fields. None means the verified baseline CCT.
_CCT_DEFAULTS: Mapping[str, tuple[int | None, int]] = {
    "flicker": (2700, 3200),
    "sunrise": (2700, 6500),
    "lightning": (6500, 6500),
    "emergency": (2700, 6500),
    "paparazzi": (5600, 6500),
    "tv": (5000, 6500),
}


def cct_defaults(pattern: str) -> tuple[int | None, int]:
    return _CCT_DEFAULTS.get(pattern, (None, 6500))


def default_curve(pattern: str) -> str:
    return "perceptual" if pattern == "sunrise" else "linear"


def default_duration(pattern: str) -> float:
    return 900 if pattern == "sunrise" else 10


def period_range(pattern: str) -> tuple[float, float]:
    return (0.2, 60) if pattern == "blink" else (1, 60)


def check_fields(pattern: str, data: Mapping[str, object]) -> None:
    unknown = set(data) - COMMON_FIELDS - PATTERN_FIELDS[pattern]
    if unknown:
        raise ValueError(f"Unknown effect property for {pattern}: {', '.join(sorted(unknown))}")


MORSE_CODE: Mapping[str, str] = {
    "A": ".-",
    "B": "-...",
    "C": "-.-.",
    "D": "-..",
    "E": ".",
    "F": "..-.",
    "G": "--.",
    "H": "....",
    "I": "..",
    "J": ".---",
    "K": "-.-",
    "L": ".-..",
    "M": "--",
    "N": "-.",
    "O": "---",
    "P": ".--.",
    "Q": "--.-",
    "R": ".-.",
    "S": "...",
    "T": "-",
    "U": "..-",
    "V": "...-",
    "W": ".--",
    "X": "-..-",
    "Y": "-.--",
    "Z": "--..",
    "0": "-----",
    "1": ".----",
    "2": "..---",
    "3": "...--",
    "4": "....-",
    "5": ".....",
    "6": "-....",
    "7": "--...",
    "8": "---..",
    "9": "----.",
    ".": ".-.-.-",
    ",": "--..--",
    "?": "..--..",
    "/": "-..-.",
    "=": "-...-",
    "+": ".-.-.",
    "-": "-....-",
    "@": ".--.-.",
}
_MORSE_TEXT = frozenset(MORSE_CODE) | frozenset(string.ascii_lowercase) | {" "}


def morse_spans(text: str) -> tuple[tuple[tuple[int, int], ...], int]:
    """On-intervals in whole units and the unit count to the end of the last symbol.

    Dot 1, dash 3, gap 1 inside a character, 3 between characters and 7 between
    words. Runs of spaces separate words; leading/trailing spaces are ignored.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for word_index, word in enumerate(word for word in text.upper().split(" ") if word):
        if word_index:
            cursor += 7
        for char_index, char in enumerate(word):
            if char_index:
                cursor += 3
            for symbol_index, symbol in enumerate(MORSE_CODE[char]):
                if symbol_index:
                    cursor += 1
                length = 1 if symbol == "." else 3
                spans.append((cursor, cursor + length))
                cursor += length
    return tuple(spans), cursor


def _keyframes(value: object) -> tuple[Keyframe, ...]:
    if not isinstance(value, list) or not 2 <= len(value) <= 256:
        raise ValueError("keyframes must be a list of 2 to 256 frames")
    frames: list[Keyframe] = []
    for index, item in enumerate(value):
        name = f"keyframes[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{name} must be an object")
        if set(item) - {"at", "brightness", "cct", "ease"}:
            raise ValueError(f"{name} accepts only at, brightness, cct and ease")
        if "at" not in item:
            raise ValueError(f"{name}.at is required")
        at = number(f"{name}.at", item["at"], 0, 3600)
        if index == 0 and at != 0:
            raise ValueError("The first keyframe must be at 0 seconds")
        if index and at - frames[-1].at < MIN_STEP - TOLERANCE:
            raise ValueError(f"{name}.at must be at least 0.1 s after the previous frame")
        frames.append(
            Keyframe(
                at,
                brightness_value(f"{name}.brightness", item["brightness"])
                if "brightness" in item
                else None,
                cct_value(f"{name}.cct", item["cct"]) if "cct" in item else None,
                choice(f"{name}.ease", item["ease"], ("hold", "linear", "smooth", "perceptual"))
                if "ease" in item
                else "linear",
            )
        )
    return tuple(frames)


def parse_options(pattern: str, data: Mapping[str, object], period: float) -> Options:
    """Validate pattern-specific fields; ``period`` is the already validated value."""
    if pattern == "blink":
        duty = number("duty", data.get("duty", 0.5), 0, 1)
        if duty * period < MIN_STEP - TOLERANCE or (1 - duty) * period < MIN_STEP - TOLERANCE:
            raise ValueError("Both blink phases (duty × period) must last at least 0.1 s")
        return BlinkOptions(duty)
    if pattern == "pulse":
        style = choice("style", data.get("style", "heartbeat"), ("heartbeat", "beat"))
        bpm = number("bpm", data.get("bpm", 60), 20, 180)
        if style == "heartbeat" and bpm > 120:
            raise ValueError("Heartbeat pulses require bpm of 120 or less")
        return PulseOptions(
            bpm,
            style,
            integer("accentEvery", data.get("accentEvery", 0), 0, 16),
            number("accentRatio", data.get("accentRatio", 0.6), 0.2, 1),
            number("decay", data.get("decay", 0.3), 0.05, 1),
        )
    if pattern == "flicker":
        style = choice("style", data.get("style", "candle"), ("candle", "fire"))
        fire = style == "fire"
        return FlickerOptions(
            style,
            number("depth", data.get("depth", 0.55 if fire else 0.25), 0, 0.6),
            number("speed", data.get("speed", 1), 0.25, 2),
            number("gusts", data.get("gusts", 0.5 if fire else 0.15), 0, 1),
            number("cctCoupling", data.get("cctCoupling", 0.5 if fire else 0), 0, 1),
        )
    if pattern == "sunrise":
        return SunriseOptions(choice("direction", data.get("direction", "rise"), ("rise", "set")))
    if pattern == "lightning":
        return LightningOptions(
            number("interval", data.get("interval", 6), 2, 60),
            integer("flashesMax", data.get("flashesMax", 3), 1, 4),
        )
    if pattern == "emergency":
        return EmergencyOptions(
            integer("flashes", data.get("flashes", 2), 1, 4),
            number("flashOn", data.get("flashOn", 0.15), 0.1, 10),
            number("flashOff", data.get("flashOff", 0.15), 0.1, 10),
            number("gap", data.get("gap", 0.3), 0.2, 30),
        )
    if pattern == "paparazzi":
        return PaparazziOptions(
            number("interval", data.get("interval", 0.8), 0.33, 5),
            number("burst", data.get("burst", 0.25), 0, 1),
        )
    if pattern == "tv":
        return TelevisionOptions(
            number("interval", data.get("interval", 2.5), 0.5, 10),
            number("motion", data.get("motion", 0.3), 0, 1),
        )
    if pattern == "morse":
        text = data.get("text", "SOS")
        if (
            not isinstance(text, str)
            or not 1 <= len(text) <= 64
            or any(char not in _MORSE_TEXT for char in text)
            or not text.strip(" ")
        ):
            raise ValueError(
                "text must be 1 to 64 characters of A-Z, 0-9, space and . , ? / = + - @"
            )
        return MorseOptions(
            text,
            number("unit", data.get("unit", 0.2), 0.1, 2),
            flag("loop", data.get("loop", True)),
        )
    if pattern == "keyframes":
        if "keyframes" not in data:
            raise ValueError("keyframes is required")
        return KeyframesOptions(_keyframes(data["keyframes"]), flag("loop", data.get("loop", True)))
    return NoOptions()


def loops(pattern: str, options: Options) -> bool:
    """Whether a run may be unbounded and restores by default on completion."""
    if pattern in ("fade-in", "fade-out", "sunrise"):
        return False
    if isinstance(options, (MorseOptions, KeyframesOptions)):
        return options.loop
    return True


def natural_duration(options: Options) -> float | None:
    """Completion time of a non-looping Morse message or keyframe program."""
    if isinstance(options, MorseOptions) and not options.loop:
        return morse_spans(options.text)[1] * options.unit
    if isinstance(options, KeyframesOptions) and not options.loop:
        return options.keyframes[-1].at
    return None


# ---------------------------------------------------------------------------
# Programs.


@dataclass(frozen=True)
class Levels:
    low: float
    high: float
    cct: int
    cct_end: int
    seed: int
    curve: Callable[[float], float]

    def at(self, u: float) -> float:
        return self.low + (self.high - self.low) * u


class Program:
    """``sample`` returns the quantized target; ``next_change`` the next time to look.

    ``next_change`` returns a time strictly after ``t`` at which output may
    differ, or None when output can no longer change. ``final`` marks the
    completion sample of a finite run.
    """

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        raise NotImplementedError

    def next_change(self, t: float) -> float | None:
        return t + SAMPLE_INTERVAL


class Blink(Program):
    def __init__(self, levels: Levels, period: float, duty: float):
        self.levels, self.period, self.duty = levels, period, duty

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        phase = (t / self.period) % 1
        level = self.levels.high if phase < self.duty else self.levels.low
        return quantize_brightness(level), self.levels.cct

    def next_change(self, t: float) -> float | None:
        start = math.floor(t / self.period) * self.period
        return _first_after(
            t,
            (
                start + self.duty * self.period,
                start + self.period,
                start + self.period + self.duty * self.period,
                start + 2 * self.period,
            ),
        )


class Breathe(Program):
    def __init__(self, levels: Levels, period: float):
        self.levels, self.period = levels, period

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        phase = (t / self.period) % 1
        u = (1 - math.cos(2 * math.pi * phase)) / 2
        return quantize_brightness(self.levels.at(self.levels.curve(u))), self.levels.cct


class Fade(Program):
    def __init__(self, levels: Levels, duration: float, rising: bool):
        self.levels, self.duration, self.rising = levels, duration, rising

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        progress = min(1.0, max(0.0, t / self.duration))
        if not self.rising:
            progress = 1 - progress
        return quantize_brightness(self.levels.at(self.levels.curve(progress))), self.levels.cct

    def next_change(self, t: float) -> float | None:
        return t + SAMPLE_INTERVAL if t < self.duration else None


class CctSweep(Program):
    def __init__(self, levels: Levels, period: float):
        self.levels, self.period = levels, period

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        phase = (t / self.period) % 1
        span = self.levels.cct_end - self.levels.cct
        return (
            quantize_brightness(self.levels.high),
            quantize_cct(self.levels.cct + span * (1 - abs(2 * phase - 1))),
        )


class Pulse(Program):
    TAU = 0.12  # Heartbeat pulse decay, seconds.

    def __init__(self, levels: Levels, options: PulseOptions):
        self.levels, self.options = levels, options
        self.beat = 60 / options.bpm
        self.second = min(0.35, max(0.2, 0.33 * self.beat))

    def envelope(self, t: float) -> float:
        options = self.options
        k = math.floor(t / self.beat)
        x = max(0.0, t - k * self.beat)
        accent = options.accentEvery == 0 or k % options.accentEvery == 0
        a = 1.0 if accent else options.accentRatio
        if options.style == "beat":
            return a * math.exp(-x / (options.decay * self.beat))
        first = a * math.exp(-x / self.TAU)
        if x < self.second:
            return first
        return max(first, 0.65 * a * math.exp(-(x - self.second) / self.TAU))

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        return quantize_brightness(self.levels.at(self.envelope(t))), self.levels.cct

    def next_change(self, t: float) -> float | None:
        start = math.floor(t / self.beat) * self.beat
        onsets = [start, start + self.beat, start + 2 * self.beat]
        if self.options.style == "heartbeat":
            onsets += [start + self.second, start + self.beat + self.second]
        onset = _first_after(t, onsets)
        grid = t + SAMPLE_INTERVAL
        return grid if onset is None else min(grid, onset)


class Flicker(Program):
    def __init__(self, levels: Levels, options: FlickerOptions):
        self.levels, self.options = levels, options
        candle = options.style == "candle"
        self.h1 = max(0.2, (0.45 if candle else 0.30) / options.speed)
        mean = 8.0 if candle else 3.0
        seed = levels.seed
        self.gusts = Renewal(lambda i: exp_gap(mean, rnd(seed, 3, i), 0.5, 30.0))

    def gust(self, t: float) -> float:
        located = self.gusts.locate(t)
        strength = 0.0
        for index, start in (
            (located.index, located.start),
            (located.index - 1, located.previous),
        ):
            if index < 0 or start is None:
                continue
            length = 0.3 + 0.5 * rnd(self.levels.seed, 4, index)
            if start <= t < start + length:
                strength = max(strength, math.sin(math.pi * (t - start) / length) ** 2)
        return strength

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        levels, options = self.levels, self.options
        n = 0.6 * noise(levels.seed, 1, t, self.h1) + 0.4 * noise(levels.seed, 2, t, 0.2)
        dip = options.depth * n
        if options.gusts:
            dip += 0.35 * options.gusts * self.gust(t)
        dip = min(1.0, max(0.0, dip))
        brightness = quantize_brightness(levels.high - (levels.high - levels.low) * dip)
        warmth = options.cctCoupling * (1 - noise(levels.seed, 5, t, 1.5))
        return brightness, quantize_cct(levels.cct + (levels.cct_end - levels.cct) * warmth)


class Sunrise(Program):
    def __init__(self, levels: Levels, duration: float, rising: bool):
        self.levels, self.duration, self.rising = levels, duration, rising

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        levels = self.levels
        u = min(1.0, max(0.0, t / self.duration))
        w = u if self.rising else 1 - u
        return (
            quantize_brightness(levels.at(levels.curve(w))),
            quantize_cct(levels.cct + (levels.cct_end - levels.cct) * smooth(w)),
        )

    def next_change(self, t: float) -> float | None:
        return t + SAMPLE_INTERVAL if t < self.duration else None


class Lightning(Program):
    def __init__(self, levels: Levels, options: LightningOptions):
        self.levels, self.options = levels, options
        seed, interval = levels.seed, options.interval
        self.strikes = Renewal(lambda i: exp_gap(interval, rnd(seed, 1, i), 1.5, 4 * interval))

    def segments(self, index: int, start: float) -> list[tuple[float, float, float]]:
        """``(begin, end, level)`` of strike ``index``: flashes, darks, afterglow."""
        levels = self.levels
        count = 1 + math.floor(self.options.flashesMax * rnd(levels.seed, 2, index))
        segments = []
        begin = start
        for flash in range(count):
            key = 16 * index + flash
            end = begin + 0.1
            segments.append((begin, end, levels.at(0.6 + 0.4 * rnd(levels.seed, 3, key))))
            begin = end
            if flash < count - 1:
                begin = end + (0.1 + 0.15 * rnd(levels.seed, 4, key))
        segments.append((begin, begin + 0.1, levels.at(0.25)))
        return segments

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        located = self.strikes.locate(t)
        level = self.levels.low
        if located.start is not None:
            for begin, end, value in self.segments(located.index, located.start):
                if begin <= t < end:
                    level = value
                    break
        return quantize_brightness(level), self.levels.cct

    def next_change(self, t: float) -> float | None:
        located = self.strikes.locate(t)
        edges = [located.following]
        if located.start is not None:
            for begin, end, _ in self.segments(located.index, located.start):
                edges += [begin, end]
        return _first_after(t, edges)


class Emergency(Program):
    def __init__(self, levels: Levels, options: EmergencyOptions):
        self.levels, self.options = levels, options
        self.cycle = options.flashOn + options.flashOff
        self.half = options.flashes * self.cycle + options.gap
        self.period = 2 * self.half
        self.switch = (options.flashes - 1) * self.cycle + options.flashOn + 0.1
        edges = {self.switch}
        for flash in range(options.flashes):
            edges |= {flash * self.cycle, flash * self.cycle + options.flashOn}
        self.edges = sorted(
            {half * self.half + edge for half in (0, 1) for edge in edges} | {self.period}
        )

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        levels, options = self.levels, self.options
        u = t % self.period
        half = 1 if u >= self.half else 0
        v = u - half * self.half
        flash = math.floor(v / self.cycle)
        lit = flash < options.flashes and v - flash * self.cycle < options.flashOn
        side = (half + (1 if v >= self.switch else 0)) % 2
        return (
            quantize_brightness(levels.high if lit else levels.low),
            levels.cct if side == 0 else levels.cct_end,
        )

    def next_change(self, t: float) -> float | None:
        base = t - t % self.period
        return _first_after(
            t,
            [base + edge for edge in self.edges] + [base + self.period + self.edges[0]],
        )


class Paparazzi(Program):
    def __init__(self, levels: Levels, options: PaparazziOptions):
        self.levels, self.options = levels, options
        seed, interval = levels.seed, options.interval
        self.flashes = Renewal(lambda i: exp_gap(interval, rnd(seed, 1, i), 0.2, 6 * interval))

    def spans(self, index: int, start: float) -> list[tuple[float, float, float]]:
        level = self.levels.at(0.8 + 0.2 * rnd(self.levels.seed, 2, index))
        spans = [(start, start + 0.1, level)]
        if rnd(self.levels.seed, 3, index) < self.options.burst:
            spans.append((start + 0.2, start + 0.3, level))
        return spans

    def _active(self, t: float) -> list[tuple[float, float, float]]:
        located = self.flashes.locate(t)
        spans = []
        for index, start in (
            (located.index, located.start),
            (located.index - 1, located.previous),
        ):
            if index >= 0 and start is not None:
                spans += self.spans(index, start)
        return spans

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        level = self.levels.low
        for begin, end, value in self._active(t):
            if begin <= t < end:
                level = max(level, value)
        return quantize_brightness(level), self.levels.cct

    def next_change(self, t: float) -> float | None:
        edges = [self.flashes.locate(t).following]
        for begin, end, _ in self._active(t):
            edges += [begin, end]
        return _first_after(t, edges)


class Television(Program):
    def __init__(self, levels: Levels, options: TelevisionOptions):
        self.levels, self.options = levels, options
        seed, interval = levels.seed, options.interval
        self.cuts = Renewal(
            lambda i: exp_gap(interval, rnd(seed, 1, i), 0.4, 4 * interval), anchored=True
        )

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        levels, motion = self.levels, self.options.motion
        scene = self.cuts.locate(t).index
        level = levels.at(0.25 + 0.75 * rnd(levels.seed, 2, scene))
        if motion:
            level *= 1 + 0.15 * motion * (2 * noise(levels.seed, 4, t, 0.25) - 1)
        cct = levels.cct + (levels.cct_end - levels.cct) * rnd(levels.seed, 3, scene)
        return quantize_brightness(min(levels.high, max(levels.low, level))), quantize_cct(cct)

    def next_change(self, t: float) -> float | None:
        following = self.cuts.locate(t).following
        return min(t + SAMPLE_INTERVAL, following) if self.options.motion else following


class Morse(Program):
    def __init__(self, levels: Levels, options: MorseOptions):
        self.levels, self.loop = levels, options.loop
        spans, content = morse_spans(options.text)
        self.starts = [start * options.unit for start, _ in spans]
        self.ends = [end * options.unit for _, end in spans]
        self.edges = sorted(self.starts + self.ends)
        self.period = (content + 7) * options.unit

    def _local(self, t: float, final: bool) -> float:
        if not self.loop:
            return t
        u = t % self.period
        return self.period if final and t > 0 and u == 0 else u

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        u = self._local(t, final)
        index = bisect.bisect_right(self.starts, u) - 1
        lit = index >= 0 and u < self.ends[index]
        return quantize_brightness(self.levels.high if lit else self.levels.low), self.levels.cct

    def next_change(self, t: float) -> float | None:
        if not self.loop:
            index = bisect.bisect_right(self.edges, t)
            return self.edges[index] if index < len(self.edges) else None
        base = t - t % self.period
        return _first_after(
            t,
            [base + edge for edge in self.edges] + [base + self.period, base + 2 * self.period],
        )


class Keyframes(Program):
    def __init__(self, options: KeyframesOptions, baseline: tuple[float, int]):
        brightness, cct = baseline
        self.loop = options.loop
        self.times: list[float] = []
        self.values: list[tuple[float, int]] = []
        self.eases: list[str] = []
        for frame in options.keyframes:
            brightness = brightness if frame.brightness is None else frame.brightness
            cct = cct if frame.cct is None else frame.cct
            self.times.append(frame.at)
            self.values.append((brightness, cct))
            self.eases.append(frame.ease)
        self.period = self.times[-1]

    def _local(self, t: float, final: bool) -> float:
        if not self.loop:
            return min(t, self.period)
        u = t % self.period
        return self.period if final and t > 0 and u == 0 else u

    def value(self, u: float) -> tuple[float, float]:
        index = bisect.bisect_right(self.times, u)
        if index >= len(self.times):
            return self.values[-1]
        (b0, k0), (b1, k1) = self.values[index - 1], self.values[index]
        ease = self.eases[index]
        if ease == "hold":
            return b0, k0
        start = self.times[index - 1]
        f = EASES[ease]((u - start) / (self.times[index] - start))
        return b0 + (b1 - b0) * f, k0 + (k1 - k0) * f

    def sample(self, t: float, final: bool = False) -> tuple[int, int]:
        brightness, cct = self.value(self._local(t, final))
        return quantize_brightness(brightness), quantize_cct(cct)

    def next_change(self, t: float) -> float | None:
        if not self.loop and t >= self.period:
            return None
        u = t % self.period if self.loop else t
        index = min(bisect.bisect_right(self.times, u), len(self.times) - 1)
        boundary = t - u + self.times[index]  # Next frame; for a loop, the wrap at period.
        if boundary <= t:
            return t + SAMPLE_INTERVAL
        if self.eases[index] == "hold" or self.values[index - 1] == self.values[index]:
            return boundary
        return min(t + SAMPLE_INTERVAL, boundary)


def build(
    pattern: str,
    options: Options,
    levels: Levels,
    *,
    period: float,
    duration: float | None,
    baseline: tuple[float, int],
) -> Program:
    """Construct the pure program for a validated, baseline-resolved configuration."""
    if pattern == "blink":
        assert isinstance(options, BlinkOptions)
        return Blink(levels, period, options.duty)
    if pattern == "breathe":
        return Breathe(levels, period)
    if pattern in ("fade-in", "fade-out", "sunrise"):
        assert duration is not None
        if pattern == "sunrise":
            assert isinstance(options, SunriseOptions)
            return Sunrise(levels, duration, options.direction == "rise")
        return Fade(levels, duration, pattern == "fade-in")
    if pattern == "cct-sweep":
        return CctSweep(levels, period)
    if isinstance(options, PulseOptions):
        return Pulse(levels, options)
    if isinstance(options, FlickerOptions):
        return Flicker(levels, options)
    if isinstance(options, LightningOptions):
        return Lightning(levels, options)
    if isinstance(options, EmergencyOptions):
        return Emergency(levels, options)
    if isinstance(options, PaparazziOptions):
        return Paparazzi(levels, options)
    if isinstance(options, TelevisionOptions):
        return Television(levels, options)
    if isinstance(options, MorseOptions):
        return Morse(levels, options)
    if isinstance(options, KeyframesOptions):
        return Keyframes(options, baseline)
    raise ValueError(f"Unsupported pattern {pattern}")
