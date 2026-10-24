"""v12.21: цветные кнопки (Bot API 9.4 style) — авто-цвет по данным."""
import os, sys, importlib.util
import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.update(BOT_TOKEN="1:test", PREMIUM_UI="1")


@pytest.fixture(scope="module")
def m():
    spec = importlib.util.spec_from_file_location("botmod", os.path.join(BASE, "bot.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["botmod"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_games_and_sections_primary(m):
    for data in ("games", "g:football", "g:slots", "cube:lo", "jackpot",
                 "tchk:5", "case", "clan", "lvl", "shop"):
        assert m.btn("X", data).style == "primary", data


def test_money_flows_danger(m):
    for data in ("refguide", "buy:3", "exch", "top"):
        assert m.btn("X", data).style == "danger", data


def test_claim_actions_success(m):
    for data in ("bonus:claim", "case:open", "exch:go", "clan:new", "duel:77",
                 "tasks", "profile", "bonus", "topup"):
        assert m.btn("X", data).style == "success", data


def test_navigation_all_colored(m):
    """v12.24: НЕЙТРАЛЬНЫХ КНОПОК БОЛЬШЕ НЕТ — вся навигация синяя."""
    for data in ("menu", "tx:ok", "sub", "refcopy", "a:users",
                 "gset:-100:1:2", "adm:cancel"):
        assert m.btn("X", data).style == "primary", data


def test_destructive_admin_ops_danger(m):
    for data in ("adm:reset", "adm:take:500", "adm:take:all"):
        assert m.btn("X", data).style == "danger", data


def test_url_and_inline_buttons_colored(m):
    b1 = m.btn("Играть", None, url="https://t.me/x")
    b2 = m.btn("Пригласить друга", None, switch_inline="invite")
    b3 = m.btn("Открыть Fun Games", "tx:none", url="https://t.me/x")
    for b in (b1, b2, b3):
        assert b.style in ("primary", "success", "danger")


def test_explicit_style_overrides(m):
    assert m.btn("X", "menu", style="danger").style == "danger"


def test_kb_serializes_style_with_icon(m):
    dump = m.kb([m.btn("Игры", "games", icon="games")]).model_dump()
    b0 = dump["inline_keyboard"][0][0]
    assert b0["style"] == "primary" and "icon_custom_emoji_id" in b0


def test_every_callback_button_valid_style(m):
    """Все кнопки бота валидны: style только primary/success/danger."""
    datas = ["games", "g:bowling", "topup", "shop", "bonus", "bonus:claim",
             "case:open", "exch", "exch:go", "clan", "clan:new", "menu",
             "profile", "tasks", "top", "jackpot", "refguide", "duel:1",
             "lvl", "tx:ok", "a:bc", "adm:take:500"]
    for d in datas:
        assert m.btn("X", d).style in ("primary", "success", "danger"), d
