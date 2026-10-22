import logging
import os
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

CREATE TABLE IF NOT EXISTS groups(
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    added_by INTEGER,
    added_at INTEGER,
    active INTEGER DEFAULT 1,
    games_on INTEGER DEFAULT 1,
    min_stake INTEGER DEFAULT 1,
    max_stake INTEGER DEFAULT 0,
    flood_sec INTEGER DEFAULT 3
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

CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS game_stats(
    game TEXT,
    value INTEGER,
    ts INTEGER
);"""


def init():
    global _conn
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=DELETE")
    _conn.executescript(SCHEMA)
    for col, dflt in [("games_played", "INTEGER DEFAULT 0"),
                      ("games_won", "INTEGER DEFAULT 0"),
                      ("games_lost", "INTEGER DEFAULT 0"),
                      ("total_won", "INTEGER DEFAULT 0")]:
        try:                                   # миграция старой базы
            _conn.execute(f"ALTER TABLE users ADD COLUMN {col} {dflt}")
        except sqlite3.OperationalError:
            pass
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


def add_gold(uid, amount):
    ex("UPDATE users SET gold = gold + ? WHERE id=?", (amount, uid))


def take_gold(uid, amount):
    """Списание админом: не ниже нуля. Возвращает новый баланс или None."""
    ex("UPDATE users SET gold = MAX(0, gold - ?) WHERE id=?", (amount, uid))
    r = one("SELECT gold FROM users WHERE id=?", (uid,))
    return r["gold"] if r else None


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

def add_item(title, cost):
    cur = ex("INSERT INTO items(title, cost) VALUES(?,?)", (title, cost))
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

def log_update(chat_id, chat_type, user_id, text):
    """Лог доставленных боту сообщений — диагностика privacy mode в группах."""
    ex("INSERT INTO updates_log(chat_id, chat_type, user_id, text, ts) VALUES(?,?,?,?,?)",
       (chat_id, chat_type, user_id, text, int(time.time())))

def balance(uid):
    r = q("SELECT gold FROM users WHERE id=?", (uid,))
    return r[0]["gold"] if r else 0


def charge_gold(uid, amount):
    """Атомарное списание одной SQL-командой: True если хватило и списалось."""
    cur = _conn.execute(
        "UPDATE users SET gold = gold - ? WHERE id = ? AND gold >= ?",
        (amount, uid, amount))
    _conn.commit()
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

def put_meta(key, value):
    """Служебные метки (сердцебиение, состояние) — на вид снаружи, через коммиты bot.db."""
    ex("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
