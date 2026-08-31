"""The review server: a local, single-user web UI over a read-only corpus.

WHY A SERVER AND NOT A STATIC DUMP
==================================

The corpus is 1.8GB of PNGs across ten apps and still growing. Baking it into a
page means copying it; serving it means the page shows the bytes that are on
disk right now, which is also what makes it safe to open while a collection is
running -- there is no second copy to fall out of step.

Stdlib ``http.server`` and one HTML page, because this repo has no JS toolchain
and adding one to filter a dataset would be a second thing to maintain forever.

WHAT IT MAY WRITE
=================

``review/`` and nothing else. Every read of ``raw/`` goes through
:class:`~monkey_collector.review.corpus.AppCorpus`, which opens files and never
creates, moves or deletes one. A collection running in another terminal must not
be able to tell that this is open.

BOUND TO LOOPBACK
=================

``127.0.0.1`` by default and there is no authentication, because there is
nothing to authenticate against: the reviewer's name is a label on their
verdicts, not a login. The corpus is screenshots of a REAL, logged-in device
(AGENTS §0.5) -- personal calendars, contacts, files. Serving that on ``0.0.0.0``
would publish it to the network, so ``--host`` exists but says so.
"""

from __future__ import annotations

import io
import json
import mimetypes
import re
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from loguru import logger

from monkey_collector.review import corpus as corpus_mod
from monkey_collector.review import rules as rules_mod
from monkey_collector.review.corpus import AppCorpus, discover
from monkey_collector.review.store import REASONS, DecisionStore

STATIC_DIR = Path(__file__).parent / "static"

#: Thumbnail widths the UI asks for. Fixed set so a crafted query cannot make
#: the server render (and cache) an unbounded number of sizes.
THUMB_WIDTHS = (160, 240, 360, 540)

_PACKAGE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class ReviewState:
    """Everything the handlers share: the corpus readers and the verdict store.

    One lock around the store because ``ThreadingHTTPServer`` serves the page's
    concurrent image requests in parallel, and a bulk apply is a read-modify-append
    that must not interleave with another one.
    """

    def __init__(
        self,
        raw_root: Path,
        review_dir: Path,
        *,
        device_size: tuple[int, int],
        reviewer: str,
    ) -> None:
        self.raw_root = raw_root
        self.review_dir = review_dir
        self.device_size = device_size
        self.reviewer = reviewer
        self.cache_dir = review_dir / "cache"
        self.store = DecisionStore.load(review_dir)
        self._store_stamp = self._verdict_stamp()
        self.lock = threading.Lock()
        #: Bounded: each corpus holds a small screen memo, and a sweep can have
        #: 50 apps. Two is enough for "review one app, glance at another".
        self._apps: OrderedDict[str, AppCorpus] = OrderedDict()

    def _verdict_stamp(self) -> tuple[tuple[str, int, int], ...]:
        """Name, size and mtime of every verdict file, cheap enough per request."""
        if not self.review_dir.is_dir():
            return ()
        stamp = []
        for path in sorted(self.review_dir.glob("by-*.jsonl")):
            try:
                stat = path.stat()
            except OSError:
                continue
            stamp.append((path.name, stat.st_size, stat.st_mtime_ns))
        return tuple(stamp)

    def note_written(self) -> None:
        """Record that THIS process wrote the verdicts, so the next request's
        staleness check does not read its own write as someone else's."""
        self._store_stamp = self._verdict_stamp()

    def refresh_store(self) -> None:
        """Re-fold the verdicts when another reviewer's file changed.

        Apps are split between reviewers, so the overlap is small by design --
        but a shared ``review/`` (one box, or a pulled branch) would otherwise
        show one reviewer progress numbers frozen at the moment they started.
        The export-time merge was always correct; this is the display catching up.
        """
        stamp = self._verdict_stamp()
        if stamp == self._store_stamp:
            return
        with self.lock:
            self.store.reload()
            self._store_stamp = stamp

    def packages(self) -> list[str]:
        return discover(self.raw_root)

    def corpus(self, package: str) -> AppCorpus | None:
        if not _PACKAGE_RE.match(package or "") or package not in self.packages():
            return None
        found = self._apps.get(package)
        if found is not None:
            self._apps.move_to_end(package)
            return found
        while len(self._apps) >= 3:
            self._apps.popitem(last=False)
        made = AppCorpus(
            self.raw_root,
            package,
            device_size=self.device_size,
            cache_dir=self.cache_dir,
        )
        self._apps[package] = made
        return made


def _status_of(row: Any, verdict: Any) -> str:
    if verdict is None:
        return "unreviewed"
    return str(verdict.verdict)


class Handler(BaseHTTPRequestHandler):
    server_version = "MonkeyReview/1.0"
    state: ReviewState  # injected by serve()

    # -- plumbing -----------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug("{} {}", self.address_string(), format % args)

    def _send(self, code: int, body: bytes, content_type: str, *, cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    # -- routing ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path == "/" or path == "/index.html":
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/meta":
                return self._api_meta()
            if path.startswith("/api/"):
                self.state.refresh_store()
            if path == "/api/apps":
                return self._api_apps()
            if path.startswith("/api/app/"):
                return self._api_app(path[len("/api/app/"):], query)
            if path.startswith("/img/"):
                return self._img(path[len("/img/"):], query)
        except BrokenPipeError:  # the page navigated away mid-image
            return
        except Exception as error:  # noqa: BLE001 - one bad request must not kill the server
            logger.exception("GET {} failed", path)
            return self._error(500, f"{type(error).__name__}: {error}")
        self._error(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/decide":
                return self._api_decide()
            if path == "/api/rules/preview":
                return self._api_rule("preview")
            if path == "/api/rules/apply":
                return self._api_rule("apply")
            if path == "/api/rules/undo":
                return self._api_rule("undo")
        except Exception as error:  # noqa: BLE001
            logger.exception("POST {} failed", path)
            return self._error(500, f"{type(error).__name__}: {error}")
        self._error(404, "not found")

    # -- static -------------------------------------------------------------

    def _static(self, name: str) -> None:
        # Resolved and re-checked against the static root: the page's own asset
        # names are fixed, so anything that escapes the directory is hostile.
        target = (STATIC_DIR / name).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            return self._error(404, "not found")
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        charset = "; charset=utf-8" if kind.startswith("text/") or kind.endswith("javascript") else ""
        self._send(200, target.read_bytes(), f"{kind}{charset}")

    # -- api ----------------------------------------------------------------

    def _api_meta(self) -> None:
        state = self.state
        self._json(
            {
                "reviewer": state.reviewer,
                "root": str(state.raw_root.parent),
                "raw_root": str(state.raw_root),
                "review_dir": str(state.review_dir),
                "reasons": REASONS,
                "rules": rules_mod.catalog(),
                "drops": {
                    corpus_mod.DROP_UNCHANGED: "화면이 바뀌지 않음",
                    corpus_mod.DROP_UNKNOWN_ACTION: "EXP08 에 이름이 없는 액션",
                    corpus_mod.DROP_MISSING_FILES: "observation 파일 결손",
                    corpus_mod.DROP_FOREIGN: "다른 앱 화면",
                    corpus_mod.DROP_UNPARSABLE: "덤프 인코딩 실패",
                    corpus_mod.DROP_DUPLICATE_STEP: "step 번호 중복",
                },
            }
        )

    def _api_apps(self) -> None:
        state = self.state
        out = []
        for package in state.packages():
            app = state.corpus(package)
            if app is None:
                continue
            summary = app.summary()
            folded = state.store.for_package(package)
            exportable = {row.step for row in app.rows() if row.exportable}
            reviewed = sum(1 for step in folded if step in exportable)
            excluded = sum(
                1 for step, verdict in folded.items()
                if verdict.is_exclude and step in exportable
            )
            payload = summary.as_dict()
            payload.update(
                {"reviewed": reviewed, "excluded": excluded, "kept": reviewed - excluded}
            )
            out.append(payload)
        self._json({"apps": out, "reviewer": state.reviewer})

    def _api_app(self, tail: str, query: dict[str, list[str]]) -> None:
        parts = [p for p in tail.split("/") if p]
        if not parts:
            return self._error(404, "not found")
        package = parts[0]
        app = self.state.corpus(package)
        if app is None:
            return self._error(404, f"unknown package {package!r}")
        if len(parts) >= 3 and parts[1] == "step":
            return self._api_step(app, parts[2])
        return self._api_steps(app, query)

    def _api_steps(self, app: AppCorpus, query: dict[str, list[str]]) -> None:
        def first(name: str, default: str = "") -> str:
            values = query.get(name) or []
            return values[0] if values else default

        scope = first("scope", "export")
        status = first("status", "")
        collapse = first("collapse", "1") == "1"
        try:
            offset = max(0, int(first("offset", "0")))
            limit = min(500, max(1, int(first("limit", "120"))))
        except ValueError:
            offset, limit = 0, 120

        rows = app.rows()
        folded = self.state.store.for_package(app.package)
        if scope == "export":
            rows = [row for row in rows if row.exportable]
        selected: list[dict[str, Any]] = []
        seen_groups: set[str] = set()
        for row in rows:
            verdict = folded.get(row.step)
            state = _status_of(row, verdict)
            if status and state != status:
                continue
            payload = row.as_dict()
            payload["status"] = state
            payload["verdict"] = verdict.as_dict() if verdict else None
            if collapse and row.group:
                if row.group in seen_groups:
                    continue
                seen_groups.add(row.group)
                members = [
                    other.step
                    for other in rows
                    if other.group == row.group
                ]
                payload["members"] = members
                payload["members_excluded"] = sum(
                    1 for step in members
                    if (v := folded.get(step)) is not None and v.is_exclude
                )
            selected.append(payload)
        total = len(selected)
        summary = app.summary().as_dict()
        self._json(
            {
                "package": app.package,
                "summary": summary,
                "total": total,
                "offset": offset,
                "rows": selected[offset: offset + limit],
                "counts": {
                    "unreviewed": sum(1 for r in rows if r.step not in folded),
                    "keep": sum(
                        1 for r in rows
                        if (v := folded.get(r.step)) is not None and v.verdict == "keep"
                    ),
                    "exclude": sum(
                        1 for r in rows
                        if (v := folded.get(r.step)) is not None and v.is_exclude
                    ),
                },
            }
        )

    def _api_step(self, app: AppCorpus, raw_step: str) -> None:
        try:
            step = int(raw_step)
        except ValueError:
            return self._error(400, "step must be an integer")
        rows = app.rows()
        row = next((r for r in rows if r.step == step), None)
        if row is None:
            return self._error(404, f"no step {step} in {app.package}")
        before = app.screen(row.before)
        after = app.screen(row.after)
        verdict = self.state.store.get(app.package, step)
        payload = row.as_dict()
        # The other steps that collapse to this same exported record. The list
        # can be reviewed group-at-a-time, so the detail view has to know who
        # else a decision would speak for -- otherwise excluding the card a
        # group was folded into leaves its 368 twins unreviewed and shipped.
        members = (
            [other.step for other in rows if other.group == row.group] if row.group else [step]
        )
        payload.update(
            {
                "members": members,
                "status": _status_of(row, verdict),
                "verdict": verdict.as_dict() if verdict else None,
                "before_screen": {
                    "index": before.index,
                    "html": before.html,
                    "raw": before.raw,
                    "size": list(before.size),
                    "measured": before.measured,
                    "package": before.package,
                    "error": before.error,
                    "has_shot": before.has_shot,
                },
                "after_screen": {
                    "index": after.index,
                    "html": after.html,
                    "raw": after.raw,
                    "size": list(after.size),
                    "measured": after.measured,
                    "package": after.package,
                    "error": after.error,
                    "has_shot": after.has_shot,
                },
            }
        )
        self._json(payload)

    def _api_decide(self) -> None:
        body = self._body()
        rows = body.get("rows")
        if isinstance(body.get("step"), int):
            rows = [body]
        if not isinstance(rows, list) or not rows:
            return self._error(400, "expected a row or a non-empty rows array")
        reviewer = str(body.get("reviewer") or self.state.reviewer)
        with self.state.lock:
            written = self.state.store.record(rows, reviewer=reviewer)
            self.state.note_written()
        self._json({"written": written})

    def _api_rule(self, mode: str) -> None:
        body = self._body()
        package = str(body.get("package") or "")
        app = self.state.corpus(package)
        if app is None:
            return self._error(404, f"unknown package {package!r}")
        rule_id = str(body.get("rule") or "")
        options = body.get("options") if isinstance(body.get("options"), dict) else {}
        reviewer = str(body.get("reviewer") or self.state.reviewer)

        if mode == "preview":
            return self._json(rules_mod.preview(app, rule_id, options))
        if mode == "undo":
            with self.state.lock:
                cleared = self.state.store.undo_rule(package, rule_id, reviewer=reviewer)
            return self._json({"cleared": cleared})

        rule = rules_mod.RULES_BY_ID.get(rule_id)
        if rule is None:
            return self._error(400, f"unknown rule {rule_id!r}")
        rows = rules_mod.rows_for(app, rule_id, options)
        # A row a human already judged is left alone: a bulk rule may fill in
        # blanks, never overwrite a person's call.
        folded = self.state.store.for_package(package)
        fresh = [row for row in rows if row.step not in folded]
        verdicts = rules_mod.verdict_rows(package, fresh, rule=rule_id, reason=rule.reason)
        with self.state.lock:
            written = self.state.store.record(verdicts, reviewer=reviewer)
        self._json({"applied": written, "skipped": len(rows) - len(fresh)})

    # -- images -------------------------------------------------------------

    def _img(self, tail: str, query: dict[str, list[str]]) -> None:
        parts = [p for p in tail.split("/") if p]
        if len(parts) != 2:
            return self._error(404, "not found")
        package, raw_index = parts[0], parts[1].split(".")[0]
        app = self.state.corpus(package)
        if app is None:
            return self._error(404, "not found")
        try:
            index = int(raw_index)
        except ValueError:
            return self._error(400, "observation must be an integer")
        source = app.observation_dir(index) / "screenshot.png"
        if not source.is_file():
            return self._error(404, "no screenshot")
        widths = query.get("w") or []
        try:
            want = int(widths[0]) if widths else 0
        except ValueError:
            want = 0
        if want in THUMB_WIDTHS:
            body = self._thumb(package, index, source, want)
            if body is not None:
                # Immutable: an observation's PNG is written once and never
                # rewritten, so the browser may keep the thumbnail for the
                # session. That is what makes a 500-tile grid usable.
                return self._send(200, body, "image/jpeg", cache="max-age=86400")
        self._send(200, source.read_bytes(), "image/png", cache="max-age=86400")

    def _thumb(self, package: str, index: int, source: Path, width: int) -> bytes | None:
        cache = self.state.cache_dir / "thumbs" / package / f"{index:04d}_{width}.jpg"
        try:
            if cache.is_file() and cache.stat().st_mtime >= source.stat().st_mtime:
                return cache.read_bytes()
        except OSError:
            pass
        try:
            from PIL import Image

            with Image.open(source) as opened:
                frame = opened.convert("RGB")
            ratio = width / float(frame.width)
            frame = frame.resize(
                (width, max(1, int(frame.height * ratio))), Image.Resampling.LANCZOS
            )
            buffer = io.BytesIO()
            frame.save(buffer, format="JPEG", quality=72)
            body = buffer.getvalue()
        except Exception as error:  # noqa: BLE001 - fall back to the full PNG
            logger.debug("thumbnail failed for {} {} ({})", package, index, error)
            return None
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(body)
        except OSError:
            logger.debug("could not cache thumbnail {}", cache)
        return body


def serve(
    raw_root: str | Path,
    review_dir: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8700,
    device_size: tuple[int, int] = (1080, 2400),
    reviewer: str = "anon",
    open_browser: bool = True,
) -> int:
    """Run the review UI until interrupted. Returns a process exit code."""
    raw = Path(raw_root)
    if not raw.is_dir():
        logger.error("no collected data at {}", raw)
        return 1
    packages = discover(raw)
    if not packages:
        logger.error("{} holds no collected session (no triples.jsonl)", raw)
        return 1

    state = ReviewState(raw, Path(review_dir), device_size=device_size, reviewer=reviewer)
    handler = type("BoundHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    logger.info(
        "review: {} app(s) under {} — verdicts go to {}", len(packages), raw, state.review_dir
    )
    print(f"monkey-collect review  ->  {url}")
    print(f"  corpus   : {raw}  ({len(packages)} app(s), read-only)")
    print(f"  verdicts : {state.review_dir}/by-{state.reviewer}.jsonl")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"  WARNING  : bound to {host} — the corpus is screenshots of a real, logged-in device")
    if open_browser:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - a browser is a convenience
            logger.debug("could not open a browser")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


__all__ = ["Handler", "ReviewState", "serve"]
