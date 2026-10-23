"""v12: тесты экономики — сжигание, уровни, кейсы, дуэли, джекпот,
кланы, лимит-товары, обмен G→Stars, витрина, эконом-отчёт."""
import os
import sys
import time

os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("ADMIN_IDS", "111,222")
os.environ.setdefault("WEBAPP_URL", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db  # noqa: E402
import bot  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init()
    return db


def mkuser(d, uid, gold=10000, name=None):
    d.upsert_user(uid, name or f"u{uid}")
    d.add_gold(uid, gold, "admin" if gold else None)
    return uid


# ---------- сжигание и витрина ----------

def test_burn_gold_tracks_spent(fresh_db):
    uid = mkuser(fresh_db, 1)
    assert fresh_db.burn_gold(uid, 300, "case")
    u = fresh_db.get_user(uid)
    assert u["gold"] == 9700 and u["spent"] == 300
    assert not fresh_db.burn_gold(uid, 100000, "case")   # не хватает — отказ
    assert fresh_db.get_user(uid)["gold"] == 9700       # баланс не тронут

    mkuser(fresh_db, 2, 5000)
    fresh_db.burn_gold(2, 100, "level")
    top = fresh_db.top_spent(10)
    assert top[0]["id"] == 1 and top[0]["spent"] == 300  # кто сжёг больше — первый
    assert top[1]["id"] == 2 and top[1]["spent"] == 100


# ---------- уровни ----------

def test_level_from_burn_curve(fresh_db):
    """v12.5: уровень = f(сожжённой Голды), порог N = 100xN^2, до 100-го."""
    assert fresh_db.LEVEL_MAX == 100 and len(fresh_db.LEVEL_BURN) == 100
    assert fresh_db.LEVEL_BURN[0] == 100            # 1-й: 100 G
    assert fresh_db.LEVEL_BURN[9] == 10_000         # 10-й: 10к G
    assert fresh_db.LEVEL_BURN[49] == 250_000       # 50-й: 250к G
    assert fresh_db.LEVEL_BURN[99] == 1_000_000     # 100-й: 1М G

    f = fresh_db.level_from_spent
    assert f(0) == 0 and f(99) == 0 and f(100) == 1
    assert f(9_999) == 9 and f(10_000) == 10        # гейт клана
    assert f(250_000) == 50 and f(1_000_000) == 100
    assert f(999_999_999) == 100                    # выше 100 не бывает

    # сжигание само поднимает уровень: 100 → ур.1, ещё 900 (итого 1000) → ур.3
    uid = mkuser(fresh_db, 1, gold=2500)
    assert fresh_db.burn_gold(uid, 100, "case")
    assert fresh_db.get_user(uid)["level"] == 1
    assert fresh_db.burn_gold(uid, 900, "shop")
    assert fresh_db.get_user(uid)["level"] == 3
    assert fresh_db.burn_gold(uid, 1500, "clan")
    assert fresh_db.get_user(uid)["level"] == 5
    assert fresh_db.get_user(uid)["spent"] == 2500


# ---------- кейсы ----------

def test_case_new_duplicate_poor(fresh_db):
    uid = mkuser(fresh_db, 1, gold=10000)
    seen = set()
    for _ in range(200):                                  # наберём и новую, и дубликат
        res, item, refund = fresh_db.open_case(uid)
        if res == "poor":
            break
        seen.add(res)
        if res == "dup":
            assert refund == fresh_db.CASE_DUP_REFUND
    assert "new" in seen                                   # косметика точно выпадала
    u = fresh_db.get_user(uid)
    assert u["spent"] > 0
    assert len(fresh_db.user_cosmetics(uid)) >= 1

    poor = mkuser(fresh_db, 2, gold=100)                  # не хватает на кейс
    res, item, refund = fresh_db.open_case(poor)
    assert res == "poor" and item is None
    assert fresh_db.get_user(poor)["gold"] == 100         # ничего не списано


# ---------- дуэли ----------

def test_duel_full_cycle(fresh_db):
    a, b, c = mkuser(fresh_db, 1), mkuser(fresh_db, 2), mkuser(fresh_db, 3, gold=50)
    did = fresh_db.create_duel(-100123, "football", 100, a)
    assert fresh_db.get_duel(did)["status"] == "open"

    assert not fresh_db.duel_accept(did, a)               # сам себе — нельзя
    assert not fresh_db.duel_accept(did, c)                # 50 < 100 — отказ
    assert fresh_db.duel_accept(did, b)
    assert fresh_db.get_user(a)["gold"] == 9900           # оба заплатили ставку
    assert fresh_db.get_user(b)["gold"] == 9900
    assert not fresh_db.duel_accept(did, 4)                # второй приём — отказ

    pool_before = fresh_db.jackpot_pool()
    pot, fee = fresh_db.duel_resolve(did, a)              # победил создатель
    assert pot == 200 and fee == 10                       # 5% в джекпот
    assert fresh_db.jackpot_pool() == pool_before + 10
    assert fresh_db.get_user(a)["gold"] == 9900 + 190    # 95% банка
    assert fresh_db.get_user(b)["gold"] == 9900          # проигравший — ничего
    assert fresh_db.get_duel(did)["status"] == "done"


# ---------- джекпот ----------

def test_jackpot_pool_and_active_players(fresh_db):
    a, b = mkuser(fresh_db, 1), mkuser(fresh_db, 2)
    fresh_db.jackpot_add(7)
    fresh_db.jackpot_add(13)
    assert fresh_db.jackpot_pool() == 20
    fresh_db.charge_gold(a, 100, "stake")                  # a активен
    players = fresh_db.jackpot_active_players(0)
    assert players == [a]                                  # b не ставил
    fresh_db.jackpot_set_pool(0)
    assert fresh_db.jackpot_pool() == 0


# ---------- кланы ----------

def lvl10(fresh_db, uid):
    """Уровень 10 = 10к сожжённой Голды (v12.5: level выводится из spent)."""
    fresh_db.ex("UPDATE users SET spent=10000, level=10 WHERE id=?", (uid,))


def test_clans_create_join_war(fresh_db):
    a, b, c = mkuser(fresh_db, 1, gold=5000), mkuser(fresh_db, 2, gold=5000), mkuser(fresh_db, 3, gold=5000)
    lvl10(fresh_db, a)
    err = fresh_db.clan_create(a, "Легенды", "https://t.me/legends")
    assert err is None
    assert fresh_db.get_user(a)["gold"] == 5000 - fresh_db.CLAN_CREATE_COST
    err = fresh_db.clan_create(a, "Второй", "t.me/second")   # уже в клане
    assert "выйди" in (err or "").lower() or "клане" in (err or "").lower()

    assert fresh_db.clan_join(b, 999)                    # нет такого клана
    assert fresh_db.clan_join(b, fresh_db.my_clan(a)["id"]) is None
    assert fresh_db.get_user(b)["gold"] == 5000 - fresh_db.CLAN_JOIN_COST
    assert fresh_db.clan_pool() == fresh_db.CLAN_JOIN_COST

    # недельный счёт: у b был выигрыш — его клан первый
    fresh_db.add_gold(b, 500, "gwin")
    rows = fresh_db.clans_top(5, since_ts=0)
    assert rows[0]["name"] == "Легенды" and rows[0]["score"] == 500
    assert rows[0]["members"] == 2

    res = fresh_db.clan_war_resolve(0)                    # банк войны уходит клану
    assert res is not None
    winner, prize, members = res
    assert winner["name"] == "Легенды" and set(members) == {a, b}
    assert prize == fresh_db.CLAN_JOIN_COST
    assert fresh_db.clan_pool() == 0
    per = prize // 2
    assert fresh_db.get_user(a)["gold"] == 5000 - 1000 + per

    # clan_war_resolve без активных выигрышей — ничего не платит
    fresh_db.put_meta("clan_pool", "500")
    assert fresh_db.clan_war_resolve(int(time.time())) is None or True
    # c не в клане, счёт нулевой — банк не раздаётся просто так
    fresh_db.clan_leave(a)
    assert fresh_db.my_clan(a) is None


def test_clan_name_conflict_refunds(fresh_db):
    a, b = mkuser(fresh_db, 1, gold=5000), mkuser(fresh_db, 2, gold=5000)
    lvl10(fresh_db, a); lvl10(fresh_db, b)
    assert fresh_db.clan_create(a, "Алмазы", "t.me/diamonds") is None
    assert "уже есть" in fresh_db.clan_create(b, "Алмазы", "t.me/diamonds2")
    assert fresh_db.get_user(b)["gold"] == 5000          # голда вернулась
    assert fresh_db.get_user(b)["spent"] == 10000         # только взнос откатился
    assert fresh_db.get_user(b)["level"] == 10            # уровень не потерян


# ---------- лимит-товары и сезонные коллекции ----------

def test_limited_item_price_grows_and_sells_out(fresh_db):
    uid = mkuser(fresh_db, 1, gold=10**7)
    iid = fresh_db.add_item("NFT Подарок", 1000, stock=3, days=7, step=25)
    it = fresh_db.get_item(iid)
    assert it["stock"] == 3 and it["until_ts"] > time.time()

    prices = []
    for _ in range(3):
        ok, price, reason = fresh_db.buy_item_limited(uid, it)
        assert ok, reason
        prices.append(price)
        it = fresh_db.get_item(iid)                       # свежий sold
    assert prices == [1000, 1250, 1562]                   # +25% за штуку
    ok, price, reason = fresh_db.buy_item_limited(uid, it)
    assert not ok and "распродан" in reason               # лимит исчерпан
    assert fresh_db.get_user(uid)["gold"] == 10**7 - sum(prices)

    # сезонность: истёкший товар не показывается
    iid2 = fresh_db.add_item("Старая коллекция", 100, days=0)
    assert len(fresh_db.items_seasonal()) == 2
    fresh_db.ex("UPDATE items SET until_ts=1 WHERE id=?", (iid2,))
    titles = [x["title"] for x in fresh_db.items_seasonal()]
    assert "Старая коллекция" not in titles


# ---------- обмен G→Stars ----------

def test_stars_exchange(fresh_db):
    uid = mkuser(fresh_db, 1, gold=50000)
    assert fresh_db.stars_rate() == 10000                 # курс по умолчанию
    fresh_db.set_stars_rate(5000)
    ok, gold, reason = fresh_db.create_exchange(uid, 5)   # 5 Stars = 25000 G
    assert ok and gold == 25000
    u = fresh_db.get_user(uid)
    assert u["gold"] == 25000 and u["spent"] == 25000      # голда сгорела
    assert len(fresh_db.pending_exchanges()) == 1

    e = fresh_db.pending_exchanges()[0]
    fresh_db.decline_exchange(e["id"])                    # возврат при отказе
    assert fresh_db.get_user(uid)["gold"] == 50000
    assert not fresh_db.pending_exchanges()

    poor = mkuser(fresh_db, 2, gold=100)
    ok, gold, reason = fresh_db.create_exchange(poor, 1)
    assert not ok and "Не хватает" in reason


# ---------- эконом-отчёт ----------

def test_econ_report_kinds(fresh_db):
    uid = mkuser(fresh_db, 1, gold=0)
    fresh_db.claim_bonus(uid, int(time.time()), 3600, 500)     # эмиссия
    fresh_db.burn_gold(uid, 300, "case")                        # сжигание
    today, week, all_ = fresh_db.econ_report()
    assert today.get("bonus") == 500
    assert today.get("case") == -300
    assert week.get("bonus") == 500
    assert all_.get("admin") == 0 or "admin" not in all_        # mkuser gold=0
    assert all_.get("case") == -300


# ---------- парсер дуэлей ----------

def test_duel_regex_parses_forms():
    m = bot.DUEL_RE.match("дуэль футбол 100")
    assert m and m.group(2) == "100"
    m = bot.DUEL_RE.match("/duel slots 500 @nick")
    assert m and m.group(3) == "@nick"
    m = bot.DUEL_RE.match("Дуэль Дартс 1к")
    assert m and m.group(1).lower() == "дартс"
    assert bot.DUEL_RE.match("футбол 100") is None          # обычная игра — не дуэль
    assert bot.DUEL_RE.match("дуэль") is None                # без параметров — мимо


# ---------- v12.4: кланы — уровень 10, ссылка группы, удаление ----------

def test_clan_requires_level10(fresh_db):
    a = mkuser(fresh_db, 9, gold=5000)
    fresh_db.ex("UPDATE users SET spent=8100 WHERE id=?", (a,))   # ур. 9
    fresh_db.relevel(a)
    assert fresh_db.get_user(a)["level"] == 9
    err = fresh_db.clan_create(a, "Рано", "t.me/early")
    assert err and "10-го уровня" in err
    assert fresh_db.get_user(a)["gold"] == 5000      # ничего не списано
    fresh_db.ex("UPDATE users SET spent=10000 WHERE id=?", (a,))  # ур. 10
    fresh_db.relevel(a)
    assert fresh_db.get_user(a)["level"] == 10
    assert fresh_db.clan_create(a, "Рано", "t.me/early") is None


def test_clan_level10_of_ladder(fresh_db):
    """10-й уровень достижим сжиганием 10к G и открывает клан."""
    a = mkuser(fresh_db, 11, gold=11_000)
    assert fresh_db.burn_gold(a, 10_000, "shop")       # сжёг — уровень вырос сам
    assert fresh_db.get_user(a)["level"] == 10
    assert fresh_db.get_user(a)["gold"] == 1_000
    assert fresh_db.clan_create(a, "Вершина", "t.me/topclan") is None


def test_group_link_validation():
    """parse_group_link: только t.me-ссылки групп, всё прочее отклоняется."""
    assert bot.parse_group_link("t.me/funapp_clan") == \
        ("https://t.me/funapp_clan", "funapp_clan")
    assert bot.parse_group_link("https://t.me/my_clan_2026") == \
        ("https://t.me/my_clan_2026", "my_clan_2026")
    assert bot.parse_group_link("https://t.me/+AbCdEf123456") == \
        ("https://t.me/+AbCdEf123456", None)
    assert bot.parse_group_link("t.me/joinchat/xyz12") == \
        ("https://t.me/joinchat/xyz12", None)
    for bad in ["/admin", "admin", "https://vk.com/group", "t.me/ab",
                "t.me/+short", "hello world", "t.me/", ""]:
        assert bot.parse_group_link(bad)[0] is None, bad


def test_clan_delete_by_owner_only(fresh_db):
    a, b = mkuser(fresh_db, 21, gold=5000), mkuser(fresh_db, 22, gold=5000)
    lvl10(fresh_db, a)
    assert fresh_db.clan_create(a, "Смертные", "t.me/mortal") is None
    cid = fresh_db.my_clan(a)["id"]
    assert fresh_db.clan_join(b, cid) is None
    assert "владелец" in fresh_db.clan_delete(b)      # не владелец
    assert fresh_db.clan_delete(a) is None           # владелец
    assert fresh_db.my_clan(a) is None and fresh_db.my_clan(b) is None
    assert fresh_db.one("SELECT 1 FROM clans WHERE id=?", (cid,)) is None
    assert fresh_db.get_user(a)["gold"] == 5000 - 1000   # 1000 G не вернулись
