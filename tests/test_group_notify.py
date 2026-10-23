"""v12.20: уведомления владельцам о подключении/удалении групп."""
import asyncio, importlib.util, os, sys, types
import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.update(BOT_TOKEN="1:test", PREMIUM_UI="1")


@pytest.fixture(scope="module")
def m(tmp_path_factory):
    import db as _db
    os.environ["ADMIN_IDS"] = "1,2"          # оба владельца (в фикстуре!)
    _db.DB_PATH = str(tmp_path_factory.mktemp("grp") / "t.db")
    _db._conn = None
    spec = importlib.util.spec_from_file_location("botmod", os.path.join(BASE, "bot.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["botmod"] = mod
    spec.loader.exec_module(mod)
    mod.db.init()
    return mod


class FakeBot:
    def __init__(self):
        self.sent = []          # (uid, text)
        self.id = 999
    async def send_message(self, uid, text=None, **k):
        self.sent.append((uid, text))


def upd(chat_id, title, st, who_id=55, who_un="adder"):
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(id=chat_id, title=title, type="supergroup"),
        from_user=types.SimpleNamespace(id=who_id, username=who_un, first_name="A"),
        new_chat_member=types.SimpleNamespace(status=st))


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def test_add_group_notifies_admins(m):
    bot = FakeBot(); real = m.BOT; m.BOT = bot
    try:
        run(m.on_my_chat_member(upd(-100777, "Клуб Футбола", "member")))
        uids = [u for u, _ in bot.sent]
        assert 1 in uids and 2 in uids, "оба владельца получают уведомление"
        body = [t for u, t in bot.sent if u == 1][0]
        assert "Клуб Футбола" in body and "-100777" in body
        assert "@adder" in body and "участник" in body
        assert m.db.get_group(-100777) is not None
    finally:
        m.BOT = real


def test_add_as_admin_shows_rights(m):
    bot = FakeBot(); real = m.BOT; m.BOT = bot
    try:
        run(m.on_my_chat_member(upd(-100888, "Кибер Арена", "administrator")))
        body = [t for u, t in bot.sent if u == 1][0]
        assert "администратор" in body
    finally:
        m.BOT = real


def test_kick_notifies(m):
    bot = FakeBot(); real = m.BOT; m.BOT = bot
    try:
        run(m.on_my_chat_member(upd(-100777, "Клуб Футбола", "kicked")))
        body = [t for u, t in bot.sent if u == 1][0]
        assert "удалён из группы" in body
    finally:
        m.BOT = real


def test_private_chat_not_notified(m):
    bot = FakeBot(); real = m.BOT; m.BOT = bot
    try:
        u = upd(777, "ЛС", "member")
        u.chat.type = "private"
        run(m.on_my_chat_member(u))
        assert not bot.sent
    finally:
        m.BOT = real
