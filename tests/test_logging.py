"""Secrets never reach the log: the filter from main.setup_logging."""
import logging

from corsarr import config, main
from corsarr.monitor import EventBuffer
from helpers import FakeConfig as Cfg


def test_secret_filter_masks_token_and_keys_in_message_args_and_exceptions():
    f = main.SecretFilter()
    f.update(Cfg(TELEGRAM_BOT_TOKEN="123456:ABC-def", ANTHROPIC_API_KEY="sk-ant-secret", ADMIN_PASSWORD="pw"))
    assert config.FIELD_BY_NAME["TELEGRAM_BOT_TOKEN"].secret  # the filter takes the fields marked secret
    buffer = EventBuffer()
    lines = []

    class Collect(logging.Handler):
        def emit(self, record):
            lines.append(self.format(record))
    collect = Collect()
    collect.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test.secrets")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    for h in (collect, buffer):
        h.addFilter(f)
        logger.addHandler(h)

    logger.info("GET https://api.telegram.org/bot123456:ABC-def/getMe")
    logger.info("key %s used for %s", "sk-ant-secret", "pw")
    try:
        raise ConnectionError("no route to https://api.telegram.org/bot123456:ABC-def/getUpdates")
    except ConnectionError:
        logger.error("failed", exc_info=True)
    text = "\n".join(lines)
    assert "123456:ABC-def" not in text and "sk-ant-secret" not in text
    assert "bot***/getMe" in lines[0] and lines[1] == "key *** used for pw"  # short "pw" stays readable
    assert "Traceback" in lines[2] and "bot***/getUpdates" in lines[2]
    events = buffer.since()
    assert "123456:ABC-def" not in str(events)
    assert events[2]["message"].endswith("ConnectionError: no route to https://api.telegram.org/bot***/getUpdates")

    f.update(Cfg())  # no secrets configured – records pass through untouched
    logger.info("plain %s", "123456:ABC-def")
    assert lines[-1] == "plain 123456:ABC-def"


def test_setup_logging_installs_filter_and_quiets_telegram(tmp_path, monkeypatch):
    root = logging.getLogger()
    before = list(root.handlers)
    (tmp_path / "logs").mkdir()
    cfg = Cfg(TELEGRAM_BOT_TOKEN="123456:ABC-def")
    cfg.log_level, cfg.log_dir = "DEBUG", tmp_path / "logs"
    try:
        main.setup_logging(cfg)
        assert logging.getLogger("telegram").level == logging.INFO
        assert logging.getLogger("telegram.ext").level == logging.INFO
        added = [h for h in root.handlers if h not in before]
        assert len(added) == 3 and all(main.secret_filter in h.filters for h in added)
        logging.getLogger("corsarr.test").warning("token 123456:ABC-def")
        for h in added:
            h.flush()
        assert "123456:ABC-def" not in (tmp_path / "logs" / "corsarr.log").read_text(encoding="utf-8")
    finally:
        for h in [h for h in root.handlers if h not in before]:
            root.removeHandler(h)
            h.close()
        main.secret_filter.update(Cfg())
