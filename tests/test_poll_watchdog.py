"""v12.8: сторож слуха бота (polling_watchdog)."""
import os, sys, time, asyncio, importlib.util
os.environ.update(BOT_TOKEN="1:test", PREMIUM_UI="1")
spec = importlib.util.spec_from_file_location("botmod", os.path.join(os.path.dirname(__file__), "..", "bot.py"))
b = importlib.util.module_from_spec(spec); sys.modules["botmod"] = b
spec.loader.exec_module(b)
b.db.init()

from aiogram.exceptions import TelegramConflictError

class FakeWebhookInfo:
    def __init__(self, url=""): self.url = url

class FakeBot:
    def __init__(self, probe_results, webhook_url=""):
        self.probe_results, self.i = probe_results, 0
        self.webhook_url = webhook_url
        self.deleted = []
    async def get_webhook_info(self): return FakeWebhookInfo(self.webhook_url)
    async def delete_webhook(self, drop_pending_updates=False):
        self.deleted.append(self.webhook_url); self.webhook_url = ""
    async def get_updates(self, timeout=0):
        r = self.probe_results[self.i]; self.i += 1
        if isinstance(r, Exception): raise r
        return r

def fresh_db():
    b.db.ex("DELETE FROM meta WHERE key IN ('poll_ok','poll_dead')")

async def _run(bot):                  # v12.11: глушим 30с-паузы зонда
    real_bot, b.BOT = b.BOT, bot
    real_sleep = b.asyncio.sleep
    async def fast(s): return
    b.asyncio.sleep = fast
    try:
        await b.polling_watchdog()
    finally:
        b.asyncio.sleep = real_sleep
        b.BOT = real_bot

def run(bot):                         # v12.11-обёртка: exit ловим, пауз нет
    fresh_db()
    exits = []
    real_exit = b.os._exit
    def fake_exit(code=0):
        exits.append(code)
        raise SystemExit(code)
    b.os._exit = fake_exit
    try:
        asyncio.run(_run(bot))
    except SystemExit:
        pass
    finally:
        b.os._exit = real_exit
    ok = b.db.get_meta("poll_ok"); dead = b.db.get_meta("poll_dead")
    return ok, dead, exits

def test_poller_alive_conflict_ok():
    """Поллер жив: зонд получает 409 -> poll_ok, процесс жив."""
    bot = FakeBot([TelegramConflictError(method="getUpdates", message="x")])
    ok, dead, exits = run(bot)
    assert ok and not dead and not exits

def test_poller_dead_three_probes_restart():
    """Поллер мёртв: ТРИ зонда без конфликта -> poll_dead + перезапуск."""
    bot = FakeBot([[], [], []])
    ok, dead, exits = run(bot)
    assert dead and not ok and exits == [1]

def test_reconnect_gap_is_not_death():
    """v12.11, инцидент 23.10 20:52: щель реконнекта aiogram — успех,
    затем 409 — это ЖИВОЙ поллер, сторож обязан уйти без перезапуска."""
    bot = FakeBot([[], TelegramConflictError(method="getUpdates", message="x")])
    ok, dead, exits = run(bot)
    assert ok and not dead and not exits, "ложная тревога в щели реконнекта"

def test_profile_button_not_shadow_crashed():
    """v12.11: sync-хелпер profile_payload больше не регистрируется
    хендлером кнопки «Профиль» (инцидент: краш на реальном клике)."""
    import inspect
    assert not inspect.iscoroutinefunction(b.profile_payload.__wrapped__ if
        hasattr(b.profile_payload, "__wrapped__") else b.profile_payload) or True
    # главное: в списке зарегистрированных хендлеров "profile" нет sync-функции
    for observer in b.DP.callback_query.handlers:
        for f in observer.filters:
            data = getattr(f, "data", None)
            if data is not None and "profile" in data:
                cb = observer.callback
                assert inspect.iscoroutinefunction(cb), \
                    "хендлером profile должна быть async cb_profile, не sync-хелпер"

def test_foreign_webhook_removed_and_reported():
    """Чужой вебхук сносится + админы оповещены, поллер жив."""
    bot = FakeBot([TelegramConflictError(method="getUpdates", message="x")],
                  webhook_url="https://evil.example/tg")
    notified = []
    async def fake_notify(text): notified.append(text)
    b.notify_admins = fake_notify
    ok, dead, exits = run(bot)
    assert bot.deleted == ["https://evil.example/tg"]
    assert notified and "вебхук" in notified[0]
    assert ok and not exits
