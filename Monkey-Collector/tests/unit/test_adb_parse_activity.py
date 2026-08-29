"""Device-free tests for AdbClient._parse_current_activity.

dumpsys wraps the resumed activity in ``ActivityRecord{...}``. A naive
``\\S+/\\S+`` match swallowed the trailing ``}`` (and any following token),
yielding a component name that never matched a declared activity and froze
coverage at 1/N. The parser restricts the component-name character class so
``}`` and whitespace act as boundaries.

Call the staticmethod directly: these pin the parse helper in isolation, given
raw ``dumpsys`` text. The shell-level path (``get_current_activity``, and its
``AdbError``-swallowing when ``grep`` finds no match) is covered in
``test_adb.py``.
"""

from monkey_collector.adb import AdbClient

_parse = AdbClient._parse_current_activity


def test_strips_trailing_brace():
    out = (
        "  ResumedActivity: ActivityRecord{c5601b7 u0 "
        "com.google.android.calendar/.AllInOneCalendarActivity}"
    )
    assert _parse(out) == "com.google.android.calendar/.AllInOneCalendarActivity"


def test_with_trailing_token():
    out = (
        "mResumedActivity: ActivityRecord{a2eb6c3 u0 "
        "com.test.app/.MainActivity t123}"
    )
    assert _parse(out) == "com.test.app/.MainActivity"


def test_full_class_and_inner_class():
    out = (
        "topResumedActivity: ActivityRecord{deadbee u0 "
        "com.test.app/com.test.app.Outer$Inner t9}"
    )
    assert _parse(out) == "com.test.app/com.test.app.Outer$Inner"


def test_no_match_returns_empty():
    assert _parse("ResumedActivity: null") == ""
    assert _parse("") == ""
