import asyncio, importlib.util, os, sys, types
import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.update(BOT_TOKEN="1:test", PREMIUM_UI="1")


@pytest.fixture(scope="module")
def m(tmp_path_factory):
    import db as _db
    _db.DB_PATH = str(tmp_path_factory.mktemp("crm") / "t.db")
    _db._conn = None
    os.environ["ADMIN_IDS"] = "1"
    spec = importlib.util.spec_from_file_location("botmod", os.path.join(BASE, "bot.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.db.init()
    for i in (1, 2):
        mod.db.upsert_user(i, f"u{i}")
    mod.db.add_gold(2, 500)
    return mod


class Sent(list):
    pass


def fake_bot(m, sent):
    async def send_message(chat_id, text, **k): sent.append(("send", chat_id, text))
    async def edit_message_text(text, chat_id=None, message_id=None, **k): sent.append(("edit", message_id, text))
    m.BOT.send_message = send_message
    m.BOT.edit_message_text = edit_message_text


class Msg:
    def __init__(self, uid, text=None, mid=10):
        self.from_user = types.SimpleNamespace(id=uid, username="x", first_name="X")
        self.chat = types.SimpleNamespace(id=uid)
        self.message_id = mid
        self.text = text
        self.html_text = text
        self.out = []
    async def answer(self, text, **k): self.out.append(text)
    async def edit_text(self, text, **k): self.out.append(text)
    async def delete(self): pass


class CB:
    def __init__(self, uid, data, msg):
        self.from_user = types.SimpleNamespace(id=uid)
        self.data = data
        self.message = msg
        self.answers = []
    async def answer(self, text=None, **k): self.answers.append(text)


def handler(m, name):
    for h in m.DP.message.handlers + m.DP.callback_query.handlers:
        if h.callback.__name__ == name:
            return h.callback
    raise KeyError(name)


def run(c): return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def test_admin_opens_overview_and_blocks_stranger(m):
    h = handler(m, "cmd_admin")
    a, s = Msg(1), Msg(2)
    run(h(a)); run(h(s))
    assert "Клавиатура мини-аппа убрана" in a.out[0]
    assert "CRM" in a.out[1] and "Обзор" in a.out[1]
    assert "Только для админа" in s.out[0]


def test_add_task_by_text_and_delete(m):
    sent = Sent(); fake_bot(m, sent)
    cb, txt = handler(m, "cb_crm"), handler(m, "crm_text")
    msg = Msg(1)
    run(cb(CB(1, "a:tasks:add", msg), ))
    run(txt(Msg(1, "@chan 50")))
    t = [x for x in m.db.tasks() if x["channel"] == "@chan"]
    assert t and t[0]["reward"] == 50
    assert any("Задание @chan" in e[2] for e in sent if e[0] == "edit")
    run(cb(CB(1, f"a:tasks:del:{t[0]['id']}", msg)))
    assert not [x for x in m.db.tasks() if x["channel"] == "@chan"]


def test_bad_input_keeps_state(m):
    sent = Sent(); fake_bot(m, sent)
    cb, txt = handler(m, "cb_crm"), handler(m, "crm_text")
    run(cb(CB(1, "a:shop:add", Msg(1))))
    run(txt(Msg(1, "abc")))
    assert any("Формат" in e[2] for e in sent)
    run(txt(Msg(1, "150 NFT Gift")))
    assert any(i["title"] == "NFT Gift" and i["cost"] == 150 for i in m.db.items())


def test_requests_done_and_decline(m):
    sent = Sent(); fake_bot(m, sent)
    cb = handler(m, "cb_crm")
    iid = m.db.items()[-1]["id"]
    m.db.add_request(2, iid); r1 = m.db.pending_requests()[-1]["id"]
    run(cb(CB(1, f"a:reqs:done:{r1}", Msg(1))))
    assert any(e[0] == "send" and e[1] == 2 and "выдан" in e[2] for e in sent)
    m.db.add_request(2, iid); r2 = m.db.pending_requests()[-1]["id"]
    run(cb(CB(1, f"a:reqs:no:{r2}", Msg(1))))
    assert any(e[0] == "send" and "отклонена" in e[2] for e in sent)
    assert not m.db.pending_requests()


def test_give_gold(m):
    sent = Sent(); fake_bot(m, sent)
    cb, txt = handler(m, "cb_crm"), handler(m, "crm_text")
    before = m.db.get_user(2)["gold"]
    run(cb(CB(1, "a:users:give:2", Msg(1))))
    run(txt(Msg(1, "77")))
    assert m.db.get_user(2)["gold"] == before + 77


def test_broadcast_flow(m):
    sent = Sent(); fake_bot(m, sent)
    cb, txt = handler(m, "cb_crm"), handler(m, "crm_text")
    run(cb(CB(1, "a:bc:new", Msg(1))))
    run(txt(Msg(1, "Привет всем")))
    assert any("Предпросмотр" in e[2] for e in sent if e[0] == "edit")
    run(cb(CB(1, "a:bc:go", Msg(1))))
    assert len([e for e in sent if e[0] == "send" and e[2] == "Привет всем"]) == 2


def test_stranger_cannot_use_crm(m):
    c = CB(2, "a:tasks:add", Msg(2))
    run(handler(m, "cb_crm")(c))
    assert c.answers and "админа" in c.answers[0]


def test_plain_text_from_admin_without_state_is_skipped(m):
    """Обычные сообщения не глотаются CRM — иначе сломается любой другой текстовый хендлер."""
    from aiogram.dispatcher.event.bases import SkipHandler
    txt = handler(m, "crm_text")
    try:
        run(txt(Msg(1, "просто сообщение")))
        assert False, "должен был бросить SkipHandler"
    except SkipHandler:
        pass
    except Exception as e:
        raise AssertionError(f"неожиданное исключение: {e}")


def test_legacy_panel_text_answers(m):
    from aiogram.dispatcher.event.bases import SkipHandler
    h = handler(m, "legacy_panel")
    a = Msg(1, text="Панель")
    run(h(a))
    assert any("удалён" in t for t in a.out)
    st = Msg(2, text="Панель")
    try:
        run(h(st))
        assert False, "должен был бросить SkipHandler"
    except SkipHandler:
        pass


def test_draw_bank_math(m):
    """Сумма ровно банк; топ-3 > места 4-10 > 11-20 по средней доле."""
    import crm as crmmod
    users = [{"id": i, "username": None} for i in range(25)]
    res = crmmod.draw_bank(1000, users)
    assert sum(a for _, a in res) == 1000
    top3 = [a for _, a in res[:3]]
    mid = [a for _, a in res[3:10]]
    low = [a for _, a in res[10:20]]
    assert sum(top3) / 3 > sum(mid) / 7 > sum(low) / 10
    assert len(res) == 20


def test_bank_flow_via_crm(m):
    sent = Sent(); fake_bot(m, sent)
    cb, txt = handler(m, "cb_crm"), handler(m, "crm_text")
    for i in range(3, 28):
        m.db.upsert_user(i, f"u{i}")
        m.db.add_gold(i, i * 10)
    before = {u["id"]: u["gold"] for u in m.db.top_users(25)}
    run(cb(CB(1, "a:bank:new", Msg(1))))
    run(txt(Msg(1, "1000")))
    after = {u["id"]: u["gold"] for u in m.db.top_users(25)}
    assert sum(after[k] - before[k] for k in after) == 1000
    g = m.db.last_giveaway()
    assert g and g["amount"] == 1000 and g["players"] == 20
    # уведомления победителям ушли
    assert sum(1 for e in sent if e[0] == "send" and "Розыгрыш банка" in e[2]) == 20


def test_bank_rejects_garbage(m):
    sent = Sent(); fake_bot(m, sent)
    cb, txt = handler(m, "cb_crm"), handler(m, "crm_text")
    run(cb(CB(1, "a:bank:new", Msg(1))))
    g0 = m.db.last_giveaway()
    run(txt(Msg(1, "миллион")))
    assert any("целое число" in e[2] for e in sent)
    assert m.db.last_giveaway() == g0  # новый розыгрыш не создан


def test_channel_diagnostics(m):
    sent = Sent(); fake_bot(m, sent)
    class Mem:
        def __init__(self, status): self.status = status
    class Me:
        id = 8721423480
    async def me(): return Me()
    async def gcm(channel, uid):
        assert uid == 8721423480
        if channel == "@ok":
            return Mem("administrator")
        if channel == "@member":
            return Mem("member")
        from aiogram.exceptions import TelegramAPIError
        raise TelegramAPIError(method="getChatMember", message="Bad Request: member list is inaccessible")
    m.BOT.me = me
    m.BOT.get_chat_member = gcm
    m.db.add_task("@ok", 10)
    m.db.add_task("@bad", 10)
    msg = Msg(1)
    run(handler(m, "cb_crm")(CB(1, "a:tasks:check", msg)))
    edits = msg.out
    assert any("@ok — бот админ" in x for x in edits)
    assert any("@bad — бот НЕ в канале" in x for x in edits)
    assert any("@funapp_game" in x for x in edits)


def test_user_view_screen(m):
    """Экран юзера: баланс + полные кнопки Начислить/Отнять."""
    fake_bot(m, Sent())
    cb = handler(m, "cb_crm")
    msg = Msg(1)
    run(cb(CB(1, "a:users:v:2", msg)))
    assert any("Баланс:" in x for x in msg.out)
    assert any("@u2" in x for x in msg.out)          # имя юзера целиком


def test_users_list_single_buttons(m):
    """Список юзеров: одна полная кнопка на юзера (имя+баланс), без 2 колонок."""
    fake_bot(m, Sent())
    cb = handler(m, "cb_crm")
    msg = Msg(1)
    run(cb(CB(1, "a:users", msg)))
    assert any("Нажми на юзера" in x for x in msg.out)
    assert not any("Начислить @" in x for x in msg.out)  # кнопки не режут имена
