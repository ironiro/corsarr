"""What the GUI shows: connection health per external system and a buffer of recent log events."""
from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import asdict, dataclass

import httpx

SERVICES = ("telegram", "llm", "jellyfin", "jellyseerr", "webhook", "sonarr", "radarr")


@dataclass
class ServiceState:
    status: str = "unknown"  # 'ok' | 'error' | 'unknown' | 'disabled'
    detail: str = ""
    checked_at: float | None = None
    last_ok: float | None = None


def describe_error(exc: BaseException) -> str:
    """'Type: message', plus the operating system's reason for connection errors.

    httpx only says "All connection attempts failed"; the useful part ("No route to host",
    "Connection refused", a DNS failure) sits in the chained OSError.
    """
    text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if isinstance(cur, OSError) and cur.strerror:
            return f"{text} ({cur.strerror})" if cur.strerror not in text else text
        stack += [e for e in (cur.__cause__, cur.__context__) if e is not None]
        stack += list(getattr(cur, "exceptions", ()))  # exception groups from parallel connect attempts
    return text


class Health:
    """Updated actively by the periodic checks and passively by every real call."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.services = {s: ServiceState() for s in SERVICES}

    def ok(self, service: str, detail: str | None = None) -> None:
        st = self.services[service]
        if detail is not None:
            st.detail = detail
        elif st.status != "ok":
            st.detail = ""  # passive success after an error: drop the stale error text
        st.status, st.checked_at = "ok", time.time()
        st.last_ok = st.checked_at

    def error(self, service: str, detail: str) -> None:
        st = self.services[service]
        st.status, st.detail, st.checked_at = "error", detail, time.time()

    def disabled(self, service: str, detail: str = "") -> None:
        st = self.services[service]
        st.status, st.detail, st.checked_at = "disabled", detail, time.time()

    def snapshot(self) -> dict[str, dict]:
        return {name: asdict(st) for name, st in self.services.items()}

    def track_http(self, service: str, exc: Exception | None) -> None:
        """Passive update from an HTTP call: only failures that mean 'not usable' count as errors."""
        if exc is None:
            self.ok(service)
        elif isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if code in (401, 403) or code >= 500:
                self.error(service, f"HTTP {code}")
            else:
                self.ok(service)  # reachable, the request itself was wrong
        elif isinstance(exc, httpx.HTTPError):
            self.error(service, describe_error(exc))


class EventBuffer(logging.Handler):
    """Keeps the last log records in memory for the GUI's event log."""

    def __init__(self, capacity: int = 1000) -> None:
        super().__init__()
        self.records: collections.deque[dict] = collections.deque(maxlen=capacity)
        self._next_id = 1
        self._lock_ = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                exc = record.exc_info[1]
                message += f" – {type(exc).__name__}: {exc}"
            with self._lock_:
                self.records.append({
                    "id": self._next_id, "ts": record.created, "level": record.levelname,
                    "logger": record.name.removeprefix("corsarr."), "message": message,
                })
                self._next_id += 1
        except Exception:  # never let logging break the bot
            self.handleError(record)

    def since(self, after: int = 0, limit: int = 1000) -> list[dict]:
        with self._lock_:
            return [r for r in self.records if r["id"] > after][-limit:]


health = Health()
events = EventBuffer()
