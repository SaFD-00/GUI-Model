"""Screen stabilization by pixel comparison.

No device is required: a fake AdbClient replays a scripted sequence of PNG
frames, so the tests pin the DECISION logic rather than the hardware.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from monkey_collector.stabilize import (
    DEFAULT_LUMA_DELTA,
    DEFAULT_PIXEL_THRESHOLD,
    Stabilized,
    changed_fraction,
    wait_for_stable_screen,
)

W, H = 120, 260


def png(fill: int = 255, patch: tuple[int, int, int, int] | None = None, shade: int = 0) -> bytes:
    """A grayscale PNG, optionally with a rectangle painted at *shade*."""
    image = Image.new("L", (W, H), fill)
    if patch is not None:
        image.paste(shade, patch)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


BLANK = png()
#: ~1.6% of the frame — above DEFAULT_PIXEL_THRESHOLD, i.e. a real change.
BIG_CHANGE = png(patch=(0, 0, W, 5))
#: A single pixel, ~0.003% — the blinking-cursor shape, below the threshold.
CURSOR = png(patch=(10, 10, 11, 11))


class FakeAdb:
    """Replays *frames*; the last frame repeats once the script runs out."""

    def __init__(self, frames: list[bytes]) -> None:
        self.frames = frames
        self.calls = 0

    def screencap(self, *, timeout: float | None = None) -> bytes:
        frame = self.frames[min(self.calls, len(self.frames) - 1)]
        self.calls += 1
        return frame


# ---------------------------------------------------------------------------
# changed_fraction
# ---------------------------------------------------------------------------


def thumb(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("L")


def test_identical_frames_have_zero_change():
    assert changed_fraction(thumb(BLANK), thumb(BLANK), DEFAULT_LUMA_DELTA) == 0.0


def test_a_one_pixel_change_lands_below_the_settle_threshold():
    """The blinking-cursor shape. If this ever exceeds the threshold, every
    screen with a caret would burn the full max_wait on every step."""
    fraction = changed_fraction(thumb(BLANK), thumb(CURSOR), DEFAULT_LUMA_DELTA)
    assert 0.0 < fraction <= DEFAULT_PIXEL_THRESHOLD


def test_a_banner_sized_change_lands_above_the_settle_threshold():
    fraction = changed_fraction(thumb(BLANK), thumb(BIG_CHANGE), DEFAULT_LUMA_DELTA)
    assert fraction > DEFAULT_PIXEL_THRESHOLD


def test_a_change_below_the_luma_delta_does_not_count():
    """Resampling wobble and subpixel AA must not read as motion."""
    almost = png(fill=255 - DEFAULT_LUMA_DELTA, patch=None)
    assert changed_fraction(thumb(BLANK), thumb(almost), DEFAULT_LUMA_DELTA) == 0.0


def test_differently_sized_frames_are_treated_as_fully_changed():
    other = Image.new("L", (W // 2, H), 255)
    assert changed_fraction(thumb(BLANK), other, DEFAULT_LUMA_DELTA) == 1.0


# ---------------------------------------------------------------------------
# wait_for_stable_screen
# ---------------------------------------------------------------------------


def test_an_already_static_screen_settles_in_two_polls():
    """Two is the floor: judging a screen settled needs two frames to compare."""
    adb = FakeAdb([BLANK])
    result = wait_for_stable_screen(adb)
    assert result.settled
    assert result.polls == 2
    assert result.last_change == 0.0
    assert adb.calls == 2


def test_a_moving_screen_settles_once_the_picture_stops():
    adb = FakeAdb([BLANK, BIG_CHANGE, CURSOR, CURSOR])
    result = wait_for_stable_screen(adb)
    assert result.settled
    assert result.polls == 4
    assert result.png == CURSOR, "the settled frame is the observation screenshot"


def test_a_never_settling_screen_times_out_instead_of_stalling(caplog):
    """A video / spinner / ticking clock must not hold the run hostage.

    This is the failure mode the threshold and the backstop exist for, and it is
    silent — the run keeps producing data while every step pays the full wait.
    """
    adb = FakeAdb([BLANK, BIG_CHANGE] * 40)
    result = wait_for_stable_screen(adb, max_wait_sec=0.35, poll_ms=50)
    assert not result.settled
    assert result.last_change is not None and result.last_change > DEFAULT_PIXEL_THRESHOLD
    assert result.waited_sec >= 0.35
    assert result.png, "the last frame is still returned so the caller can continue"


def test_a_screen_that_settles_on_the_deadline_poll_counts_as_settled():
    """The deadline is checked AFTER the comparison, never before it.

    Otherwise a screen that stops moving on the very poll that crosses the
    deadline is misreported as a timeout, and the caller degrades a perfectly
    good observation.
    """
    adb = FakeAdb([BLANK, BIG_CHANGE, BIG_CHANGE])
    result = wait_for_stable_screen(adb, max_wait_sec=0.01, poll_ms=0)
    assert result.settled
    assert result.polls == 3


def test_stabilized_is_truthy_only_when_settled():
    assert bool(Stabilized(b"x", True, 2, 0.1, 0.0))
    assert not bool(Stabilized(b"x", False, 9, 8.0, 0.4))


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"max_wait_sec": 0}, "max_wait_sec"),
        ({"max_wait_sec": -1.0}, "max_wait_sec"),
        ({"pixel_threshold": -0.1}, "pixel_threshold"),
        ({"pixel_threshold": 1.5}, "pixel_threshold"),
    ],
)
def test_invalid_arguments_raise(kwargs, match):
    with pytest.raises(ValueError, match=match):
        wait_for_stable_screen(FakeAdb([BLANK]), **kwargs)


def test_the_caller_never_needs_a_second_screencap():
    """Pin the cost contract: the settled frame IS the screenshot.

    Re-capturing after stabilization would cost another ~0.68 s per step AND
    could catch a different frame than the one judged settled.
    """
    adb = FakeAdb([BLANK, BIG_CHANGE, BIG_CHANGE])
    result = wait_for_stable_screen(adb)
    assert result.png == adb.frames[min(adb.calls - 1, len(adb.frames) - 1)]
