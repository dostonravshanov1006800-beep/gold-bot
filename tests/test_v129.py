"""v12.9: слэш-команды не глотаются вводами + чистка мусорных кланов."""
import os, sys, time, asyncio, importlib.util
os.environ.update(BOT_TOKEN="1:test", PREMIUM_UI="1")
_dir = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location(
    "botmod", os.path.join(_dir, "..", "bot.py"))
b = importlib.util.module_from_spec(spec); sys.modules["botmod"] = b
spec.loader.exec_module(b)
b.db.init()
from aiogram.dispatcher.event.bases import SkipHandler

class FakeUser:
    def __init__(self, uid): self.id = uid
class FakeMsg:
    def __init__(self, uid, text):
        self.from_user = FakeUser(uid); self.text = text
        self.replies = []
    async def answer(self, text=None, **k): self.replies.append(text)

def test_exch_input_releases_slash_commands():
    """Ввод суммы обмена пропускает команды дальше и сбрасывает контекст."""
    uid = 301
    b.EXCH_AWAIT[uid] = time.time()
    m = FakeMsg(uid, "/balance")
    try:
        asyncio.run(b.exch_amount_input(m))
        raised = False
    except SkipHandler:
        raised = True
    assert raised, "/balance должно уйти дальше, а не съесться"
    assert uid not in b.EXCH_AWAIT, "контекст обмена должен сброситься"

def test_clan_name_input_releases_slash_commands():
    """Ввод ссылки клана пропускает команды дальше и сбрасывает контекст."""
    uid = 302
    b.CLAN_NAME_AWAIT[uid] = time.time()
    m = FakeMsg(uid, "/admin")
    try:
        asyncio.run(b.clan_name_input(m))
        raised = False
    except SkipHandler:
        raised = True
    assert raised, "/admin должно уйти дальше, а не съесться"
    assert uid not in b.CLAN_NAME_AWAIT, "контекст клана должен сброситься"

def test_junk_clan_cleanup_and_refund():
    """Миграция v12.9 сносит клан «/admin» без участников и возвращает 1000 G."""
    d = b.db
    uid = 8396883978
    d.ex("DELETE FROM clan_members")
    d.ex("DELETE FROM clans")
    d.ex("DELETE FROM users WHERE id=?", (uid,))
    d.ex("INSERT INTO users (id, username, gold) VALUES(?, '', 0)", (uid,))
    d.ex("INSERT INTO clans (name, owner, created, link) VALUES('/admin', ?, ?, NULL)",
         (uid, int(time.time())))
    d.init()                                  # миграция выполняется при init
    left = d.q("SELECT * FROM clans WHERE name LIKE '/%'")
    assert not left, "мусорный клан должен быть удалён"
    gold = d.one("SELECT gold FROM users WHERE id=?", (uid,))["gold"]
    assert gold == d.CLAN_CREATE_COST, f"ожидали возврат {d.CLAN_CREATE_COST}, got {gold}"
