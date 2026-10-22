"""Списание и обнуление баланса админом (v11.28)."""
import os, sys
os.environ.setdefault("BOT_TOKEN", "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
os.environ.setdefault("ADMIN_IDS", "111")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db


def setup_function():
    db.DB_PATH = "/tmp/take_test.db"
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init()
    db.ex("INSERT INTO users(id, username, gold, joined) VALUES(?, ?, ?, ?)",
          (1, "u1", 500, 0))


def test_take_gold_normal():
    assert db.take_gold(1, 200) == 300
    assert db.get_user(1)["gold"] == 300


def test_take_gold_clamps_at_zero():
    assert db.take_gold(1, 9999) == 0
    assert db.get_user(1)["gold"] == 0        # минусового баланса не бывает


def test_zero_gold_returns_old_balance():
    db.ex("UPDATE users SET gold=777 WHERE id=1")
    old = db.zero_gold(1)
    assert old == 777
    assert db.get_user(1)["gold"] == 0


def test_take_gold_missing_user():
    assert db.take_gold(999, 10) is None
    assert db.zero_gold(999) is None
