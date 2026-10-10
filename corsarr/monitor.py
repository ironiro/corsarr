"""What the GUI shows: connection health per external system and a buffer of recent log events."""
from __future__ import annotations

import collections
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import httpx

from .i18n import t

SERVICES = ("telegram", "llm", "jellyfin", "jellyseerr", "webhook", "sonarr", "radarr")


@dataclass
class ServiceState:
    status: str = "unknown"  # 'ok' | 'error' | 'unknown' | 'disabled'
    detail: str = ""
    checked_at: float | None = None
    last_ok: float | None = None
    event: str = ""  # webhooks: the last event that arrived, e.g. "last: Download"


def describe_error(exc: BaseException) -> str:
    """'Type: message', plus the operating system's reason for connection errors.

    httpx only says "All connection attempts failed"; the useful part ("No route to host",
    "Connection refused", a DNS failure) sits in the chained OSError.
    """
    if type(exc).__module__.startswith("corsarr") and str(exc):
        return str(exc)  # our own exceptions already carry a readable message
    text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    if isinstance(exc, httpx.TimeoutException) and not str(exc):
        try:  # "ConnectTimeout" alone doesn't say which address didn't answer
            url = exc.request.url
            return f"{text} ({t('check.no_answer', target=f'{url.host}:{url.port or url.scheme}')})"
        except RuntimeError:
            return text
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


# A line as the log file's formatter writes it (main.setup_logging):
# "2026-01-31 18:04:05,123 INFO corsarr.bot: message". Lines that don't match continue the entry before.
LOG_LINE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
                      r"(DEBUG|INFO|WARNING|ERROR|CRITICAL) (\S+): (.*)")


class EventBuffer(logging.Handler):
    """Keeps the last log records in memory for the GUI's event log.

    After a restart, `restore()` fills it from the tail of the log file, so the entries from before stay
    visible; a marker entry (`"restart": True`) stands at each point where the program started.
    """

    def __init__(self, capacity: int = 1000) -> None:
        super().__init__()
        self.records: collections.deque[dict] = collections.deque(maxlen=capacity)
        self._next_id = 1
        self._lock_ = threading.Lock()
        # Ids start at 1 again in every process; the GUI notices the new value and loads everything anew.
        self.boot = f"{time.time():.6f}"

    def _append(self, entry: dict) -> None:
        self.records.append({"id": self._next_id, **entry})
        self._next_id += 1

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                exc = record.exc_info[1]
                message += f" – {type(exc).__name__}: {exc}"
            with self._lock_:
                self._append({"ts": record.created, "level": record.levelname,
                              "logger": record.name.removeprefix("corsarr."), "message": message})
        except Exception:  # never let logging break the bot
            self.handleError(record)

    def since(self, after: int = 0, limit: int = 1000) -> list[dict]:
        with self._lock_:
            return [r for r in self.records if r["id"] > after][-limit:]

    def restore(self, log_file: Path, start_messages: tuple[str, ...] = (),
                fallback_start: tuple[str, ...] = ()) -> int:
        """Fill the buffer from the tail of `log_file` (and its rotated predecessor `.1` if needed).

        `start_messages`: the line each program start writes; a restart marker goes before it.
        `fallback_start`: message prefixes that mark a start in older files without that line.
        Returns the number of restored entries; a marker for the current start follows them.
        """
        capacity = self.records.maxlen or 1000
        entries: list[dict] = []
        for path in (log_file, log_file.with_name(log_file.name + ".1")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                break  # no file (yet): nothing (more) to restore
            entries = parse_log(text, start_messages, fallback_start) + entries
            if len(entries) >= capacity:
                break
        entries = entries[-(capacity - 1):]
        while entries and entries[0].get("restart"):
            entries.pop(0)  # a divider above everything says nothing
        if not entries:
            return 0
        with self._lock_:
            for entry in entries:
                self._append(entry)
            self._append(_marker(time.time()))
        return sum(not e.get("restart") for e in entries)


def _marker(ts: float) -> dict:
    return {"ts": ts, "level": "", "logger": "", "message": "", "restart": True}


def parse_log(text: str, start_messages: tuple[str, ...] = (),
              fallback_start: tuple[str, ...] = ()) -> list[dict]:
    """Entries (without ids) in the event buffer's shape from the log file's text.

    Continuation lines (multi-line messages, tracebacks) belong to the entry before; of a traceback only
    its last line ("ValueError: ...") is kept, the same way `EventBuffer.emit` shows exceptions.
    """
    entries: list[dict] = []
    in_traceback = False
    exc_line = ""
    started = False  # a start line was seen and the fallback line of the same start not yet

    def finish() -> None:
        if entries and exc_line:
            entries[-1]["message"] += f" – {exc_line}"

    for line in text.splitlines():
        m = LOG_LINE.fullmatch(line)
        if m is None:
            if not entries:
                continue  # the rest of an entry whose beginning lies in the older file
            if line.startswith("Traceback (most recent call last):"):
                in_traceback = True
            elif in_traceback:
                if line and not line[0].isspace() and not line.startswith("During handling") \
                        and not line.startswith("The above exception"):
                    exc_line = line
            else:
                entries[-1]["message"] += "\n" + line
            continue
        finish()
        in_traceback, exc_line = False, ""
        stamp, level, name, message = m.groups()
        try:
            ts = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S,%f").timestamp()
        except ValueError:
            continue
        if message in start_messages:
            entries.append(_marker(ts))
            started = True
        elif message.startswith(fallback_start):  # False for an empty tuple
            if not started:
                entries.append(_marker(ts))
            started = False
        entries.append({"ts": ts, "level": level, "logger": name.removeprefix("corsarr."), "message": message})
    finish()
    return entries


health = Health()
events = EventBuffer()
