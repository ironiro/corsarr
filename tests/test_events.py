import logging
import time
from datetime import datetime

from corsarr.monitor import EventBuffer, parse_log

START = "Corsarr starting"
LISTEN = "Web server (GUI and webhook) listening on "


def _line(ts, level, name, message):
    stamp = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S,") + f"{int(ts * 1000) % 1000:03d}"
    return f"{stamp} {level} {name}: {message}"


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_parse_log_lines_tracebacks_and_multiline_messages():
    t0 = 1_700_000_000.25
    text = "\n".join([
        "  File \"x.py\", line 1, in <module>",  # rest of an entry from the older file
        _line(t0, "INFO", "corsarr.bot", "first"),
        _line(t0 + 1, "ERROR", "corsarr.main", "Telegram error: boom"),
        "Traceback (most recent call last):",
        '  File "a.py", line 3, in f',
        "    raise KeyError('a')",
        "KeyError: 'a'",
        "",
        "During handling of the above exception, another exception occurred:",
        "",
        "Traceback (most recent call last):",
        '  File "b.py", line 9, in g',
        "ValueError: boom",
        _line(t0 + 2, "WARNING", "corsarr.web", "two lines"),
        "second line",
        _line(t0 + 3, "INFO", "telegram.ext.Application", "Application started"),
    ])
    entries = parse_log(text)
    assert [e["message"] for e in entries] == [
        "first", "Telegram error: boom – ValueError: boom", "two lines\nsecond line", "Application started"]
    assert [e["level"] for e in entries] == ["INFO", "ERROR", "WARNING", "INFO"]
    assert entries[0]["logger"] == "bot" and entries[3]["logger"] == "telegram.ext.Application"
    assert abs(entries[0]["ts"] - t0) < 0.002


def test_parse_log_marks_starts_old_and_new_style():
    t0 = 1_700_000_000.0
    text = "\n".join([
        _line(t0, "INFO", "corsarr.web", LISTEN + "http://localhost:8787/"),  # old file: no start line
        _line(t0 + 1, "INFO", "corsarr.bot", "a"),
        _line(t0 + 2, "INFO", "corsarr", START),
        _line(t0 + 3, "INFO", "corsarr.web", LISTEN + "http://localhost:8787/"),
        _line(t0 + 4, "INFO", "corsarr.bot", "b"),
    ])
    entries = parse_log(text, (START,), (LISTEN,))
    assert [("|" if e.get("restart") else e["message"][:7]) for e in entries] == [
        "|", "Web ser", "a", "|", "Corsarr", "Web ser", "b"]


def test_restore_fills_buffer_with_rotated_file_and_limit(tmp_path):
    t0 = time.time() - 3600
    log_file = tmp_path / "corsarr.log"
    _write(tmp_path / "corsarr.log.1", [_line(t0 + i, "INFO", "corsarr.bot", f"old {i}") for i in range(700)])
    _write(log_file, [_line(t0 + 700, "INFO", "corsarr", START)]
           + [_line(t0 + 701 + i, "INFO", "corsarr.bot", f"new {i}") for i in range(500)])
    buf = EventBuffer(capacity=1000)
    assert buf.restore(log_file, (START,)) == 998
    logging.getLogger("corsarr.test").addHandler(buf)
    try:
        logging.getLogger("corsarr.test").warning("live")
    finally:
        logging.getLogger("corsarr.test").removeHandler(buf)
    records = buf.since()
    assert len(records) == 1000
    ids = [r["id"] for r in records]
    assert ids == sorted(ids) and len(set(ids)) == 1000
    assert records[-1]["message"] == "live" and records[-2].get("restart")
    restarts = [i for i, r in enumerate(records) if r.get("restart")]
    assert len(restarts) == 2 and records[restarts[0] + 1]["message"] == START
    assert records[0]["message"] == "old 204"  # 999 restored + marker, the live entry pushed the oldest out


def test_restore_without_file_and_divider_not_first(tmp_path):
    buf = EventBuffer()
    assert buf.restore(tmp_path / "corsarr.log") == 0 and buf.since() == []
    _write(tmp_path / "corsarr.log", [_line(time.time(), "INFO", "corsarr", START),
                                      _line(time.time(), "INFO", "corsarr.bot", "x")])
    assert buf.restore(tmp_path / "corsarr.log", (START,)) == 2
    assert [bool(r.get("restart")) for r in buf.since()] == [False, False, True]
