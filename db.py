import logging
import os
import random
import sqlite3
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    username TEXT,
    gold INTEGER DEFAULT 0,
    ref_by INTEGER,
    ref_paid INTEGER DEFAULT 0,
    last_bonus INTEGER DEFAULT 0,
    joined INTEGER
);
CREATE TABLE IF NOT EXISTS tasks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT,
    reward INTEGER,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS user_tasks(
    user_id INTEGER,
    task_id INTEGER,
    done INTEGER DEFAULT 0,
    PRIMARY KEY(user_id, task_id)
);
CREATE TABLE IF NOT EXISTS items(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    cost INTEGER,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS giveaways(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount INTEGER NOT NULL,
    players INTEGER NOT NULL,
    winners TEXT NOT NULL,
    created_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS requests(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    item_id INTEGER,
    status TEXT DEFAULT 'pending',
    created INTEGER
);

CREATE TABLE IF NOT EXISTS promos(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    amount INTEGER NOT NULL,
    max_uses INTEGER DEFAULT 0,
    uses INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS promo_used(
    promo_id INTEGER,
    user_id INTEGER,
    PRIMARY KEY(promo_id, user_id)
);
CREATE TABLE IF NOT EXISTS groups(
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    added_by INTEGER,
    added_at INTEGER,
    active INTEGER DEFAULT 1,
    games_on INTEGER DEFAULT 1,
    min_stake INTEGER DEFAULT 1,
    max_stake INTEGER DEFAULT 0,
    flood_sec INTEGER DEFAULT 3,
    tx_on INTEGER DEFAULT 1,
    tx_confirm INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transfers(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id INTEGER NOT NULL,
    to_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    ts INTEGER DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS group_stats(
    chat_id INTEGER,
    user_id INTEGER,
    played INTEGER DEFAULT 0,
    won INTEGER DEFAULT 0,
    lost INTEGER DEFAULT 0,
    total_won INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS updates_log(
    chat_id INTEGER,
    chat_type TEXT,
    user_id INTEGER,
    text TEXT,
    ts INTEGER
);
CREATE TABLE IF NOT EXISTS payments(
    charge_id TEXT PRIMARY KEY,
    user_id INTEGER,
    amount INTEGER,
    ts INTEGER
);

CREATE TABLE IF NOT EXISTS ledger(
    ts INTEGER,
    user_id INTEGER,
    delta INTEGER,
    kind TEXT
);
CREATE INDEX IF NOT EXISTS idx_ledger_ts ON ledger(ts);
CREATE INDEX IF NOT EXISTS idx_ledger_kind ON ledger(kind);
CREATE TABLE IF NOT EXISTS cosmetics(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    kind TEXT,
    value TEXT,
    rarity TEXT,
    created INTEGER
);
CREATE TABLE IF NOT EXISTS duels(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    game TEXT,
    stake INTEGER,
    p1 INTEGER,
    p2 INTEGER,
    status TEXT DEFAULT 'open',
    winner INTEGER,
    created INTEGER
);
CREATE TABLE IF NOT EXISTS clans(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    owner INTEGER,
    link TEXT,
    created INTEGER
);
CREATE TABLE IF NOT EXISTS clan_members(
    user_id INTEGER PRIMARY KEY,
    clan_id INTEGER,
    joined INTEGER
);
CREATE TABLE IF NOT EXISTS rate_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rate INTEGER,
    ts INTEGER
);
CREATE TABLE IF NOT EXISTS exchanges(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    gold INTEGER,
    stars INTEGER,
    status TEXT DEFAULT 'pending',
    created INTEGER
);
CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS game_stats(
    game TEXT,
    value INTEGER,
    ts INTEGER
);"""


SCHEMA_ECON = """
CREATE INDEX IF NOT EXISTS idx_ledger_ts ON ledger(ts);
CREATE INDEX IF NOT EXISTS idx_ledger_kind ON ledger(kind);
"""

def init():
    global _conn
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=DELETE")
    _conn.executescript(SCHEMA)
    for col, dflt in [("tx_on", "INTEGER DEFAULT 1"),
                      ("tx_confirm", "INTEGER DEFAULT 0")]:
        try:                                   # миграция групп (v11.46)
            _conn.execute(f"ALTER TABLE groups ADD COLUMN {col} {dflt}")
        except sqlite3.OperationalError:
            pass
    for col, dflt in [("games_played", "INTEGER DEFAULT 0"),
                      ("games_won", "INTEGER DEFAULT 0"),
                      ("games_lost", "INTEGER DEFAULT 0"),
                      ("total_won", "INTEGER DEFAULT 0")]:
        try:                                   # миграция старой базы
            _conn.execute(f"ALTER TABLE users ADD COLUMN {col} {dflt}")
        except sqlite3.OperationalError:
            pass
    for col, dflt in [("level", "INTEGER DEFAULT 0"),        # v12: платные уровни
                      ("spent", "INTEGER DEFAULT 0")]:       # v12: сожжено (витрина)
        try:
            _conn.execute(f"ALTER TABLE users ADD COLUMN {col} {dflt}")
        except sqlite3.OperationalError:
            pass
    try:                                   # миграция кланов (v12.4: ссылка группы)
        _conn.execute("ALTER TABLE clans ADD COLUMN link TEXT")
    except sqlite3.OperationalError:
        pass
    for col, dflt in [("stock", "INTEGER DEFAULT 0"),          # v12: лимит-товары
                      ("sold", "INTEGER DEFAULT 0"),
                      ("until_ts", "INTEGER DEFAULT 0"),      # v12: сезонные коллекции
                      ("price_step", "INTEGER DEFAULT 0")]:   # v12: рост цены за штуку, %
        try:
            _conn.execute(f"ALTER TABLE items ADD COLUMN {col} {dflt}")
        except sqlite3.OperationalError:
            pass
    _conn.executescript(SCHEMA_ECON)
    # v12.5: одноразовый пересчёт уровней из сожжённой Голды для старой базы
    for r in q("SELECT id, spent FROM users"):
        lv = level_from_spent(r["spent"])
        ex("UPDATE users SET level=? WHERE id=?", (lv, r["id"]))
    # v12.9: одноразовая чистка мусорных кланов из старого флоу тестирования
    # (клан «/admin»: имя со слэшем и ноль участников). Владельцу возвращаем
    # потраченные на создание 1000 G — клан сгорел по нашей вине, не его.
    junk = q("SELECT id, owner FROM clans WHERE name LIKE '/%' "
             "AND id NOT IN (SELECT DISTINCT clan_id FROM clan_members)")
    for r in junk:
        ex("DELETE FROM clans WHERE id=?", (r["id"],))
        if r["owner"]:
            ex("UPDATE users SET gold = gold + ? WHERE id=?",
               (CLAN_CREATE_COST, r["owner"]))
    _conn.commit()


def q(sql, args=()):
    cur = _conn.execute(sql, args)
    return cur.fetchall()


def one(sql, args=()):
    cur = _conn.execute(sql, args)
    return cur.fetchone()


def ex(sql, args=()):
    global _conn
    try:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur
    except sqlite3.OperationalError as e:
        # git мог подменить файл базы под живым соединением (readonly/
        # stale inode): один переоткрытие и повтор. Если не помогло — падаем.
        logging.warning("db reopen after %s", e)
        try:
            _conn.close()
        except Exception:
            pass
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        _conn.execute("PRAGMA journal_mode=DELETE")
        _conn.row_factory = sqlite3.Row
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur


# ---------- users ----------

def get_user(uid):
    return one("SELECT * FROM users WHERE id=?", (uid,))


def upsert_user(uid, username):
    ex(
        "INSERT INTO users(id, username, joined) VALUES(?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username",
        (uid, username, int(time.time())),
    )


def set_ref(uid, ref):
    """Записать пригласившего. True — записан (первый раз), False — уже был/повтор."""
    cur = _conn.execute(
        "UPDATE users SET ref_by=? WHERE id=? AND ref_by IS NULL", (ref, uid)
    )
    _conn.commit()
    return bool(cur.rowcount)


def add_gold(uid, amount, kind=None):
    ex("UPDATE users SET gold = gold + ? WHERE id=?", (amount, uid))
    if kind:
        log_econ(uid, amount, kind)


def take_gold(uid, amount):
    """Списание админом: не ниже нуля. Возвращает новый баланс или None."""
    ex("UPDATE users SET gold = MAX(0, gold - ?) WHERE id=?", (amount, uid))
    r = one("SELECT gold FROM users WHERE id=?", (uid,))
    return r["gold"] if r else None


def take_all_gold(amount=None):
    """v12.19: изъять amount G у каждого юзера (amount=None — всё).
    Сгорание из оборота (анти-инфляция). Возвращает (юзеров, всего G).
    spent/уровни НЕ растут — это изъятие, а не трата."""
    rows = q("SELECT id, gold FROM users WHERE gold > 0")
    tot, n = 0, 0
    for r in rows:
        take = r["gold"] if amount is None else min(r["gold"], amount)
        ex("UPDATE users SET gold = gold - ? WHERE id=?", (take, r["id"]))
        tot += take
        n += 1
    if tot:
        log_econ(0, -tot, "takeall")
    return n, tot

def reset_all_gold():
    """v12.19: полный сброс голды везде: балансы юзеров, клан-пул
    войны недели, джекпот. Возвращает словарь с былыми суммами."""
    ug = one("SELECT COALESCE(SUM(gold),0) AS s, COUNT(*) AS n FROM users WHERE gold > 0")
    cp, jp = clan_pool(), jackpot_pool()
    ex("UPDATE users SET gold = 0 WHERE gold > 0")
    put_meta("clan_pool", "0")
    put_meta("jackpot_pool", "0")
    tot = ug["s"] + cp + jp
    if tot:
        log_econ(0, -tot, "reset")
    return {"users": ug["n"], "gold": ug["s"], "clans": cp, "jackpot": jp}

def zero_gold(uid):
    """Полное обнуление баланса юзера. Возвращает прежний баланс или None."""
    r = one("SELECT gold FROM users WHERE id=?", (uid,))
    if not r:
        return None
    ex("UPDATE users SET gold = 0 WHERE id=?", (uid,))
    return r["gold"]


def set_ref_paid(uid):
    ex("UPDATE users SET ref_paid=1 WHERE id=?", (uid,))


def set_last_bonus(uid, ts):
    ex("UPDATE users SET last_bonus=? WHERE id=?", (ts, uid))


def claim_bonus(uid, ts, wait_sec, amount):
    """Атомарный бонус: проверка окна + начисление одним UPDATE.
    Двойной параллельный клик даёт True только одному вызову."""
    cur = ex("UPDATE users SET last_bonus=?, gold=gold+? "
             "WHERE id=? AND last_bonus+?<=?", (ts, amount, uid, wait_sec, ts))
    _conn.commit()
    if cur.rowcount > 0:
        log_econ(uid, amount, "bonus")
    return cur.rowcount > 0


def invited_count(uid):
    return one("SELECT COUNT(*) c FROM users WHERE ref_by=?", (uid,))["c"]


def all_user_ids():
    return [r["id"] for r in q("SELECT id FROM users")]


def stats():
    u = one("SELECT COUNT(*) c, COALESCE(SUM(gold),0) g FROM users")
    r = one("SELECT COUNT(*) c FROM requests WHERE status='pending'")
    return u["c"], u["g"], r["c"]


# ---------- tasks ----------

def add_promo(code, amount, max_uses=0):
    ex("INSERT OR IGNORE INTO promos(code, amount, max_uses) VALUES(?,?,?)",
       (code, amount, max_uses))


def promos():
    return q("SELECT * FROM promos ORDER BY id DESC")


def set_promo(pid, active):
    ex("UPDATE promos SET active=? WHERE id=?", (1 if active else 0, pid))


def del_promo(pid):
    ex("DELETE FROM promos WHERE id=?", (pid,))
    ex("DELETE FROM promo_used WHERE promo_id=?", (pid,))


def redeem_promo(code, uid):
    """Активация промокода. Возвращает (статус, сумма).
    Статусы: ok · bad · inactive · used · limit."""
    r = one("SELECT * FROM promos WHERE code=?", (code.strip().upper(),))
    if not r:
        return "bad", 0
    if not r["active"]:
        return "inactive", r["amount"]
    if one("SELECT 1 FROM promo_used WHERE promo_id=? AND user_id=?",
           (r["id"], uid)):
        return "used", r["amount"]
    if r["max_uses"] and r["uses"] >= r["max_uses"]:
        return "limit", r["amount"]
    ex("INSERT OR IGNORE INTO promo_used VALUES(?,?)", (r["id"], uid))
    ex("UPDATE promos SET uses=uses+1 WHERE id=?", (r["id"],))
    add_gold(uid, r["amount"], "promo")
    return "ok", r["amount"]


def all_group_ids():
    return [g["chat_id"] for g in
            q("SELECT chat_id FROM groups WHERE active=1")]


def add_task(channel, reward):
    ex("INSERT INTO tasks(channel, reward) VALUES(?,?)", (channel, reward))


def del_task(tid):
    ex("UPDATE tasks SET active=0 WHERE id=?", (tid,))


def tasks():
    return q("SELECT * FROM tasks WHERE active=1 ORDER BY id")


def get_task(tid):
    return one("SELECT * FROM tasks WHERE id=?", (tid,))


def is_done(uid, tid):
    return one("SELECT done FROM user_tasks WHERE user_id=? AND task_id=?", (uid, tid)) is not None


def try_claim_task(uid, tid):
    """Атомарный клейм: True только первому параллельному вызову (PK защитит от race).
    RETURNING: при конфликте INSERT тихо пропускается и строк не возвращает."""
    cur = _conn.execute(
        "INSERT INTO user_tasks(user_id, task_id, done) VALUES(?,?,1) "
        "ON CONFLICT(user_id, task_id) DO NOTHING RETURNING user_id", (uid, tid))
    got = cur.fetchone()
    _conn.commit()
    return got is not None


def process_payment_idempotent(charge_id, uid, gold_amt):
    """Stars: зачисляем только новый charge_id, повторы Telegram молча игнорируем."""
    try:
        ex("INSERT INTO payments(charge_id, user_id, amount, ts) VALUES(?,?,?,?)",
           (charge_id, uid, gold_amt, int(time.time())))
        ex("UPDATE users SET gold = gold + ? WHERE id=?", (gold_amt, uid))
        log_econ(uid, gold_amt, "topup")
        return True
    except sqlite3.IntegrityError:
        return False


def pay_ref_bonus(uid, ref_by, amount):
    """Атомарная выплата реферального бонуса: True только раз в жизни юзера.
    UPDATE с условием ref_paid=0 сам атомарен: гонка проигрывает второму вызову."""
    cur = _conn.execute("UPDATE users SET ref_paid=1 WHERE id=? AND ref_by=? AND ref_paid=0",
                        (uid, ref_by))
    _conn.commit()
    changed = _conn.total_changes
    if cur.rowcount:                     # rowcount UPDATE надёжен
        add_gold(ref_by, amount)
    return bool(cur.rowcount)



# ---------- экономика v12: леджер, сжигание, уровни, кейсы, дуэли,
# джекпот, кланы, лимит-товары, обмен G→Stars, витрина ----------

def log_econ(uid, delta, kind):
    ex("INSERT INTO ledger(ts, user_id, delta, kind) VALUES(?,?,?,?)",
       (int(time.time()), uid, delta, kind))

def econ_report():
    """Отчёт по потокам голды: kinds за сегодня / неделю / всё время."""
    def by(kind=None, since=0):
        if kind:
            r = one("SELECT COALESCE(SUM(delta),0) s, COUNT(*) c FROM ledger "
                    "WHERE kind=? AND ts>=?", (kind, since))
        else:
            r = one("SELECT COALESCE(SUM(delta),0) s, COUNT(*) c FROM ledger "
                    "WHERE ts>=?", (since,))
        return r["s"], r["c"]
    def kinds(since):
        return {r["kind"]: r["s"] for r in
                q("SELECT kind, SUM(delta) s FROM ledger WHERE ts>=? "
                  "GROUP BY kind ORDER BY s", (since,))}
    today0 = int(time.time()) // 86400 * 86400
    week0 = int(time.time()) - 7 * 86400
    return kinds(today0), kinds(week0), kinds(0)

# --- сжигание: списание + учёт spent (витрина богатства) ---

def burn_gold(uid, amount, kind):
    """Атомарное сожжение: True если хватило. amount уходит из экономики,
    spent растёт (для «Витрины богатства»)."""
    cur = ex("UPDATE users SET gold = gold - ?, spent = spent + ? "
             "WHERE id = ? AND gold >= ?", (amount, amount, uid, amount))
    if cur.rowcount == 1:
        relevel(uid)                      # v12.5: уровень растёт от сожжения
        log_econ(uid, -amount, kind)
        return True
    return False

def top_spent(limit=10):
    """Витрина богатства: кто больше всего сжёг голды на статус."""
    return q("SELECT id, username, spent, level FROM users "
             "WHERE spent > 0 ORDER BY spent DESC, id ASC LIMIT ?", (limit,))

# --- уровни (косметика и престиж, не голда) ---

# v12.4: лестница до 10 ступеней — 10-й уровень открывает создание клана
# v12.5: уровни считаются ПО СОЖЖЁННОЙ Голде (users.spent), до 100-го.
# Порог уровня N: сожжено 100 x N^2 Голды — 1-й: 100, 10-й: 10к,
# 50-й: 250к, 100-й: 1М. Растут автоматически при каждом сожжении.
LEVEL_BURN = [100 * n * n for n in range(1, 101)]
LEVEL_MAX = 100

def level_from_spent(spent):
    """Уровень по суммарно сожжённой Голде: наибольший N с порогом <= spent."""
    spent = int(spent or 0)
    lv = 0
    for i, th in enumerate(LEVEL_BURN):
        if spent >= th:
            lv = i + 1
        else:
            break
    return lv

def relevel(uid):
    """Пересчёт уровня из spent — после сожжения или возврата."""
    u = one("SELECT spent, level FROM users WHERE id=?", (uid,))
    if not u:
        return
    lv = level_from_spent(u["spent"])
    if lv != (u["level"] or 0):
        ex("UPDATE users SET level=? WHERE id=?", (lv, uid))

# --- кейсы (голда сгорает, дубликат возвращает лишь пятую часть) ---

CASE_COST = 500
CASE_DUP_REFUND = 100
CASE_POOL = [  # (kind, value, rarity, вес)
    ("frame", "Золотая рамка в топе", "common", 30),
    ("frame", "Рамка «Алмаз» в топе", "rare", 10),
    ("frame", "Рамка «Корона» в топе", "epic", 3),
    ("color", "Цвет имени «Изумруд»", "common", 25),
    ("color", "Цвет имени «Рубин»", "rare", 12),
    ("color", "Цвет имени «Аметист»", "epic", 4),
    ("title", "Титул «Легенда Funapp»", "legend", 1),
]

def has_cosmetic(uid, kind, value):
    return bool(one("SELECT id FROM cosmetics WHERE user_id=? AND kind=? AND value=?",
                    (uid, kind, value)))

def add_cosmetic(uid, kind, value, rarity):
    ex("INSERT INTO cosmetics(user_id, kind, value, rarity, created) VALUES(?,?,?,?,?)",
       (uid, kind, value, rarity, int(time.time())))

def user_cosmetics(uid):
    return q("SELECT * FROM cosmetics WHERE user_id=? ORDER BY id DESC", (uid,))

def open_case(uid):
    """Открывает кейс: -CASE_COST, случайная косметика по весам.
    Дубликат не дублируется: вместо него +CASE_DUP_REFUND (20% стоимости).
    Возвращает ('new'|'dup'|'poor', item, refund)."""
    if not burn_gold(uid, CASE_COST, "case"):
        return "poor", None, 0
    total_w = sum(it[3] for it in CASE_POOL)
    pick = random.randrange(total_w)
    acc = 0
    for it in CASE_POOL:
        acc += it[3]
        if pick < acc:
            kind, value, rarity = it[0], it[1], it[2]
            break
    if has_cosmetic(uid, kind, value):
        add_gold(uid, CASE_DUP_REFUND, "case_refund")
        return "dup", (kind, value, rarity), CASE_DUP_REFUND
    add_cosmetic(uid, kind, value, rarity)
    return "new", (kind, value, rarity), 0

# --- дуэли (голда ходит между игроками, 5% банка -> джекпот) ---

DUEL_FEE_PCT = 5

def create_duel(chat_id, game, stake, p1):
    cur = ex("INSERT INTO duels(chat_id, game, stake, p1, created) "
             "VALUES(?,?,?,?,?)", (chat_id, game, stake, p1, int(time.time())))
    return cur.lastrowid

def get_duel(did):
    return one("SELECT * FROM duels WHERE id=?", (did,))

def duel_accept(did, p2):
    """Атомарный приём дуэли: оба игрока платят ставку в банк одной транзакцией."""
    d = get_duel(did)
    if not d or d["status"] != "open" or d["p1"] == p2:
        return False
    with TX_LOCK:
        try:
            _conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            pass
        try:
            for uid in (d["p1"], p2):
                cur = _conn.execute(
                    "UPDATE users SET gold = gold - ? WHERE id = ? AND gold >= ?",
                    (d["stake"], uid, d["stake"]))
                if cur.rowcount != 1:
                    try: _conn.execute("ROLLBACK")
                    except Exception: pass
                    return False
            _conn.execute("UPDATE duels SET p2=?, status='accepted' WHERE id=?", (p2, did))
            _conn.commit()
        except Exception:
            try: _conn.execute("ROLLBACK")
            except Exception: pass
            return False
    log_econ(d["p1"], -d["stake"], "duel")
    log_econ(p2, -d["stake"], "duel")
    return True

def duel_resolve(did, winner):
    """Победитель забирает банк минус 5% в джекпот. loser получает ничего."""
    d = get_duel(did)
    if not d or d["status"] != "accepted":
        return None
    pot = d["stake"] * 2
    fee = pot * DUEL_FEE_PCT // 100
    ex("UPDATE duels SET status='done', winner=? WHERE id=?", (winner, did))
    add_gold(winner, pot - fee, "duel_win")
    jackpot_add(fee)
    return pot, fee

def open_duels(chat_id):
    return q("SELECT * FROM duels WHERE chat_id=? AND status='open' "
             "ORDER BY id DESC LIMIT 5", (chat_id,))

# --- глобальный джекпот (1% каждой ставки, тираж раз в сутки) ---

def jackpot_pool():
    try:
        return int(get_meta("jackpot_pool") or 0)
    except (TypeError, ValueError):
        return 0

def jackpot_add(amount):
    if amount <= 0:
        return
    ex("INSERT OR REPLACE INTO meta(key, value) VALUES('jackpot_pool', "
       "COALESCE((SELECT value FROM meta WHERE key='jackpot_pool'), 0) + ?)", (amount,))
    _conn.commit()

def jackpot_set_pool(v):
    put_meta("jackpot_pool", str(v))

def jackpot_active_players(since_ts):
    return [r["user_id"] for r in
            q("SELECT DISTINCT user_id FROM ledger "
              "WHERE kind IN ('stake', 'duel') AND ts>=? "
              "ORDER BY user_id", (since_ts,))]

# --- кланы (создание сжигает голду, взнос идёт в недельный банк войны) ---

CLAN_CREATE_COST = 1000
CLAN_JOIN_COST = 100

def clan_create(uid, name, link=None):
    """Создание клана: -1000 (сгорает). Создатель становится членом.
    v12.4: только с 10-го уровня профиля; имя = имя группы по ссылке."""
    name = (name or "").strip()
    if not (3 <= len(name) <= 24):
        return "Имя клана: от 3 до 24 символов"
    u = get_user(uid)
    if not u or (u["level"] or 0) < 10:
        lv = (u["level"] or 0) if u else 0
        return f"Создание клана — с 10-го уровня профиля (твой: {lv})"
    if one("SELECT user_id FROM clan_members WHERE user_id=?", (uid,)):
        return "Сначала выйди из текущего клана"
    if not burn_gold(uid, CLAN_CREATE_COST, "clan"):
        return f"Не хватает Голды: создание клана стоит {CLAN_CREATE_COST}"
    try:
        cur = ex("INSERT INTO clans(name, owner, link, created) VALUES(?,?,?,?)",
                 (name, uid, link, int(time.time())))
    except sqlite3.IntegrityError:
        ex("UPDATE users SET spent = spent - ? WHERE id=?",
           (CLAN_CREATE_COST, uid))          # откат витрины тоже
        relevel(uid)                          # v12.5: уровень тоже откатывается
        add_gold(uid, CLAN_CREATE_COST, "clan_refund")
        return "Клан с таким именем уже есть"
    ex("INSERT OR REPLACE INTO clan_members(user_id, clan_id, joined) VALUES(?,?,?)",
       (uid, cur.lastrowid, int(time.time())))
    return None

def clan_join(uid, clan_id):
    """Вступление: взнос 100 идёт в недельный банк войны кланов."""
    if one("SELECT user_id FROM clan_members WHERE user_id=?", (uid,)):
        return "Ты уже в клане"
    clan = one("SELECT id FROM clans WHERE id=?", (clan_id,))
    if not clan:
        return "Клан не найден"
    if not burn_gold(uid, CLAN_JOIN_COST, "clanfee"):
        return f"Не хватает Голды: взнос за вход {CLAN_JOIN_COST}"
    ex("INSERT OR REPLACE INTO clan_members(user_id, clan_id, joined) VALUES(?,?,?)",
       (uid, clan_id, int(time.time())))
    pool = int(get_meta("clan_pool") or 0) + CLAN_JOIN_COST
    put_meta("clan_pool", str(pool))
    return None

def clan_leave(uid):
    ex("DELETE FROM clan_members WHERE user_id=?", (uid,))

def next_clan_no():
    """Номер для автоимени приватного клана (ссылка t.me/+код)."""
    return one("SELECT COALESCE(MAX(id),0)+1 AS n FROM clans")["n"]

def clan_delete(uid):
    """v12.4: удаление клана владельцем. Участники выходят, 1000 G
    за создание не возвращаются (сожжённая экономика)."""
    c = one("SELECT id, name FROM clans WHERE owner=?", (uid,))
    if not c:
        return "Удалить клан может только его владелец"
    ex("DELETE FROM clan_members WHERE clan_id=?", (c["id"],))
    ex("DELETE FROM clans WHERE id=?", (c["id"],))
    return None

def my_clan(uid):
    return one("SELECT c.*, "
               "(SELECT COUNT(*) FROM clan_members m WHERE m.clan_id=c.id) members "
               "FROM clans c JOIN clan_members m ON m.clan_id=c.id "
               "WHERE m.user_id=?", (uid,))

def clans_top(limit=10, since_ts=0):
    """Топ кланов: недельный счёт = сумма выигрышей членов (ledger gwin)."""
    return q(
        "SELECT c.id, c.name, c.owner, "
        "(SELECT COUNT(*) FROM clan_members m WHERE m.clan_id=c.id) members, "
        "COALESCE((SELECT SUM(l.delta) FROM ledger l "
        " JOIN clan_members m2 ON m2.user_id=l.user_id "
        " WHERE m2.clan_id=c.id AND l.kind='gwin' AND l.ts>=?), 0) score "
        "FROM clans c ORDER BY score DESC, c.id ASC LIMIT ?",
        (since_ts, limit))

def clan_pool():
    try:
        return int(get_meta("clan_pool") or 0)
    except (TypeError, ValueError):
        return 0

def clan_war_resolve(week0):
    """Финал недельной войны: топ-клан делит банк поровну. Возвращает
    (winner_clan, prize, members) или None."""
    rows = clans_top(20, week0)
    if not rows or clan_pool() < CLAN_JOIN_COST:
        return None
    winner = rows[0]
    if (winner["score"] or 0) <= 0:
        return None
    members = [r["user_id"] for r in
               q("SELECT user_id FROM clan_members WHERE clan_id=?", (winner["id"],))]
    if not members:
        return None
    prize = clan_pool()
    share = prize // len(members)
    rest = prize - share * len(members)
    for uid in members:
        add_gold(uid, share, "clanprize")
    if rest:
        add_gold(winner["owner"], rest, "clanprize")
    put_meta("clan_pool", "0")
    return winner, prize, members

# --- лимит-товары и сезонные коллекции ---

def item_price(it):
    """Цена растёт на price_step% за каждую проданную штуку (дефицит)."""
    base = it["cost"]
    if it["price_step"]:
        for _ in range(it["sold"] or 0):
            base = base * (100 + it["price_step"]) // 100
    return base

def items_seasonal():
    """Активные товары: только неистёкшие (сезонные коллекции)."""
    now = int(time.time())
    return q("SELECT * FROM items WHERE active=1 AND (until_ts=0 OR until_ts>?) "
             "ORDER BY id", (now,))

def buy_item_limited(uid, it):
    """Покупка с учётом лимита и растущей цены. Возвращает (ok, price, reason)."""
    if it["stock"] and (it["sold"] or 0) >= it["stock"]:
        return False, 0, "Товар распродан"
    price = item_price(it)
    if not burn_gold(uid, price, "shop"):
        return False, price, "Не хватает Голды"
    cur = ex("UPDATE items SET sold = sold + 1 WHERE id = ? "
             "AND (stock = 0 OR sold < stock)", (it["id"],))
    if cur.rowcount != 1:                  # кто-то успел раньше — возврат
        add_gold(uid, price, "shop_refund")
        return False, price, "Товар распродан"
    return True, price, None

# --- обмен Голды на Stars (курс задаёт админ, Stars шлёт админ вручную) ---

def stars_rate():
    try:
        return int(get_meta("stars_rate") or 10000)
    except (TypeError, ValueError):
        return 10000

def set_stars_rate(rate):
    """v12.6: курс публичный — каждое изменение пишется в rate_log
    (история видна игрокам на экране обмена и админу в CRM)."""
    rate = int(rate)
    put_meta("stars_rate", str(rate))
    ex("INSERT INTO rate_log(rate, ts) VALUES(?,?)", (rate, int(time.time())))

def rate_history(limit=5):
    """Последние изменения курса (новые первыми)."""
    if one("SELECT 1 FROM rate_log LIMIT 1") is None:
        ex("INSERT INTO rate_log(rate, ts) VALUES(?,?)",
           (stars_rate(), int(time.time())))
    return q("SELECT rate, ts FROM rate_log ORDER BY id DESC LIMIT ?", (limit,))

def my_exchanges(uid):
    """Заявки игрока на обмен (новые первыми)."""
    return q("SELECT * FROM exchanges WHERE user_id=? ORDER BY id DESC LIMIT 5",
             (uid,))

def create_exchange(uid, stars):
    """Сжигает голду по курсу, создаёт заявку на Stars.
    Возвращает (ok, gold, reason)."""
    rate = stars_rate()
    gold = rate * stars
    if not burn_gold(uid, gold, "exchange"):
        return False, gold, f"Не хватает Голды: {stars} Stars = {gold} G"
    cur = ex("INSERT INTO exchanges(user_id, gold, stars, created) "
             "VALUES(?,?,?,?)", (uid, gold, stars, int(time.time())))
    return True, gold, None

def pending_exchanges():
    return q("SELECT * FROM exchanges WHERE status='pending' ORDER BY id")

def done_exchange(eid):
    ex("UPDATE exchanges SET status='done' WHERE id=?", (eid,))

def decline_exchange(eid):
    e = one("SELECT * FROM exchanges WHERE id=? AND status='pending'", (eid,))
    if e:
        ex("UPDATE exchanges SET status='declined' WHERE id=?", (eid,))
        add_gold(e["user_id"], e["gold"], "exchange_refund")
    return e

def wal_checkpoint():
    """Сshedule: проталкиваем WAL в главный файл перед git add (снимок полный)."""
    try:
        _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass


def prune_old():
    """Чистим растущие логи, иначе база разрастётся и Actions умрёт."""
    ex("DELETE FROM updates_log WHERE ts < ?", (int(time.time()) - 7 * 86400,))
    ex("DELETE FROM game_stats WHERE ts < ?", (int(time.time()) - 30 * 86400,))


# ---------- shop ----------

def add_item(title, cost, stock=0, days=0, step=0):
    """v12: опционально шт=N (лимит), дней=N (сезонная коллекция),
    рост=N (цена +N% за каждую проданную штуку)."""
    until = int(time.time()) + days * 86400 if days else 0
    cur = ex("INSERT INTO items(title, cost, stock, until_ts, price_step) "
             "VALUES(?,?,?,?,?)", (title, cost, stock, until, step))
    return cur.lastrowid


def del_item(iid):
    ex("UPDATE items SET active=0 WHERE id=?", (iid,))


def items():
    return q("SELECT * FROM items WHERE active=1 ORDER BY id")


def get_item(iid):
    return one("SELECT * FROM items WHERE id=?", (iid,))


def add_request(uid, iid):
    cur = ex(
        "INSERT INTO requests(user_id, item_id, created) VALUES(?,?,?)",
        (uid, iid, int(time.time())),
    )
    return cur.lastrowid


def pending_requests():
    return q("SELECT r.*, i.title FROM requests r JOIN items i ON i.id=r.item_id "
             "WHERE r.status='pending' ORDER BY r.id")


def decline_request(rid):
    r = one("SELECT r.*, i.title, i.cost FROM requests r JOIN items i ON i.id=r.item_id "
            "WHERE r.id=? AND r.status='pending'", (rid,))
    if r:
        ex("UPDATE requests SET status='declined' WHERE id=?", (rid,))
        add_gold(r["user_id"], r["cost"])      # отказ = возврат списанной голды
    return r


def done_request(rid):
    r = one("SELECT r.*, i.title FROM requests r JOIN items i ON i.id=r.item_id "
            "WHERE r.id=? AND r.status='pending'", (rid,))
    if r:
        ex("UPDATE requests SET status='done' WHERE id=?", (rid,))
    return r


# ---------- банк/розыгрыши ----------

def top_users(limit=20):
    """Топ по балансу голды (для розыгрыша банка и кнопки Топ)."""
    return q(
        "SELECT id, username, gold FROM users ORDER BY gold DESC, joined ASC LIMIT ?",
        (limit,),
    )


def add_giveaway(amount, players, winners_json):
    ex("INSERT INTO giveaways(amount, players, winners) VALUES(?,?,?)",
       (amount, players, winners_json))


def last_giveaway():
    return one("SELECT * FROM giveaways ORDER BY id DESC LIMIT 1")

def add_stat(game, value):
    """Логирует исход игры на нативной анимации — для будущей калибровки выплат."""
    ex("INSERT INTO game_stats(game, value, ts) VALUES(?,?,?)",
       (game, int(value), int(time.time())))

# ---------- переводы между юзерами (reply в группах) ----------

TX_LOCK = __import__("threading").Lock()

def transfer(from_id, to_id, amount, chat_id):
    """Атомарный перевод (v11.46): списание + начисление + журнал одной
    транзакцией BEGIN IMMEDIATE. Возвращает (ok, баланс отправителя)."""
    u = one("SELECT gold FROM users WHERE id=?", (from_id,))
    bal = u["gold"] if u else 0
    if amount < 1 or from_id == to_id:
        return False, bal
    with TX_LOCK:
        try:
            _conn.execute("BEGIN IMMEDIATE")   # НЕ через ex(): он делает commit
        except sqlite3.OperationalError:
            pass                       # уже в транзакции того же соединения
        try:
            row = one("SELECT gold FROM users WHERE id=?", (from_id,))
            if not row or row["gold"] < amount:
                try: _conn.execute("ROLLBACK")
                except Exception: pass
                return False, (row["gold"] if row else 0)
            _conn.execute("UPDATE users SET gold=gold-? WHERE id=?", (amount, from_id))
            cur = _conn.execute("UPDATE users SET gold=gold+? WHERE id=?", (amount, to_id))
            if cur.rowcount != 1:      # получатель исчез — откатываем всё
                try: _conn.execute("ROLLBACK")
                except Exception: pass
                return False, row["gold"]
            _conn.execute("INSERT INTO transfers(from_id,to_id,amount,chat_id) "
                          "VALUES(?,?,?,?)", (from_id, to_id, amount, chat_id))
            _conn.execute("INSERT INTO ledger(ts,user_id,delta,kind) VALUES "
                          "(?,?,?,?),(?,?,?,?)",
                          (int(time.time()), from_id, -amount, "transfer_out",
                           int(time.time()), to_id, amount, "transfer_in"))
            _conn.commit()
            return True, row["gold"] - amount
        except Exception:
            try: _conn.execute("ROLLBACK")
            except Exception: pass
            raise

def recent_transfers(limit=10):
    return q("SELECT t.*, fu.username AS fu, tu.username AS tu, g.title AS chat "
             "FROM transfers t "
             "LEFT JOIN users fu ON fu.id=t.from_id LEFT JOIN users tu ON tu.id=t.to_id "
             "LEFT JOIN groups g ON g.chat_id=t.chat_id "
             "ORDER BY t.id DESC LIMIT ?", (limit,))

def user_transfers(uid, limit=15):
    return q("SELECT t.*, fu.username AS fu, tu.username AS tu, g.title AS chat "
             "FROM transfers t "
             "LEFT JOIN users fu ON fu.id=t.from_id LEFT JOIN users tu ON tu.id=t.to_id "
             "LEFT JOIN groups g ON g.chat_id=t.chat_id "
             "WHERE t.from_id=? OR t.to_id=? ORDER BY t.id DESC LIMIT ?",
             (uid, uid, limit))

def chat_tx_setting(chat_id):
    """(включено ли, порог подтверждения). Чат вне базы — дефолт (1, 0)."""
    r = one("SELECT tx_on, tx_confirm FROM groups WHERE chat_id=?", (chat_id,))
    return (r["tx_on"], r["tx_confirm"]) if r else (1, 0)

def tx_toggle(chat_id):
    ex("UPDATE groups SET tx_on = 1 - tx_on WHERE chat_id=?", (chat_id,))

def tx_set_confirm(chat_id, n):
    ex("UPDATE groups SET tx_confirm=? WHERE chat_id=?", (n, chat_id))

def log_update(chat_id, chat_type, user_id, text):
    """Лог доставленных боту сообщений — диагностика privacy mode в группах."""
    ex("INSERT INTO updates_log(chat_id, chat_type, user_id, text, ts) VALUES(?,?,?,?,?)",
       (chat_id, chat_type, user_id, text, int(time.time())))

def balance(uid):
    r = q("SELECT gold FROM users WHERE id=?", (uid,))
    return r[0]["gold"] if r else 0


def charge_gold(uid, amount, kind=None):
    """Атомарное списание одной SQL-командой: True если хватило и списалось."""
    cur = _conn.execute(
        "UPDATE users SET gold = gold - ? WHERE id = ? AND gold >= ?",
        (amount, uid, amount))
    _conn.commit()
    if cur.rowcount == 1 and kind:
        log_econ(uid, -amount, kind)
    return cur.rowcount == 1


def track_game(uid, win, net):
    """Статистика профиля: сыграно/победы/поражения/общий выигрыш."""
    ex("UPDATE users SET games_played = games_played + 1, "
       "games_won = games_won + ?, games_lost = games_lost + ?, "
       "total_won = total_won + ? WHERE id = ?",
       (1 if win else 0, 0 if win else 1, max(net, 0), uid))

# ---------- мульти-группы ----------

_GROUP_DEFAULTS = {"chat_id": 0, "title": "", "added_by": None, "added_at": 0,
                    "active": 1, "games_on": 1, "min_stake": 1,
                    "max_stake": 0, "flood_sec": 3}


def upsert_group(chat_id, title, added_by):
    ex("INSERT INTO groups(chat_id, title, added_by, added_at, active) VALUES(?,?,?,?,1) "
       "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, added_by=excluded.added_by, active=1",
       (chat_id, title, added_by, int(time.time())))


def deactivate_group(chat_id):
    ex("UPDATE groups SET active=0 WHERE chat_id=?", (chat_id,))


def get_group(chat_id):
    r = q("SELECT * FROM groups WHERE chat_id=?", (chat_id,))
    if r:
        return r[0]
    g = dict(_GROUP_DEFAULTS)
    g["chat_id"] = chat_id
    return g


GROUP_COLS = {"title", "added_by", "active", "games_on", "min_stake", "max_stake", "flood_sec"}


def set_group(chat_id, **kw):
    ex("INSERT INTO groups(chat_id) VALUES(?) ON CONFLICT(chat_id) DO NOTHING", (chat_id,))
    kw = {k: v for k, v in kw.items() if k in GROUP_COLS}   # только известные колонки
    if kw:
        keys = ", ".join(f"{k}=?" for k in kw)
        ex(f"UPDATE groups SET {keys} WHERE chat_id=?", (*kw.values(), chat_id))


def track_group_game(chat_id, uid, win, net):
    """Статистика отдельно для каждой группы (общий профиль — в users)."""
    ex("INSERT INTO group_stats(chat_id, user_id, played, won, lost, total_won) "
       "VALUES(?,?,1,?,?,?) ON CONFLICT(chat_id, user_id) DO UPDATE SET "
       "played=played+1, won=won+?, lost=lost+?, total_won=total_won+?",
       (chat_id, uid, 1 if win else 0, 0 if win else 1, max(net, 0),
        1 if win else 0, 0 if win else 1, max(net, 0)))


def group_top(chat_id, limit=10):
    return q("SELECT s.user_id, s.total_won, s.won, s.played, u.username, u.gold "
             "FROM group_stats s JOIN users u ON u.id=s.user_id "
             "WHERE s.chat_id=? ORDER BY s.total_won DESC, s.won DESC LIMIT ?",
             (chat_id, limit))

def del_meta(key):
    ex("DELETE FROM meta WHERE key=?", (key,))


def get_meta(key):
    r = one("SELECT value FROM meta WHERE key=?", (key,))
    return r["value"] if r else None


def put_meta(key, value):
    """Служебные метки (сердцебиение, состояние) — на вид снаружи, через коммиты bot.db."""
    ex("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
