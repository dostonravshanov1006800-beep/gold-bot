# -*- coding: utf-8 -*-
"""v12.3: гейт подписки в группах — команды только для запустивших /start
в личке; /start в группе = только информация."""
import asyncio
import types

import bot


class U:
    def __init__(s, id, un):
        s.id, s.username, s.is_bot, s.first_name = id, un, False, "Тест"


class C:
    def __init__(s, ty, id):
        s.type, s.id = ty, id


class M:
    def __init__(s, ty, uid, text, mid):
        s.chat, s.from_user, s.text, s.message_id = C(ty, -100123), \
            U(uid, f"u{uid}"), text, mid
        s.reply_to_message = None

    async def reply(s, text, **k):
        s.replies.append(text)
        return types.SimpleNamespace(message_id=1)

    async def answer_photo(s, *a, **k):
        s.replies.append(("banner",))

    async def answer(s, *a, **k):
        pass


def setup():
    bot.db.init()
    bot.ME = "funapp_game"
    replies = []
    async def send_photo(**k): replies.append(("photo",))
    async def edit_message_text(**k): pass
    async def set_menu(*a, **k): pass
    async def send_message(chat, text, **k): replies.append(text)
    bot.BOT.send_photo = send_photo
    bot.BOT.edit_message_text = edit_message_text
    bot.BOT.set_chat_menu_button = set_menu
    bot.BOT.send_message = send_message
    bot.db.ex("DELETE FROM users")
    return replies


def run(c):
    return asyncio.run(c)


def test_newcomer_game_in_group_gets_subscribe_prompt():
    replies = setup()
    m = M("supergroup", 111, "футбол 100", 10)
    m.replies = replies
    run(bot.text_cmd(m))
    assert replies and "funapp_game" in replies[-1], replies
    assert bot.db.get_user(111) is None, "авто-регистрация в группе закрыта"


def test_newcomer_chatter_is_silent():
    replies = setup()
    m = M("supergroup", 111, "привет всем, как дела?", 11)
    m.replies = replies
    run(bot.text_cmd(m))
    assert not replies, replies


def test_subscribed_player_passes_gate():
    replies = setup()
    bot.db.upsert_user(222, "player")
    m = M("supergroup", 222, "футбол 100", 12)
    m.replies = replies
    run(bot.text_cmd(m))
    assert replies and "funapp_game" not in replies[-1], replies


def test_start_in_group_is_info_only():
    replies = setup()
    m = M("supergroup", 333, "/start", 13)
    m.replies = replies
    run(bot.cmd_start(m, types.SimpleNamespace(args="")))
    assert replies and "личке" in replies[-1], replies
    assert bot.db.get_user(333) is None, "групповой /start не регистрирует"


def test_start_in_private_still_registers():
    replies = setup()
    m = M("private", 333, "/start", 14)
    m.replies = replies
    run(bot.cmd_start(m, types.SimpleNamespace(args="")))
    assert bot.db.get_user(333) is not None


def test_duel_with_unsubscribed_opponent():
    replies = setup()
    bot.db.upsert_user(444, "duelist")
    bot.db.add_gold(444, 5000)
    m = M("supergroup", 444, "дуэль футбол 100", 15)
    m.replies = replies
    m.reply_to_message = types.SimpleNamespace(
        from_user=U(555, "stranger"), message_id=9)
    run(bot.try_duel(m))
    assert replies and "Соперник ещё не запустил" in replies[-1], replies
