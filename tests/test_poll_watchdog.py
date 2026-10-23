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

def run(bot):
    real_bot = b.BOT
    b.BOT = bot                       # подменяем глобальный BOT на фейк
    fresh_db()
    exits = []
    real_exit = b.os._exit
    b.os._exit = lambda code=0: exits.append(code) or (_ for _ in ()).throw(
        SystemExit(code))
    try:
        asyncio.run(b.polling_watchdog())
    except SystemExit:
        pass
    finally:
        b.os._exit = real_exit
        b.BOT = real_bot
    ok = b.db.get_meta("poll_ok"); dead = b.db.get_meta("poll_dead")
    return ok, dead, exits

def test_poller_alive_conflict_ok():
    """Поллер жив: зонд получает 409 -> poll_ok, процесс жив."""
    bot = FakeBot([TelegramConflictError(method="getUpdates", message="x")])
    ok, dead, exits = run(bot)
    assert ok and not dead and not exits

def test_poller_dead_two_probes_restart():
    """Поллер мёртв: два ответа без конфликта -> poll_dead + перезапуск."""
    bot = FakeBot([[], []])
    ok, dead, exits = run(bot)
    assert dead and not ok and exits == [1]

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
