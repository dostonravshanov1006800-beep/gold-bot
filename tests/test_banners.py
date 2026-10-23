"""v12.10: баннер «Справочник» + у каждой секции свой баннер."""
import os, sys, asyncio, types, importlib.util
os.environ.update(BOT_TOKEN="1:test", PREMIUM_UI="1")
_dir = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location(
    "botmod", os.path.join(_dir, "..", "bot.py"))
b = importlib.util.module_from_spec(spec); sys.modules["botmod"] = b
spec.loader.exec_module(b)
b.db.init()

class FakeMsg:
    def __init__(self, uid=777):
        from types import SimpleNamespace
        self.from_user = SimpleNamespace(id=uid, username=None)
        self.chat = SimpleNamespace(id=uid, type="private")   # v12.15: swap-экраны
        self.message_id = 400                                 # v12.18: id команды
        self.sent = []          # ('photo'|'text', payload)
    async def answer(self, text=None, **k): self.sent.append(("text", text))
    async def answer_photo(self, photo=None, caption=None, **k):
        self.sent.append(("photo", (photo, caption)))

class FakeCbMsg(FakeMsg):
    async def delete(self): self.sent.append(("delete", None))

class FakeCb:
    def __init__(self, uid=777):
        self.message = FakeCbMsg(uid)
        from types import SimpleNamespace
        self.from_user = SimpleNamespace(id=uid, username=None)
        self.answers = []
    async def answer(self, text=None, **k): self.answers.append(text)

def test_refguide_shows_banner_then_pack():
    """v12.17: кнопка «Справочник» — баннер с подписью-справочником
    внутри (раньше текст шёл отдельным сообщением)."""
    c = FakeCb()
    asyncio.run(b.cb_refguide(c))
    assert c.message.sent[0][0] == "photo"
    assert c.message.sent[0][1][0] == b.REF_BANNER_URL
    cap = c.message.sent[0][1][1]
    assert cap and "Справочник" in cap
    assert not [s for s in c.message.sent if s[0] == "text"], \
        "отдельного текстового сообщения быть не должно"

def test_plays_command_shows_games_banner():
    """/plays: баннер «Игры» с подписью-экраном."""
    m = FakeMsg()
    asyncio.run(b.cmd_plays(m))
    assert m.sent[0][0] == "photo" and m.sent[0][1][0] == b.GAMES_BANNER_URL
    cap = m.sent[0][1][1]
    assert cap and "Игры" in cap, "подпись с балансом должна быть"

def test_bonus_uses_bonus_banner():
    """/bonus: баннер «Бонусы» (не потерян при рефакторингах)."""
    m = FakeMsg()
    asyncio.run(b.cmd_bonus(m))
    assert m.sent[0][0] == "photo" and m.sent[0][1][0] == b.BONUS_BANNER_URL

def test_every_banner_constant_is_used():
    """Ни один загруженный баннер не должен висеть мёртвым грузом."""
    src = open(os.path.join(_dir, "..", "bot.py")).read()
    for const in ["BANNER_URL", "TASKS_BANNER_URL", "PROFILE_BANNER_URL",
                  "GAMES_BANNER_URL", "SHOP_BANNER_URL", "BONUS_BANNER_URL",
                  "TOPUP_BANNER_URL", "REF_BANNER_URL",
                  "TOP_BANNER_URL", "CASE_BANNER_URL",
                  "CLAN_BANNER_URL", "EXCH_BANNER_URL"]:
        uses = src.count(const)
        assert uses >= 2, f"{const} определён, но не используется ({uses})"


def test_top_screen_uses_top_banner():
    """v12.13: кнопка «Топ» открывает экран с баннером ТОП."""
    c = FakeCb()
    asyncio.run(b.cb_top(c))
    assert c.message.sent[0][0] == "photo"
    assert c.message.sent[0][1][0] == b.TOP_BANNER_URL
    assert "Топ по Голде" in c.message.sent[0][1][1]

def test_case_screen_uses_case_banner():
    """v12.13: кнопка «Кейсы» открывает экран с баннером КЕЙСЫ."""
    c = FakeCb()
    asyncio.run(b.cb_case(c))
    assert c.message.sent[0][0] == "photo"
    assert c.message.sent[0][1][0] == b.CASE_BANNER_URL


def test_open_banner_no_duplicate_when_delete_fails():
    """v12.14, скрин 22:42: старое сообщение не удаляется (группа, >48ч,
    закреплённое) — НЕ должен отправляться текст повторно. Фото+подпись
    единым пузырём, ошибка удаления проглатывается молча."""
    from aiogram.exceptions import TelegramAPIError
    class NoDeleteMsg(FakeCbMsg):
        async def delete(self):
            raise TelegramAPIError(method="deleteMessage", message="can't delete")
    c = FakeCb()
    c.message = NoDeleteMsg()
    asyncio.run(b.cb_top(c))
    kinds = [s[0] for s in c.message.sent]
    assert kinds.count("text") == 0, f"лишнее текстовое сообщение: {kinds}"
    assert kinds[0] == "photo", "баннер должен быть фото с подписью"
    cap = c.message.sent[0][1][1]
    assert "Топ по Голде" in cap, "текст должен быть подписью внутри фото"


# ---------- v12.15: один живой экран на чат ----------

class FakeChat:
    def __init__(self, cid=777, ctype="private"):
        self.id, self.type = cid, ctype

class SentMsg:
    def __init__(self, mid): self.message_id = mid

class SwapMsg:
    """Сообщение из ЛС: answer/answer_photo возвращают SentMsg."""
    def __init__(self, cid=777, mid=400):
        from types import SimpleNamespace
        self.chat = FakeChat(cid)
        self.from_user = SimpleNamespace(id=cid, username=None)
        self.message_id = mid          # id сообщения-команды юзера
        self._n = 5000
    async def answer(self, text=None, **k):
        self._n += 1; return SentMsg(self._n)
    async def answer_photo(self, photo=None, caption=None, **k):
        self._n += 1; return SentMsg(self._n)

class FakeBotApi:
    def __init__(self): self.deleted = []
    async def delete_message(self, cid, mid): self.deleted.append((cid, mid))
    async def set_chat_menu_button(self, chat_id=None, menu_button=None): pass

def test_commands_swap_screens():
    """v12.15: /bonus потом /plays — экран /bonus удаляется,
    запоминается новый. В личке всегда один живой экран."""
    m = SwapMsg()
    api = FakeBotApi(); real_bot = b.BOT; b.BOT = api
    try:
        b.db.put_meta("screen:777", "111,222")
        asyncio.run(b.cmd_bonus(m))
        assert (777, 111) in api.deleted and (777, 222) in api.deleted, \
            "прошлые экраны должны удаляться"
        v = b.db.get_meta("screen:777")
        assert v and "111" not in v, f"экран перезаписан, а не дописан: {v}"
        asyncio.run(b.cmd_plays(m))
        v2 = b.db.get_meta("screen:777")
        assert v2 != v, "новая команда перезаписала экран"
        assert b.db.get_meta("screen:777").count(",") == 0, \
            "одна команда = один экран-баннер"
    finally:
        b.BOT = real_bot
        b.db.ex("DELETE FROM meta WHERE key LIKE 'screen:%' OR key LIKE 'cmd:%'")

def test_help_swaps_single_bubble():
    """v12.17: /help = ОДНО сообщение (баннер + подпись-справочник),
    старый экран удаляется."""
    m = SwapMsg()
    api = FakeBotApi(); real_bot = b.BOT; b.BOT = api
    try:
        b.db.put_meta("screen:777", "999")
        asyncio.run(b.cmd_help(m))
        assert (777, 999) in api.deleted
        v = b.db.get_meta("screen:777")
        assert v and "," not in v, f"один пузырь = один id: {v}"
    finally:
        b.BOT = real_bot
        b.db.ex("DELETE FROM meta WHERE key LIKE 'screen:%' OR key LIKE 'cmd:%'")

def test_groups_do_not_delete_screens():
    """В группе экраны разных игроков — ничего не удаляем и не помним."""
    from types import SimpleNamespace
    m = SwapMsg(); m.chat = FakeChat(-100, "supergroup")
    api = FakeBotApi(); real_bot = b.BOT; b.BOT = api
    try:
        b.db.put_meta("screen:-100", "111")
        asyncio.run(b.cmd_balance(m))
        assert api.deleted == [], "в группах удаление запрещено"
    finally:
        b.BOT = real_bot
        b.db.ex("DELETE FROM meta WHERE key LIKE 'screen:%' OR key LIKE 'cmd:%'")


def test_clan_screen_uses_clan_banner():
    """v12.16: кнопка «Клан» — баннер КЛАН с текстом внутри фото."""
    c = FakeCb()
    asyncio.run(b.cb_clan(c))
    assert c.message.sent[0][0] == "photo"
    assert c.message.sent[0][1][0] == b.CLAN_BANNER_URL

def test_exch_screen_uses_exch_banner():
    """v12.16: кнопка «Обмен» — баннер ОБМЕН STARS с текстом внутри фото."""
    c = FakeCb()
    asyncio.run(b.cb_exch(c))
    assert c.message.sent[0][0] == "photo"
    assert c.message.sent[0][1][0] == b.EXCH_BANNER_URL

def test_refguide_caption_inside_photo():
    """v12.17: справочник — подпись ВНУТРИ баннера, один пузырь,
    отдельного текстового сообщения нет (скрин 23:24)."""
    c = FakeCb()
    asyncio.run(b.cb_refguide(c))
    kinds = [s[0] for s in c.message.sent]
    assert "text" not in kinds, f"отдельного текста нет: {kinds}"
    assert kinds.count("photo") == 1, f"одно фото-сообщение: {kinds}"
    cap = c.message.sent[0][1][1]
    assert "Справочник" in cap and "Игры" in cap

def test_reference_caption_fits_telegram_limit():
    """Подпись справочника <= 1024 видимых знаков (лимит Telegram)."""
    import re
    vis = re.sub(r"<[^>]+>", "", b.reference_caption())
    assert len(vis) <= 1024, f"подпись {len(vis)} знаков — Telegram отклонит"
    assert "Джекпот" in vis and "Клан" in vis and "Обмен" in vis


def test_old_user_command_deleted_on_new_command():
    """v12.18: /bonus (msg 100) -> /plays (msg 101): сообщение «/bonus»
    юзера удаляется вместе со старым экраном."""
    api = FakeBotApi(); real_bot = b.BOT; b.BOT = api
    try:
        m1 = SwapMsg(mid=100)
        asyncio.run(b.cmd_bonus(m1))
        screens_after_bonus = len(api.deleted)
        m2 = SwapMsg(mid=101)
        asyncio.run(b.cmd_plays(m2))
        assert (777, 100) in api.deleted, "команда /bonus должна удалиться"
        assert (777, 101) not in api.deleted, "текущая команда живёт"
        assert b.db.get_meta("cmd:777") == "101"
    finally:
        b.BOT = real_bot
        b.db.ex("DELETE FROM meta WHERE key LIKE 'screen:%' OR key LIKE 'cmd:%'")

def test_start_and_balance_track_commands_too():
    """v12.18: /start и /balance тоже запоминают команду."""
    api = FakeBotApi(); real_bot = b.BOT; b.BOT = api
    try:
        m1 = SwapMsg(mid=200)
        m1.from_user.first_name = "Тест"
        asyncio.run(b.cmd_start(m1, types.SimpleNamespace(args="")))
        assert b.db.get_meta("cmd:777") == "200"
        m2 = SwapMsg(mid=201)
        asyncio.run(b.cmd_balance(m2))
        assert (777, 200) in api.deleted
        assert b.db.get_meta("cmd:777") == "201"
    finally:
        b.BOT = real_bot
        b.db.ex("DELETE FROM meta WHERE key LIKE 'screen:%' OR key LIKE 'cmd:%'")

def test_button_navigation_keeps_command_message():
    """v12.18: навигация кнопками НЕ трогает сообщение-команду."""
    api = FakeBotApi(); real_bot = b.BOT; b.BOT = api
    try:
        m1 = SwapMsg(mid=300)
        asyncio.run(b.cmd_bonus(m1))
        before = list(api.deleted)
        c = FakeCb()
        asyncio.run(b.cb_games(c))          # кнопка «Игры» — навигация
        assert list(api.deleted) == before or (777, 300) not in api.deleted
        assert b.db.get_meta("cmd:777") == "300", "команда живёт при кнопках"
    finally:
        b.BOT = real_bot
        b.db.ex("DELETE FROM meta WHERE key LIKE 'screen:%' OR key LIKE 'cmd:%'")
