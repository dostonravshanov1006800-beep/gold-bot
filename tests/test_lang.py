import os, sys, tempfile
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("PREMIUM_UI", "1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import i18n, bot


def test_uz_text_translates_core_ui():
    t = i18n.uz_text("Меню · Баланс: 500 Голды. Задания выполнено")
    assert "Menyu" in t and "Balans" in t and "Oltin" in t and "Vazifalar" in t
    assert "Голды" not in t and "Меню" not in t


def test_uz_text_keeps_html_and_numbers():
    t = i18n.uz_text('<b>Баланс</b>: <blockquote>Голда — 1000</blockquote>')
    assert "<b>" in t and "<blockquote>" in t and "1000" in t


def test_uz_text_phrases_first():
    assert "Hammasi qo'yilgan" in i18n.uz_text("Всё на кон!")
    assert "Kichik" in i18n.uz_text("Малые (1-3)")


def test_menu_kb_has_lang_button_and_uz_mode():
    kb = bot.menu_kb(0)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert "Til / Язык" in labels and "Справочник" in labels
    bot.UZ_CTX.set(True)
    try:
        kb2 = bot.menu_kb(0)
        labels2 = [b.text for row in kb2.inline_keyboard for b in row]
        assert "Меню" not in labels2
        assert any("Vazifalar" in x for x in labels2)
        assert "Til / Язык" in labels2
    finally:
        bot.UZ_CTX.set(False)


def test_set_lang_persists():
    dbfile = tempfile.mktemp(suffix=".db")
    bot.db.DB_PATH = dbfile
    bot.db.init()
    bot.db.upsert_user(77, "t")
    assert bot.db.get_user(77)["lang"] == "ru"
    bot.db.set_lang(77, "uz")
    assert bot.db.get_user(77)["lang"] == "uz"
    assert bot._uz(77) is True
    bot.db.set_lang(77, "ru")
    assert bot.db.get_user(77)["lang"] == "ru"
    assert bot._uz(77) is False
    os.unlink(dbfile)
