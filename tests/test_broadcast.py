"""v12.29: рассылка всем — флуд-повтор + точный учёт недоставки."""
import asyncio, importlib.util, os, sys, types
import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.update(BOT_TOKEN="1:test", PREMIUM_UI="1")


@pytest.fixture(scope="module")
def m(tmp_path_factory):
    import db as _db
    _db.DB_PATH = str(tmp_path_factory.mktemp("bcast") / "t.db")
    _db._conn = None
    os.environ["ADMIN_IDS"] = "1"
    spec = importlib.util.spec_from_file_location("botmod", os.path.join(BASE, "bot.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["botmod"] = mod
    spec.loader.exec_module(mod)
    mod.db.init()
    for i in range(1, 6):
        mod.db.upsert_user(i, f"u{i}")
    return mod


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def test_broadcast_counts_every_failure_reason(m):
    from aiogram.exceptions import (TelegramForbiddenError, TelegramRetryAfter,
                                    TelegramBadRequest)
    calls = {"4": 0, "5": 0}

    async def send_message(uid, text, **k):
        if uid == 2:
            raise TelegramForbiddenError(method="send", message="bot was blocked")
        if uid == 3:
            raise TelegramBadRequest(method="send",
                                     message="Bot can't initiate conversation with a user")
        if uid == 4:                       # HTML ломается, чистый текст — ок
            if k.get("parse_mode") == "HTML":
                raise TelegramBadRequest(method="send", message="can't parse entities")
            return "ok"
        if uid == 5:                       # сначала флуд, потом доставлено
            calls["5"] += 1
            if calls["5"] == 1:
                raise TelegramRetryAfter(method="send", message="Too Many Requests",
                                         retry_after=0)
            return "ok"
        return "ok"

    m.BOT.send_message = send_message
    ok, total, why = run(m.broadcast_all("Всем привет"))
    assert (ok, total) == (3, 5)
    assert why == {"blocked": 1, "no_chat": 1, "other": 0}
    assert calls["5"] == 2                 # флуд: пауза и повтор — доставлено


def test_broadcast_plain_text_fallback(m):
    from aiogram.exceptions import TelegramBadRequest

    async def send_message(uid, text, **k):
        if k.get("parse_mode") == "HTML":
            raise TelegramBadRequest(method="send", message="can't parse entities")
        return "ok"

    m.BOT.send_message = send_message
    ok, total, why = run(m.broadcast_all("Текст"))
    assert ok == total == 5 and why["other"] == 0
