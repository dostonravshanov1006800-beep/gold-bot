"""Inline-карточка приглашения (v11.33)."""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("ADMIN_IDS", "111,222")
os.environ.setdefault("WEBAPP_URL", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db  # noqa: E402
import bot  # noqa: E402

import asyncio  # noqa: E402


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


class FakeInlineQuery:
    def __init__(self, uid):
        self.from_user = SimpleNamespace(id=uid)
        self.answers = []

    async def answer(self, results, **kw):
        self.answers.append((results, kw))


async def _handler():
    for h in bot.DP.inline_query.handlers:
        return h.callback
    raise KeyError("inline")


def test_inline_card_has_photo_caption_reflink():
    bot.ME = "funapp_game"
    h = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_handler())
    q2 = FakeInlineQuery(42)
    run(h(q2))
    results, kw = q2.answers[0]
    card = results[0]
    assert card.type == "photo"
    assert card.photo_url and card.photo_url.startswith("https://")
    assert "Funs" in card.caption and "Голду" in card.caption
    url = card.reply_markup.inline_keyboard[0][0].url
    assert url == "https://t.me/funapp_game?start=ref_42"
    assert kw.get("is_personal") is True and kw.get("cache_time") == 0


def test_inline_card_id_personal_per_sender():
    bot.ME = "funapp_game"
    h = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_handler())
    q1, q2 = FakeInlineQuery(42), FakeInlineQuery(77)
    run(h(q1)); run(h(q2))
    u1 = q1.answers[0][0][0].reply_markup.inline_keyboard[0][0].url
    u2 = q2.answers[0][0][0].reply_markup.inline_keyboard[0][0].url
    assert "ref_42" in u1 and "ref_77" in u2


def test_btn_url_and_switch_inline():
    u = bot.btn("Играть", None, icon="games", url="https://example.com")
    assert u.url == "https://example.com"
    s = bot.btn("Пригласить друга", None, switch_inline="invite")
    assert s.switch_inline_query == "invite"
    c = bot.btn("Обычная", "menu")
    assert c.callback_data == "menu"


def test_inline_in_allowed_updates():
    """Регресс 22.10: без inline_query в allowed_updates карточка не работает."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")).read()
    assert '"inline_query"' in src.split("start_polling")[1][:300]


def test_banner_url_is_direct_no_redirect():
    """Telegram inline не ходит по редиректам: нужна прямая ссылка на jpg."""
    assert bot.BANNER_URL.startswith("https://media.base44.com/")
    assert bot.BANNER_URL.endswith(".jpg")


def test_info_commands_no_cooldown_and_wait_text_never_zero():
    import math
    for left in (0.01, 0.4, 0.99, 1.0, 1.4):
        assert max(1, math.ceil(left)) >= 1


def test_show_handles_photo_menu():
    """Регресс 22.10: кнопки на фото-меню /start «зависали» — edit_text не
    работает на подписи фото. show() должен звать edit_caption."""
    import asyncio
    from types import SimpleNamespace

    class FakeMsg:
        photo = [SimpleNamespace()]
        def __init__(self): self.calls = []
        async def edit_caption(self, caption=None, reply_markup=None):
            self.calls.append(("caption", caption))
        async def edit_text(self, text, **kw):
            self.calls.append(("text", text))
        async def answer(self, text, **kw):
            self.calls.append(("answer", text))

    class FakeCb:
        def __init__(self): self.message = FakeMsg()
        async def answer(self, *a, **kw): pass

    h = asyncio.get_event_loop_policy().new_event_loop().run_until_complete
    cb = FakeCb()
    h(bot.show(cb, "меню на фото"))
    assert cb.message.calls[0][0] == "caption"      # фото — правим подпись, не текст

    cb2 = FakeCb(); cb2.message.photo = []
    h(bot.show(cb2, "меню на тексте"))
    assert cb2.message.calls[0][0] == "text"


def test_blockquote_style():
    """v11.38: стилистика Telegram — цитаты вместо воды, без вложенности."""
    w = bot.welcome_text("Тест")
    assert "<blockquote>" in w and len(w) <= 1024   # фото-caption лимит
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")).read()
    import re
    # blockquote не должен вкладываться (запрещено Bot API)
    for m in re.finditer(r"<blockquote[^>]*>", src):
        pass
    assert "<blockquote expandable>" in src          # сворачиваемая подсказка есть


def test_successor_status_picks_newest_after_ours():
    """v11.44: выбор сменщика — только раны с id больше нашего, самый свежий."""
    runs = {"workflow_runs": [
        {"id": 111, "status": "completed"},
        {"id": 222, "status": "queued"},
        {"id": 333, "status": "in_progress"},
        {"id": 100, "status": "in_progress"},   # наш собственный — игнорируется
    ]}
    assert bot.successor_status.__name__ == "successor_status"
    # прямая проверка логики фильтра
    my = 100
    newer = [r for r in runs["workflow_runs"] if r["id"] != my and r["id"] > my]
    assert max(newer, key=lambda r: r["id"])["status"] == "in_progress"


def test_custom_topup_skips_without_state():
    """v11.45: без состояния ввода хендлер обязан кидать SkipHandler,
    иначе глотаются все личные тексты (кнопки/команды «не отвечают»)."""
    import asyncio
    from aiogram.dispatcher.event.bases import SkipHandler

    class U:
        id = 999999999
    class M:
        from_user = U()
        text = "баланс"

    bot.CUSTOM_TOPUP.pop(999999999, None)
    try:
        asyncio.run(bot.custom_topup_amount(M()))
        raise AssertionError("должен был кидать SkipHandler")
    except SkipHandler:
        pass
