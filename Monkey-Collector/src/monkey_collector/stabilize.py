"""Screen stabilization by screenshot pixel comparison.

WHY NOT POLL ``uiautomator dump``
=================================

Host-pull has no "the screen changed" signal from the device, so the host must
decide for itself when a screen has settled. The obvious way — dump the view
hierarchy repeatedly until two consecutive dumps agree — is the expensive way.
Measured on the target Pixel 6 (oriole, Android 16 / SDK 36), median of 6 runs:

    adb exec-out uiautomator dump /dev/tty   2.341 s
    adb exec-out screencap -p                0.680 s   (1.7 MB PNG)
    adb exec-out screencap    (raw RGBA)     0.803 s   (10.4 MB — the transfer
                                                        costs more than the
                                                        on-device PNG encode)

A screenshot poll is **3.44x cheaper** than a hierarchy poll, and the host-side
work to compare two frames is noise: decode + grayscale + downscale to 100 px
wide measures 22 ms, i.e. 3% of the transfer it rides on.

So: poll SCREENSHOTS until the picture stops moving, then take exactly ONE
hierarchy dump. For a stabilization needing ``k`` polls:

    dump-polling   k x 2.341
    this module    k x 0.680 + 2.341      (the final poll IS the observation
                                           screenshot, so it is not extra)

which breaks even at k = 1.41 — and k >= 2 always, because deciding that a
screen has settled requires at least two frames to compare. Real savings:
k=2 21%, k=3 38%, k=4 46%.

WHY A THRESHOLD RATHER THAN EQUALITY
====================================

Measured on real transitions on the device, the changed-pixel fraction
converges to exactly ``0.0000`` (an app launch, a tap, and a Back all settled
within 2-3 frames). It would be tempting to require frame equality.

Do not. Some screens never become pixel-identical: a blinking text cursor, a
video or ad, an indeterminate progress spinner, a clock ticking seconds. Under
strict equality every step on such a screen burns the whole ``max_wait``
budget — the single most expensive failure mode this module can have, and a
silent one, since the run still produces data. Hence
:data:`DEFAULT_PIXEL_THRESHOLD`: a screen counts as settled once the changed
fraction falls to a small residue rather than to nothing.

The backstop is the second half of the same defence: when the deadline passes
the LAST frame is accepted and returned with ``settled=False``. Never-settling
screens are real, they are legitimately part of the corpus, and the caller
decides what to do about them — this module does not get to stall the run.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

    from monkey_collector.adb import AdbClient

#: Width the comparison thumbnail is scaled to. 100 px keeps a 1080x2400 frame
#: at ~100x222 = 22k samples: enough to see any real UI change, small enough
#: that the comparison disappears next to the transfer.
DEFAULT_LOW_RES_WIDTH = 100

#: Per-pixel intensity delta (0-255) that counts as "this pixel changed".
#: Absorbs JPEG-ish resampling wobble and subpixel AA without hiding real edits.
DEFAULT_LUMA_DELTA = 10

#: Fraction of changed pixels at or below which a screen counts as settled.
#: 0.005 of a 100x222 thumbnail is ~111 samples — a blinking cursor or a
#: ticking clock digit, but not a dialog, a list scroll, or a page transition.
DEFAULT_PIXEL_THRESHOLD = 0.005

#: Extra sleep between polls. Zero by default: ``screencap`` itself takes
#: ~0.68 s on the target device, so consecutive frames are already sampled
#: ~680 ms apart and an added sleep buys nothing but latency.
DEFAULT_POLL_MS = 0

#: Give up waiting after this long and accept the last frame (``settled=False``).
DEFAULT_MAX_WAIT_SEC = 8.0


@dataclass(frozen=True)
class Stabilized:
    """The screenshot a settled (or timed-out) screen was captured at.

    ``png`` is the observation screenshot itself — the final poll doubles as the
    capture, so the caller must NOT take another ``screencap``: it would cost
    another 0.68 s and could catch a different frame than the one that was
    judged settled.
    """

    png: bytes
    settled: bool
    polls: int
    waited_sec: float
    #: Changed-pixel fraction of the last comparison; None if only one frame
    #: was ever captured (deadline already passed).
    last_change: float | None

    def __bool__(self) -> bool:
        return self.settled


def _thumbnail(png: bytes, width: int) -> PILImage:
    """Decode *png* to a small grayscale image for comparison."""
    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("L")
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height))


def changed_fraction(before: PILImage, after: PILImage, luma_delta: int) -> float:
    """Fraction of samples differing by more than *luma_delta*.

    Deliberately a COUNT of changed pixels, not a mean difference. A mean is
    dominated by how much the changed pixels changed, so a small dialog over a
    dark background scores lower than a uniform brightness shift that alters
    nothing structurally. What matters here is how much of the screen moved.

    Counted from the difference image's HISTOGRAM rather than by iterating
    pixels: the histogram is computed in C and is 256 buckets wide regardless of
    image size, so this stays flat as ``low_res_width`` grows.
    """
    from PIL import ImageChops

    if before.size != after.size:
        return 1.0
    total = before.size[0] * before.size[1]
    if not total:
        return 0.0
    histogram = ImageChops.difference(before, after).histogram()
    # Buckets are intensity deltas 0..255; "changed" is strictly above the delta.
    return sum(histogram[min(luma_delta + 1, len(histogram)) :]) / total


def wait_for_stable_screen(
    adb: AdbClient,
    *,
    max_wait_sec: float = DEFAULT_MAX_WAIT_SEC,
    poll_ms: int = DEFAULT_POLL_MS,
    pixel_threshold: float = DEFAULT_PIXEL_THRESHOLD,
    luma_delta: int = DEFAULT_LUMA_DELTA,
    low_res_width: int = DEFAULT_LOW_RES_WIDTH,
) -> Stabilized:
    """Poll screenshots until the screen stops moving; return the settled frame.

    Takes at least two screenshots (a settle decision needs two frames to
    compare) and at most as many as fit in *max_wait_sec*. The returned
    :attr:`Stabilized.png` is the frame the decision was made on and is meant to
    be used directly as the observation screenshot.

    Callers should take their ``uiautomator dump`` AFTER this returns, exactly
    once. Dumping before or during defeats the entire point of the module.

    A timeout is not an error: ``settled=False`` is returned with the last frame
    so the run continues. See the module docstring on why never-settling screens
    are expected rather than exceptional.
    """
    if max_wait_sec <= 0:
        raise ValueError(f"max_wait_sec must be positive, got {max_wait_sec!r}")
    if not 0.0 <= pixel_threshold <= 1.0:
        raise ValueError(f"pixel_threshold must be in [0.0, 1.0], got {pixel_threshold!r}")

    started = time.monotonic()
    deadline = started + max_wait_sec

    png = adb.screencap()
    previous = _thumbnail(png, low_res_width)
    polls = 1
    last_change: float | None = None

    while True:
        if poll_ms > 0:
            time.sleep(poll_ms / 1000.0)

        png = adb.screencap()
        polls += 1
        current = _thumbnail(png, low_res_width)
        last_change = changed_fraction(previous, current, luma_delta)
        previous = current

        if last_change <= pixel_threshold:
            return Stabilized(
                png=png,
                settled=True,
                polls=polls,
                waited_sec=time.monotonic() - started,
                last_change=last_change,
            )

        # Checked AFTER the comparison so the deadline never discards a frame
        # that would have settled: a screen that stops moving on the very poll
        # that crosses the deadline is settled, not a timeout.
        if time.monotonic() >= deadline:
            waited = time.monotonic() - started
            logger.warning(
                "screen did not settle within {:.1f}s ({} polls, last change {:.4f} > {:.4f}) "
                "— accepting the last frame; an animation, video or blinking cursor is likely",
                waited,
                polls,
                last_change,
                pixel_threshold,
            )
            return Stabilized(
                png=png,
                settled=False,
                polls=polls,
                waited_sec=waited,
                last_change=last_change,
            )


__all__ = [
    "DEFAULT_LOW_RES_WIDTH",
    "DEFAULT_LUMA_DELTA",
    "DEFAULT_MAX_WAIT_SEC",
    "DEFAULT_PIXEL_THRESHOLD",
    "DEFAULT_POLL_MS",
    "Stabilized",
    "changed_fraction",
    "wait_for_stable_screen",
]
