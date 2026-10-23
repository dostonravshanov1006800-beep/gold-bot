"""v12.12: сторож без зондов (getUpdates-протокол убит — он терминировал
собственного поллера) + middleware-маркер слуха upd_seen."""
import os, sys, time, asyncio, importlib.util
os.environ.update(BOT_TOKEN="1:test", PREMIUM_UI="1")
spec = importlib.util.spec_from_file_location("botmod", os.path.join(os.path.dirname(__file__), "..", "bot.py"))
b = importlib.util.module_from_spec(spec); sys.modules["botmod"] = b
spec.loader.exec_module(b)
b.db.init()

class FakeWebhookInfo:
    def __init__(self, url=""): self.url = url

class FakeBot:
    def __init__(self, webhook_url=""):
        self.webhook_url = webhook_url
        self.deleted = []
        self.get_updates_calls = 0
    async def get_webhook_info(self): return FakeWebhookInfo(self.webhook_url)
    async def delete_webhook(self, drop_pending_updates=False):
        self.deleted.append(self.webhook_url); self.webhook_url = ""
    async def get_updates(self, timeout=0):
        self.get_updates_calls += 1
        return []

def run(bot):
    b.db.ex("DELETE FROM meta WHERE key IN ('poll_ok','poll_dead','upd_seen')")
    real_bot, b.BOT = b.BOT, bot
    notified = []
    async def fake_notify(text): notified.append(text)
    real_notify = b.notify_admins
    b.notify_admins = fake_notify
    try:
        asyncio.run(b.polling_watchdog())
    finally:
        b.notify_admins = real_notify
        b.BOT = real_bot
    ok = b.db.get_meta("poll_ok"); dead = b.db.get_meta("poll_dead")
    return ok, dead, notified

def test_watchdog_never_touches_get_updates():
    """Зонд-протокол удалён: сторож НЕ зовёт getUpdates вовсе
    (иначе он терминирует ждущий запрос живого поллера — 409 получает
    поллер, не зонд; бот-убийца 23.10 16:07)."""
    bot = FakeBot()
    ok, dead, _ = run(bot)
    assert bot.get_updates_calls == 0, "никаких getUpdates-зондов!"
    assert ok and not dead, "poll_ok пишется каждый цикл"

def test_foreign_webhook_removed_and_reported():
    """Чужой вебхук сносится + админы оповещены (вектор глухоты №1)."""
    bot = FakeBot(webhook_url="https://evil.example/tg")
    ok, dead, notified = run(bot)
    assert bot.deleted == ["https://evil.example/tg"]
    assert notified and "вебхук" in notified[0]
    assert ok

def test_update_marker_throttled():
    """upd_seen пишется не чаще раза в минуту, апдейт доходит до хендлера."""
    seen = []
    async def handler(event, data): seen.append(event); return "OK"
    b.db.ex("DELETE FROM meta WHERE key='upd_seen'")
    b._upd_ts = 0.0
    r1 = asyncio.run(b._mark_update_seen(handler, "u1", {}))
    r2 = asyncio.run(b._mark_update_seen(handler, "u2", {}))
    assert r1 == r2 == "OK" and seen == ["u1", "u2"]
    assert b.db.get_meta("upd_seen"), "upd_seen записан"
    t = int(b.db.get_meta("upd_seen"))
    # второй вызов сразу после первого — троттлится (та же минута)
    b._upd_ts = t + 0.5
    asyncio.run(b._mark_update_seen(handler, "u3", {}))
    assert b.db.get_meta("upd_seen") == str(t), "чаще раза в минуту не пишем"

def test_start_polling_exit_triggers_supervisor():
    """Если start_polling вернулся — процесс уходит на перезапуск (exit 1),
    а супервизор в workflow его поднимает (set +e + while-цикл)."""
    w = open(os.path.join(os.path.dirname(__file__), "..", ".github/workflows/run-bot.yml")).read()
    assert "set +e" in w, "без set +e степ умирает на первом ненулевом выходе"
    assert 'code="$?"' in w or 'code=$?' in w, "код выхода должен ловиться"
    assert '"$code" = "42"' in w, "эстафета (42) завершает джобу спокойно"

def test_profile_button_not_shadow_crashed():
    """v12.11: sync-хелпер profile_payload больше не регистрируется
    хендлером кнопки «Профиль» (краш на реальном клике, логи 15:44)."""
    import inspect
    for observer in b.DP.callback_query.handlers:
        for f in observer.filters:
            data = getattr(f, "data", None)
            if data is not None and "profile" in data:
                assert inspect.iscoroutinefunction(observer.callback), \
                    "хендлером profile должна быть async cb_profile"
