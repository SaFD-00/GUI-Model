"""The review server: its API, and the one promise it cannot break.

A sweep is normally still collecting while someone reviews the apps that already
finished. If this server wrote so much as a mtime under ``raw/`` or ``runtime/``
it would be racing the collector for its own corpus, so
``test_a_full_session_leaves_the_corpus_byte_identical`` walks the whole API and
then compares a checksum of every file in both trees.

Everything else here is the API contract the page depends on, plus the two
inputs that reach the filesystem from a URL -- the package name and the static
asset path.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from monkey_collector.review.server import Handler, ReviewState
from monkey_collector.review.store import DecisionStore
from tests.unit.test_export import dump, tap, triple, write_session

PACKAGE = "com.example.app"


def fingerprint(root: Path) -> dict[str, tuple[int, str]]:
    """Every file under *root*, by size and content hash."""
    out: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            out[str(path.relative_to(root))] = (len(data), hashlib.sha1(data).hexdigest())
    return out


@pytest.fixture()
def server(tmp_path):
    same = {index: dump(label="one") for index in range(4)}
    write_session(
        tmp_path / "raw",
        PACKAGE,
        [triple(0, tap(100, 200)), triple(1, tap(100, 200)), triple(2, tap(700, 900))],
        dumps=same,
    )
    (tmp_path / "runtime").mkdir()
    state = ReviewState(
        tmp_path / "raw",
        tmp_path / "review",
        device_size=(1080, 2400),
        reviewer="tester",
    )
    handler = type("BoundHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path, state
    httpd.shutdown()
    httpd.server_close()


def get(base: str, path: str):
    with urlopen(f"{base}{path}", timeout=10) as response:  # noqa: S310 - local test server
        body = response.read()
    return json.loads(body) if path.startswith("/api") else body


def post(base: str, path: str, payload: dict):
    request = Request(  # noqa: S310 - local test server
        f"{base}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read())


# ---------------------------------------------------------------------------
# read-only
# ---------------------------------------------------------------------------


def test_a_full_session_leaves_the_corpus_byte_identical(server):
    base, tmp_path, _ = server
    before_raw = fingerprint(tmp_path / "raw")
    before_runtime = fingerprint(tmp_path / "runtime")

    get(base, "/api/meta")
    get(base, "/api/apps")
    get(base, f"/api/app/{PACKAGE}")
    get(base, f"/api/app/{PACKAGE}/step/0")
    get(base, f"/img/{PACKAGE}/0?w=240")
    post(base, "/api/rules/preview", {"package": PACKAGE, "rule": "duplicate"})
    post(base, "/api/rules/apply", {"package": PACKAGE, "rule": "duplicate"})
    post(base, "/api/decide",
         {"package": PACKAGE, "step": 2, "verdict": "keep", "reason": ""})

    assert fingerprint(tmp_path / "raw") == before_raw
    assert fingerprint(tmp_path / "runtime") == before_runtime
    # ... and the verdicts did land, so this is not passing by doing nothing.
    assert (tmp_path / "review" / "by-tester.jsonl").is_file()


# ---------------------------------------------------------------------------
# api
# ---------------------------------------------------------------------------


def test_apps_reports_progress_against_the_exportable_count(server):
    base, _, _ = server

    payload = get(base, "/api/apps")
    app = payload["apps"][0]

    assert app["package"] == PACKAGE
    assert app["exportable"] == 3
    assert app["distinct"] == 2
    assert app["redundant"] == 1
    assert app["reviewed"] == 0


def test_collapsing_shows_one_card_per_duplicate_group(server):
    base, _, _ = server

    collapsed = get(base, f"/api/app/{PACKAGE}?collapse=1")
    expanded = get(base, f"/api/app/{PACKAGE}?collapse=0")

    assert len(collapsed["rows"]) == 2
    assert len(expanded["rows"]) == 3
    first = collapsed["rows"][0]
    assert first["group_size"] == 2
    assert first["members"] == [0, 1]


def test_a_step_detail_carries_both_encodings_and_the_verdict_key(server):
    base, _, _ = server

    detail = get(base, f"/api/app/{PACKAGE}/step/0")

    assert detail["before_screen"]["html"].startswith("<")
    assert "<node" in detail["before_screen"]["raw"]
    assert detail["before_screen"]["size"] == [1080, 2400]
    assert detail["key"]
    assert detail["status"] == "unreviewed"


def test_a_decision_is_visible_on_the_next_read(server):
    base, _, _ = server

    post(base, "/api/decide",
         {"package": PACKAGE, "step": 0, "verdict": "exclude", "reason": "no_effect"})
    detail = get(base, f"/api/app/{PACKAGE}/step/0")
    counts = get(base, f"/api/app/{PACKAGE}")["counts"]

    assert detail["status"] == "exclude"
    assert detail["verdict"]["reason"] == "no_effect"
    assert counts["exclude"] == 1


def test_a_rule_never_overwrites_a_human_decision(server):
    base, _, _ = server

    post(base, "/api/decide", {"package": PACKAGE, "step": 1, "verdict": "keep"})
    result = post(base, "/api/rules/apply", {"package": PACKAGE, "rule": "duplicate"})

    assert result["applied"] == 0
    assert result["skipped"] == 1


def test_a_rule_can_be_taken_back_by_id(server):
    base, _, _ = server

    post(base, "/api/rules/apply", {"package": PACKAGE, "rule": "duplicate"})
    assert get(base, f"/api/app/{PACKAGE}")["counts"]["exclude"] == 1

    post(base, "/api/rules/undo", {"package": PACKAGE, "rule": "duplicate"})
    assert get(base, f"/api/app/{PACKAGE}")["counts"]["exclude"] == 0


# ---------------------------------------------------------------------------
# untrusted input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/app/..%2f..%2fetc",
        "/img/..%2f..%2fetc/0",
        "/api/app/com.not.collected",
    ],
)
def test_a_package_outside_the_corpus_is_refused(server, path):
    base, _, _ = server

    with pytest.raises(HTTPError) as caught:
        get(base, path)

    assert caught.value.code == 404


def test_the_static_route_cannot_escape_its_directory(server):
    base, _, _ = server

    with pytest.raises(HTTPError) as caught:
        get(base, "/static/../../../etc/passwd")

    assert caught.value.code in (400, 404)


def test_the_page_and_its_assets_are_served(server):
    base, _, _ = server

    assert b"monkey" in get(base, "/")
    assert b"--accent" in get(base, "/static/app.css")
    assert b"markLayer" in get(base, "/static/app.js")


def test_another_reviewers_verdicts_appear_without_a_restart(server):
    # Apps are split between reviewers, so overlap is rare by design -- but a
    # shared review/ (one box, or a pulled branch) would otherwise leave one
    # reviewer's progress numbers frozen at the moment they opened the page.
    base, tmp_path, _ = server
    assert get(base, f"/api/app/{PACKAGE}")["counts"]["exclude"] == 0

    other = DecisionStore.load(tmp_path / "review")
    other.record(
        [{"package": PACKAGE, "step": 2, "verdict": "exclude", "reason": "broken"}],
        reviewer="kim",
    )

    assert get(base, f"/api/app/{PACKAGE}")["counts"]["exclude"] == 1
