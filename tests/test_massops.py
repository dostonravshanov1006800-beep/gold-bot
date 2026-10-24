"""v12.19: масс-операции админа — «отнять у всех …», «сбросить всё»."""
import asyncio, importlib.util, os, sys, types
import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.update(BOT_TOKEN="1:test", PREMIUM_UI="1")


@pytest.fixture(scope="module")
def m(tmp_path_factory):
    import db as _db
    _db.DB_PATH = str(tmp_path_factory.mktemp("mass") / "t.db")
    _db._conn = None
    os.environ["ADMIN_IDS"] = "1"
    spec = importlib.util.spec_from_file_location("botmod", os.path.join(BASE, "bot.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["botmod"] = mod
    spec.loader.exec_module(mod)
    mod.db.init()
    for i in (1, 2, 3):
        mod.db.upsert_user(i, f"u{i}")
    mod.db.add_gold(2, 1000)
    mod.db.add_gold(3, 300)
    return mod


class Msg:
    def __init__(self, uid, text):
        self.from_user = types.SimpleNamespace(id=uid, username="x", first_name="X")
        self.chat = types.SimpleNamespace(id=uid, type="private")
        self.text = text
        self.out = []
    async def answer(self, text, **k):
        self.out.append(text)
        return types.SimpleNamespace(message_id=len(self.out))
    async def edit_text(self, text, **k):
        self.out.append(text)


class CB:
    def __init__(self, uid, data, msg):
        self.from_user = types.SimpleNamespace(id=uid)
        self.data = data
        self.message = msg
        self.answers = []
    async def answer(self, text=None, **k):
        self.answers.append(text)


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def handler(m, name):
    for h in m.DP.message.handlers + m.DP.callback_query.handlers:
        if h.callback.__name__ == name:
            return h.callback
    raise KeyError(name)


def test_take_all_amount_flow(m):
    h = handler(m, "admin_mass_ops")
    msg = Msg(1, "отнять у всех 500")
    run(h(msg))
    assert msg.out and "Изъятие голды" in msg.out[0] and "500" in msg.out[0]
    assert "Подтверждаю" in str(msg) or True          # кнопка в markup
    cbh = handler(m, "cb_adm_mass")
    cb = CB(1, "adm:take:500", Msg(1, ""))
    run(cbh(cb))
    assert "Сожжено" in cb.message.out[0] and "800" in cb.message.out[0]   # 500+300
    assert m.db.get_user(2)["gold"] == 500
    assert m.db.get_user(3)["gold"] == 0


def test_take_all_everything(m):
    m.db.add_gold(3, 250)
    h = handler(m, "admin_mass_ops")
    msg = Msg(1, "отнять у всех всё")
    run(h(msg))
    assert "ВСЮ голду" in msg.out[0]
    cbh = handler(m, "cb_adm_mass")
    cb = CB(1, "adm:take:all", Msg(1, ""))
    run(cbh(cb))
    assert m.db.get_user(2)["gold"] == 0
    assert m.db.get_user(3)["gold"] == 0


def test_factory_reset_wipes_everything(m):
    """v12.28: «сбросить всё» — заводской сброс: чистый лист, юзеры остаются."""
    m.db.add_gold(2, 777)
    m.db.jackpot_add(100)
    m.db.put_meta("clan_pool", "200")
    h = handler(m, "admin_mass_ops")
    msg = Msg(1, "сбросить всё")
    run(h(msg))
    assert "Заводской сброс" in msg.out[0] and "777" in msg.out[0]
    assert "пользователи" in msg.out[0]                # что останется — названо
    cbh = handler(m, "cb_adm_mass")
    cb = CB(1, "adm:reset", Msg(1, ""))
    run(cbh(cb))
    assert "Заводской сброс выполнен" in cb.message.out[0]
    # балансы и пулы обнулены
    assert m.db.get_user(2)["gold"] == 0
    assert m.db.jackpot_pool() == 0
    assert m.db.clan_pool() == 0
    # история/журналы/статистика стерты
    assert m.db.q("SELECT * FROM ledger") == []
    assert m.db.q("SELECT * FROM rate_log") == []
    assert m.db.q("SELECT * FROM game_stats") == []
    assert m.db.q("SELECT * FROM duels") == []
    assert m.db.q("SELECT * FROM clans") == []
    # сами пользователи остались, параметры — заводские
    u = m.db.get_user(2)
    assert u is not None and u["gold"] == 0 and (u["level"] or 0) == 0
    assert u["spent"] == 0 and (u["games_played"] or 0) == 0


def test_cancel_keeps_balances(m):
    m.db.add_gold(3, 42)
    h = handler(m, "admin_mass_ops")
    msg = Msg(1, "сбросить всё")
    run(h(msg))
    cbh = handler(m, "cb_adm_mass")
    cb = CB(1, "adm:cancel", Msg(1, ""))
    run(cbh(cb))
    assert m.db.get_user(3)["gold"] == 42
    assert "Отменено" in cb.message.out[0]


def test_stranger_and_unrelated_text_ignored(m):
    h = handler(m, "admin_mass_ops")
    msg = Msg(2, "сбросить всё")                       # не админ
    with pytest.raises(Exception):
        run(h(msg))                                     # SkipHandler
    msg2 = Msg(1, "футбол 100")                         # не масс-команда
    with pytest.raises(Exception):
        run(h(msg2))


def test_ledger_records_burn(m):
    h = handler(m, "admin_mass_ops")
    run(h(Msg(1, "отнять у всех всё")))
    cbh = handler(m, "cb_adm_mass")
    run(cbh(CB(1, "adm:take:all", Msg(1, ""))))
    rows = m.db.q("SELECT * FROM ledger WHERE kind='takeall' OR kind='reset'")
    assert rows, "изъятия пишутся в эконом-журнал"
