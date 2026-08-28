"""Atlas-Collector — host-pull Android GUI data collector.

A single host-side Python process drives the device over ADB and pulls every
observation itself::

    uiautomator dump      -> before_xml
    exec-out screencap -p -> before_screenshot
    input tap/swipe/text  -> action
    (stabilize, then pull again)
    uiautomator dump      -> after_xml
    exec-out screencap -p -> after_screenshot

There is no Android app, no AccessibilityService and no TCP server: nothing on
the device pushes anything back, so there is no push signal to time out on.

Milestone status (see ARCHITECTURE.md):
  M1 scaffold + catalog + config + docs  — DONE
  M2 xml/ encoding + coordinate frame    — DONE (implemented, tested)
  M3 coverage-guided exploration         — NOT IMPLEMENTED
  M4 collection loop                     — NOT IMPLEMENTED
  M5 Stage-1 export                      — NOT IMPLEMENTED

M2 is a library, not a CLI surface: ``atlas-collect catalog`` is still the only
subcommand that runs.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
