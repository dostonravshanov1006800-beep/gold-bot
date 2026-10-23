"""v12.10: баннер «Справочник» + у каждой секции свой баннер."""
import os, sys, asyncio, importlib.util
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
    """Кнопка «Справочник»: баннер, затем полный справочник, старое удаляется."""
    c = FakeCb()
    asyncio.run(b.cb_refguide(c))
    kinds = [s[0] for s in c.message.sent]
    assert kinds[0] == "photo", "первым должен идти баннер"
    assert c.message.sent[0][1][0] == b.REF_BANNER_URL, "не тот баннер"
    assert "photo" not in kinds[1:], "фото должно быть одно"
    texts = [s[1] for s in c.message.sent if s[0] == "text"]
    assert texts and "Справочник" in texts[0], "справочник должен уйти следом"
    assert "delete" in kinds, "старый экран должен удалиться"

def test_help_command_shows_banner_first():
    """/help: баннер справочника, затем текст справочника."""
    m = FakeMsg()
    asyncio.run(b.cmd_help(m))
    assert m.sent[0][0] == "photo" and m.sent[0][1][0] == b.REF_BANNER_URL
    assert m.sent[1][0] == "text" and "Справочник" in m.sent[1][1]

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
                  "TOP_BANNER_URL", "CASE_BANNER_URL"]:
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
