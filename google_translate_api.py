#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Local HTTP API adapter for Google Translate's undocumented MkEWBc web RPC.

Final tested design:
- Upstream host: translate.google.com.vn
- RPC: MkEWBc
- Minimal form POST: only rpcids=MkEWBc + f.req
- mode="advanced" -> final payload flag 1
- mode="classic"  -> final payload flag 2
- No Cookie/bootstrap/Chrome/Scrapling required for the main translation path.
- Upstream requests are serialized (concurrency=1) through one persistent Session.
- No automatic host/IP switching and no retry loop.
- 302->/sorry, 403 and 429 trigger a local cooldown/circuit breaker.

This is NOT the official Google Cloud Translation API.
Google may change this private web RPC without notice.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests


UPSTREAM = "https://translate.google.com.vn/_/TranslateWebserverUi/data/batchexecute"
RPC_ID = "MkEWBc"
DEFAULT_TIMEOUT = 20.0
DEFAULT_COOLDOWN = 60.0
MAX_TEXT_CHARS = 20000

MODE_FLAGS = {
    "advanced": 1,
    "classic": 2,
}

# Exactly one persistent upstream Session.
_UPSTREAM_SESSION = requests.Session()
_UPSTREAM_SESSION.headers.clear()

# Serialize upstream calls. Local HTTP server may accept many clients, but only
# one request at a time is sent to Google's private RPC.
_UPSTREAM_LOCK = threading.Lock()

# Small circuit breaker: after 302 /sorry, 403 or 429, do not hammer upstream.
_STATE_LOCK = threading.Lock()
_BLOCKED_UNTIL = 0.0
_LAST_BLOCK_REASON: str | None = None


class TranslateError(RuntimeError):
    pass


@dataclass
class UpstreamBlockedError(TranslateError):
    upstream_status: int
    reason: str
    retry_after_seconds: int | None = None
    location: str | None = None

    def __str__(self) -> str:
        return self.reason


def normalize_mode(mode: str | None) -> str:
    mode = (mode or "advanced").strip().lower()
    if mode not in MODE_FLAGS:
        raise ValueError("mode must be 'advanced' or 'classic'")
    return mode


def build_f_req(text: str, source: str, target: str, mode: str = "advanced") -> str:
    mode = normalize_mode(mode)
    mode_flag = MODE_FLAGS[mode]

    inner = json.dumps(
        [[text, source, target, 1, None, mode_flag], []],
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return json.dumps(
        [[[RPC_ID, inner, None, "generic"]]],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def parse_frames(raw: str) -> list[Any]:
    """
    Parse batchexecute line frames. Responses observed in the captures contain:
      )]}'
      <byte-count>
      <JSON frame>
      ...
    """
    frames: list[Any] = []

    for line in raw.splitlines():
        s = line.strip()
        if not s or s == ")]}'" or s.isdigit() or not s.startswith("["):
            continue
        try:
            frames.append(json.loads(s))
        except json.JSONDecodeError:
            pass

    return frames


def extract_payload(raw: str) -> Any:
    for frame in parse_frames(raw):
        if not isinstance(frame, list):
            continue

        for item in frame:
            if (
                isinstance(item, list)
                and len(item) >= 3
                and item[0] == "wrb.fr"
                and item[1] == RPC_ID
                and isinstance(item[2], str)
            ):
                try:
                    return json.loads(item[2])
                except json.JSONDecodeError as exc:
                    raise TranslateError(
                        f"Invalid inner MkEWBc JSON: {exc}"
                    ) from exc

    raise TranslateError("MkEWBc payload not found in Google response")


def join_segments(segments: Any) -> str:
    """
    Captured MkEWBc segment behavior:
    - segment[0] = translated text
    - segment[2] is True => insert one separator space before this segment
    - newlines may already be embedded in segment[0]
    """
    if not isinstance(segments, list):
        raise TranslateError("Unexpected segment container")

    out: list[str] = []

    for i, seg in enumerate(segments):
        if not isinstance(seg, list) or not seg or not isinstance(seg[0], str):
            continue

        text = seg[0]

        add_space_before = (
            i > 0
            and len(seg) > 2
            and seg[2] is True
            and text
            and not text[0].isspace()
        )

        if add_space_before:
            out.append(" ")

        out.append(text)

    if not out:
        raise TranslateError("No translated segments found")

    return "".join(out)


def extract_translation(raw: str) -> tuple[str, dict[str, Any]]:
    payload = extract_payload(raw)

    try:
        segments = payload[1][0][0][5]
        translation = join_segments(segments)
    except (IndexError, TypeError) as exc:
        raise TranslateError("Google MkEWBc response shape changed") from exc

    meta: dict[str, Any] = {}

    try:
        if isinstance(payload[2], str):
            meta["detected_source"] = payload[2]
    except (IndexError, TypeError):
        pass

    try:
        if isinstance(payload[1][1], str):
            meta["target_language"] = payload[1][1]
    except (IndexError, TypeError):
        pass

    return translation, meta


def _parse_retry_after(value: str | None) -> int | None:
    if not value:
        return None

    value = value.strip()

    if value.isdigit():
        return max(0, int(value))

    try:
        dt = parsedate_to_datetime(value)
        seconds = int(dt.timestamp() - time.time())
        return max(0, seconds)
    except Exception:
        return None


def _set_block(seconds: float, reason: str) -> None:
    global _BLOCKED_UNTIL, _LAST_BLOCK_REASON

    with _STATE_LOCK:
        _BLOCKED_UNTIL = max(_BLOCKED_UNTIL, time.time() + max(0.0, seconds))
        _LAST_BLOCK_REASON = reason


def breaker_state() -> dict[str, Any]:
    with _STATE_LOCK:
        remaining = max(0.0, _BLOCKED_UNTIL - time.time())
        return {
            "cooldown_active": remaining > 0,
            "retry_after_seconds": int(remaining + 0.999) if remaining > 0 else 0,
            "last_block_reason": _LAST_BLOCK_REASON,
        }


def _raise_if_cooldown() -> None:
    state = breaker_state()
    if state["cooldown_active"]:
        raise UpstreamBlockedError(
            upstream_status=0,
            reason="Local upstream cooldown is active",
            retry_after_seconds=state["retry_after_seconds"],
        )


def translate(
    text: str,
    source: str = "auto",
    target: str = "vi",
    mode: str = "advanced",
    timeout: float = DEFAULT_TIMEOUT,
    cooldown_seconds: float = DEFAULT_COOLDOWN,
) -> dict[str, Any]:

    if not isinstance(text, str) or not text:
        raise ValueError("text must be a non-empty string")

    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"text exceeds {MAX_TEXT_CHARS} characters")

    if not isinstance(source, str) or not source:
        raise ValueError("source/from must be a language code or 'auto'")

    if not isinstance(target, str) or not target:
        raise ValueError("target/to must be a language code")

    mode = normalize_mode(mode)

    _raise_if_cooldown()

    started = time.perf_counter()

    # Queue upstream work: no parallel MkEWBc requests.
    with _UPSTREAM_LOCK:
        # Cooldown may have been activated while this request waited in queue.
        _raise_if_cooldown()

        response = _UPSTREAM_SESSION.post(
            UPSTREAM,
            params={"rpcids": RPC_ID},
            data={"f.req": build_f_req(text, source, target, mode)},
            timeout=timeout,
            allow_redirects=False,
        )

    upstream_ms = (time.perf_counter() - started) * 1000.0

    retry_after = _parse_retry_after(response.headers.get("Retry-After"))

    # Do not follow Google /sorry redirects.
    if 300 <= response.status_code < 400:
        location = response.headers.get("Location")
        is_sorry = bool(location and "/sorry/" in location)

        if is_sorry:
            seconds = retry_after or int(cooldown_seconds)
            _set_block(seconds, "Google redirected MkEWBc to /sorry")
            raise UpstreamBlockedError(
                upstream_status=response.status_code,
                reason="Google redirected MkEWBc to /sorry",
                retry_after_seconds=seconds,
                location=location,
            )

        raise TranslateError(
            f"Google returned redirect HTTP {response.status_code}"
        )

    if response.status_code in (403, 429):
        seconds = retry_after or int(cooldown_seconds)
        reason = f"Google returned HTTP {response.status_code}"
        _set_block(seconds, reason)

        raise UpstreamBlockedError(
            upstream_status=response.status_code,
            reason=reason,
            retry_after_seconds=seconds,
        )

    if response.status_code != 200:
        raise TranslateError(
            f"Google returned HTTP {response.status_code}: "
            f"{response.text[:200]!r}"
        )

    translated, meta = extract_translation(response.text)

    return {
        "translation": translated,
        "source": source,
        "target": target,
        "detected_source": meta.get("detected_source"),
        "mode": mode,
        "upstream_ms": round(upstream_ms, 2),
    }


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "GTLocalAPI/2.0"

    def log_message(self, fmt, *args):
        print(
            f"[{time.strftime('%H:%M:%S')}] "
            f"{self.client_address[0]} - {fmt % args}"
        )

    def send_json(
        self,
        status: int,
        obj: Any,
        extra_headers: dict[str, str] | None = None,
    ):
        payload = json.dumps(
            obj,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-API-Key",
        )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)

        self.end_headers()

        if status != 204:
            self.wfile.write(payload)

    def check_api_key(self) -> bool:
        api_key = getattr(self.server, "api_key", None)

        if not api_key:
            return True

        if self.headers.get("X-API-Key") == api_key:
            return True

        self.send_json(401, {"ok": False, "error": "invalid_api_key"})
        return False

    def do_OPTIONS(self):
        self.send_json(204, {})

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "service": "google-translate-local-api",
                    "version": "2.0",
                    "rpc": RPC_ID,
                    "upstream_host": "translate.google.com.vn",
                    "default_mode": "advanced",
                    **breaker_state(),
                },
            )
            return

        if parsed.path != "/translate":
            self.send_json(
                404,
                {
                    "ok": False,
                    "error": "not_found",
                    "endpoints": [
                        "GET /health",
                        "GET /translate?text=hello&from=en&to=vi&mode=advanced",
                        "POST /translate",
                    ],
                },
            )
            return

        if not self.check_api_key():
            return

        q = parse_qs(parsed.query)

        text = q.get("text", [""])[0]
        source = q.get("from", q.get("source", ["auto"]))[0]
        target = q.get("to", q.get("target", ["vi"]))[0]
        mode = q.get("mode", ["advanced"])[0]

        self.handle_translate(text, source, target, mode)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path != "/translate":
            self.send_json(404, {"ok": False, "error": "not_found"})
            return

        if not self.check_api_key():
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0

        if length <= 0 or length > 2_000_000:
            self.send_json(400, {"ok": False, "error": "invalid_body"})
            return

        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            self.send_json(
                400,
                {
                    "ok": False,
                    "error": "invalid_json",
                    "detail": str(exc),
                },
            )
            return

        if not isinstance(body, dict):
            self.send_json(
                400,
                {
                    "ok": False,
                    "error": "body_must_be_object",
                },
            )
            return

        text = body.get("text", "")
        source = body.get("from", body.get("source", "auto"))
        target = body.get("to", body.get("target", "vi"))
        mode = body.get("mode", "advanced")

        self.handle_translate(text, source, target, mode)

    def handle_translate(
        self,
        text: str,
        source: str,
        target: str,
        mode: str,
    ):
        started = time.perf_counter()

        try:
            result = translate(
                text=text,
                source=source,
                target=target,
                mode=mode,
                timeout=self.server.upstream_timeout,
                cooldown_seconds=self.server.cooldown_seconds,
            )

            result["total_ms"] = round(
                (time.perf_counter() - started) * 1000.0,
                2,
            )

            self.send_json(200, {"ok": True, **result})

        except ValueError as exc:
            self.send_json(
                400,
                {
                    "ok": False,
                    "error": "bad_request",
                    "detail": str(exc),
                },
            )

        except requests.Timeout:
            self.send_json(
                504,
                {
                    "ok": False,
                    "error": "upstream_timeout",
                },
            )

        except requests.RequestException as exc:
            self.send_json(
                502,
                {
                    "ok": False,
                    "error": "upstream_network_error",
                    "detail": str(exc),
                },
            )

        except UpstreamBlockedError as exc:
            retry_after = exc.retry_after_seconds or 1

            # A direct upstream 429 remains 429.
            # /sorry, 403, or local cooldown use 503.
            local_status = 429 if exc.upstream_status == 429 else 503

            error_code = {
                429: "upstream_rate_limited",
                403: "upstream_forbidden",
                0: "upstream_cooldown",
            }.get(exc.upstream_status, "upstream_blocked")

            self.send_json(
                local_status,
                {
                    "ok": False,
                    "error": error_code,
                    "detail": exc.reason,
                    "upstream_status": exc.upstream_status,
                    "retry_after_seconds": retry_after,
                },
                extra_headers={"Retry-After": str(retry_after)},
            )

        except TranslateError as exc:
            self.send_json(
                502,
                {
                    "ok": False,
                    "error": "upstream_error",
                    "detail": str(exc),
                },
            )


def main() -> int:
    p = argparse.ArgumentParser(
        description="Local HTTP translation API using Google MkEWBc"
    )

    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument(
        "--cooldown",
        type=float,
        default=DEFAULT_COOLDOWN,
        help="Local cooldown seconds after 302 /sorry, 403 or 429",
    )
    p.add_argument(
        "--api-key",
        default=None,
        help="Optional X-API-Key required for /translate",
    )

    args = p.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), ApiHandler)
    server.upstream_timeout = args.timeout
    server.cooldown_seconds = max(0.0, args.cooldown)
    server.api_key = args.api_key

    print("=" * 72)
    print("Google Translate Local API V2")
    print(f"Listen       : http://{args.host}:{args.port}")
    print(f"Health       : http://{args.host}:{args.port}/health")
    print("Upstream     : translate.google.com.vn")
    print("RPC          : MkEWBc")
    print("Default mode : advanced (flag 1)")
    print("Classic mode : classic  (flag 2)")
    print("Upstream queue: concurrency = 1")
    print(f"Cooldown     : {server.cooldown_seconds:g}s after 302/403/429")
    print("No automatic host/IP switching. No retry loop.")
    print("Ctrl+C to stop")
    print("=" * 72)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        server.server_close()
        _UPSTREAM_SESSION.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
