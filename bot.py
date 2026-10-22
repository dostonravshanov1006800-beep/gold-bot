import asyncio
import hashlib
import json
import logging
import math
import os
import random
import subprocess
import urllib.request
import threading
import re
import time

import db
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats,
                           BotCommandScopeChat, ChatMemberUpdated, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQuery, InlineQueryResultPhoto,
    KeyboardButton, LabeledPrice, MenuButtonCommands, MenuButtonDefault,
    Message, PreCheckoutQuery, )
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DB_KEY = os.environ.get("DB_KEY", "")
GH_RENEW_TOKEN = os.environ.get("GH_RENEW_TOKEN", "")
GH_REPO = os.environ.get("GH_REPO", "dostonravshanov1006800-beep/gold-bot")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip().rstrip("/")

# премиум-иконки включаются секретом PREMIUM_UI=1, когда у владельца будет Premium
PREMIUM_UI = os.environ.get("PREMIUM_UI", "1") == "1"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

START_GOLD = 10        # голда новому игроку
BONUS_GOLD = 5          # ежедневный бонус
BONUS_HOURS = 12        # раз в сколько часов
REF_BONUS = 25          # бонус за друга после его первого задания
CUBE_X = 1.64        # честные x2 -> RTP 82%
# Таблицы калиброваны по замерам sendDice 22.10.2026 (N=140/80/80/80/200).
# Верхняя граница 95% доверительного интервала -> RTP гарантированно <= 0.85.
DART_PAY = {6: 5.0}                    # бык: p=11.4% (верх 16.8%) -> RTP 0.57-0.84
SLOTS_PAY = {1: 15.0, 2: 7.5, 3: 7.5, 4: 7.5, 22: 4.5, 43: 4.5}  # RTP ~0.81
BOWL_PAY = {6: 4.8}              # страйк: p=1/6 -> RTP 0.80 (целевой коридор)
FOOT_PAY = {3: 1.0, 4: 1.2, 5: 1.8}   # ТРИ визуальных гола (3=в сетку низом,
    # 4=в угол, 5=девятка; подтверждено бросками): возврат, +20%, +80% -> RTP 0.80
BASK_PAY = {4: 1.7, 5: 2.3}      # с щитком (4) и чисто (5): p=0.2+0.2 -> RTP 0.80
BANNER_URL = "https://media.base44.com/images/public/6ad9124e6fe14fca68f0c8ce/1a1fabe19_banner.jpg"  # баннер /start

# карточки игр: изображение + подпись (единый премиальный золотой стиль)
GAME_IMG = {
    "football":  "https://media.base44.com/images/public/6ad9124e6fe14fca68f0c8ce/d1f04562b_generated_image.png",
    "basketball": "https://media.base44.com/images/public/6ad9124e6fe14fca68f0c8ce/7db88f905_generated_image.png",
    "bowling":   "https://media.base44.com/images/public/6ad9124e6fe14fca68f0c8ce/90f807c22_generated_image.png",
    "cubes":     "https://media.base44.com/images/public/6ad9124e6fe14fca68f0c8ce/15b79c64d_generated_image.png",
    "dart":      "https://media.base44.com/images/public/6ad9124e6fe14fca68f0c8ce/71fb93910_generated_image.png",
    "slots":     "https://media.base44.com/images/public/6ad9124e6fe14fca68f0c8ce/66f9e4e5c_generated_image.png",
}

TOPUP = {5: 50, 10: 110, 25: 300, 50: 650}   # Telegram Stars -> голда (с бонусом за объём)

# игры на родных анимациях-кубиках Telegram
# Исходы игр: чёткая фраза вместо «Выпало: 5» (win для победы, fail для мимо)
# Тексты исходов. Побеждают только те значения кубика, которые ВИЗУАЛЬНО
# выглядят голом/попаданием: текст обязан совпадать с анимацией Telegram.
# WIN_TEXT: value -> фраза победы; FAIL_TEXT: value -> фраза промаха.
WIN_TEXT = {
    "football":   {3: "⚽ ГОЛ! Мяч в сетке!", 4: "⚽ ГОЛ! В самый угол!",
                   5: "⚽ ГОЛ! Прямо в девятку!"},
    "basketball": {4: "🏀 ПОПАДАНИЕ! С щитком!", 5: "🏀 ПОПАДАНИЕ! Чисто в кольцо!"},
    "bowling":    {6: "🎳 СТРАЙК! Все кегли разлетелись!"},
    "dart":       {6: "🎯 В ЯБЛОЧКО! Точно в десятку!"},
    "slots":      {1: "🎰 ДЖЕКПОТ! БАРЫ В РЯД!", 2: "🎰 ТРИ В РЯД!", 3: "🎰 ТРИ В РЯД!",
                   4: "🎰 ТРИ В РЯД!", 22: "🎰 ПАРА! Почти джекпот!", 43: "🎰 ПАРА! Почти джекпот!"},
}
FAIL_TEXT = {
    "football":   {1: "Мимо — выше ворот!", 2: "Вратарь вытащил..."},
    "basketball": {1: "Мимо кольца...", 2: "Не долетел...", 3: "С кольца — и отскочил!"},
    "bowling":    {1: "Срыв в канаву!", 2: "Зацепил пару кеглей...", 3: "Кегли почти устояли...",
                   4: "Кегли почти устояли...", 5: "Девятая кегля устояла!"},
    "dart":       {1: "Мимо доски!", 2: "Край доски — мимо...", 3: "Внешний круг...",
                   4: "Внешний круг...", 5: "Почти десятка — рядом!"},
    "slots":      {},
}

DICE_GAMES = {
    "dart":      ("🎯", DART_PAY),
    "slots":     ("🎰", SLOTS_PAY),
    "bowling":   ("🎳", BOWL_PAY),
    "football":  ("⚽", FOOT_PAY),
    "basketball": ("🏀", BASK_PAY),
}

BOT = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
DP = Dispatcher()
GAMES = {}  # user_id -> state
ME = ""


# ---------- премиум-эмодзи (пак Telegram iOS Icons) ----------

# fallback-эмодзи и ID из https://emoji.wivvi.net (пак t.me/addemoji/tgiosicons)
ICONS = {
    "coin":    ("🪙", "5778613750688911681"),   # монета
    "money":   ("💰", "5778421276024509124"),   # монеты TON
    "wallet":  ("👛", "5769126056262898415"),   # кошелёк
    "tasks":   ("🗂", "5766994197705921104"),   # список
    "games":   ("🎮", "5938413566624272793"),   # геймпад
    "shop":    ("🏪", "5920332557466997677"),   # магазин
    "people":  ("👥", "6033125983572201397"),   # добавить людей
    "gift":    ("🎁", "5773677501825945508"),   # подарок со звездой
    "present": ("🎁", "6032644646587338669"),  # подарок
    "app":     ("✈️", "6028346797368283073"),   # телеграм
    "home":    ("🏠", "6042137469204303531"),   # дом
    "back":    ("⬅️", "5960671702059848143"),   # назад
    "left":    ("⬅️", "6039539366177541657"),   # ответить (стрелка влево)
    "right":   ("➡️", "6037622221625626773"),   # переслать (стрелка вправо)
    "check":   ("✅", "5774022692642492953"),    # галочка
    "cross":   ("❌", "5774077015388852135"),    # крестик
    "target":  ("🎯", "6032949275732742941"),   # мишень
    "spin":    ("🌀", "6050588788021793070"),   # круги (слоты)
    "cube":    ("⬜️", "5884089033558070257"),   # квадрат (кубы)
    "up":      ("⏫", "5938437708635443119"),   # вверх (башня)
    "gem":     ("💎", "6037083366438737901"),   # алмаз
    "bolt":    ("⚡", "5884428842780594914"),   # молния (всё на кон)
    "star":    ("⭐️", "6034923938486684992"),   # звезда
    "crown":   ("👑", "5805553606635559688"),   # корона (админ)
    "stats":   ("📊", "5936143551854285132"),   # статистика
    "horn":    ("📢", "6021418126061605425"),   # рупор (рассылка)
    "inbox":   ("📥", "5776182936638329359"),   # входящие (заявки)
    "warn":    ("❗️", "6030563507299160824"),   # внимание
    "party":   ("🎉", "6041731551845159060"),   # ура
    "sad":     ("☹️", "6042029429301973188"),   # грусть
    "clock":   ("⏰️", "5983150113483134607"),   # часы
    "trophy":  ("🥇", "6037428784888549034"),   # медаль
    "info":    ("ℹ", "6028435952299413210"),    # инфо
    "wave":    ("👋", "6041921818896372382"),   # привет
    "trash":   ("🗑", "6039522349517115015"),   # удалить
    "plus":    ("➕", "5882207227997066107"),   # добавить
    "edit":    ("✏️", "6039779802741739617"),   # редактировать
    "robot":   ("🤖", "6030400221232501136"),   # бот
    "tags":    ("🏷", "5890727932011223292"),   # ценник
    "ball":    ("⚽️", "6042069608721027027"),   # мяч (футбол)
    "stars":   ("⭐️", "6028338546736107668"),   # звёзды TG
}


def pe(key):
    """Эмодзи для текста: премиум-анимация при PREMIUM_UI=1, иначе нативный фолбэк."""
    e, i = ICONS[key]
    if PREMIUM_UI:
        return f'<tg-emoji emoji-id="{i}">{e}</tg-emoji>'
    return e


def btn(text, data, icon=None, *, url=None, switch_inline=None):
    """Кнопка. Премиум-иконка — только в премиум-режиме, иначе чистый текст.
    url — ссылка; switch_inline — открыть выбор чата с подстановкой @бот <query>."""
    if url:
        if icon and PREMIUM_UI:
            return InlineKeyboardButton(
                text=text, url=url, icon_custom_emoji_id=ICONS[icon][1]
            )
        return InlineKeyboardButton(text=text, url=url)
    if switch_inline is not None:
        return InlineKeyboardButton(text=text, switch_inline_query=switch_inline)
    if icon and PREMIUM_UI:
        return InlineKeyboardButton(
            text=text, callback_data=data, icon_custom_emoji_id=ICONS[icon][1]
        )
    return InlineKeyboardButton(text=text, callback_data=data)


def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def fg(n):
    return f"{int(n):,}".replace(",", " ")


def is_admin(uid):
    return uid in ADMIN_IDS


def menu_kb(gold=0):
    return kb(
        [btn("Задания", "tasks", icon="tasks"), btn("Профиль", "profile", icon="wallet")],
        [btn("Игры", "games", icon="games"), btn("Магазин", "shop", icon="shop")],
        [btn("Бонус", "bonus", icon="gift"), btn("Пополнить", "topup", icon="stars")],
        [btn("Топ", "top", icon="trophy")],
    )


def back_kb(where="menu", label="Меню", icon="home"):
    return kb([btn(label, where, icon=icon)])


def welcome_text(name, referrer=None):
    via = f"\n{pe('people')} Пригласил: <b>@{referrer}</b>\n" if referrer else ""
    return (
        f"{pe('wave')} Привет, <b>{name}</b>!{via}\n"
        f"<blockquote>Зарабатывай <b>Голду</b> {pe('coin')} за подписки и игры. "
        f"Меняй на {pe('gift')} NFT-подарки и TG Premium.</blockquote>"
    )


async def notify_admins(text):
    for aid in ADMIN_IDS:
        try:
            await BOT.send_message(aid, text)
        except TelegramAPIError:
            pass


# ---------- безопасное обновление экрана ----------

async def show(c: CallbackQuery, text, reply_markup=None):
    """Обновить экран из кнопки. Работает и на фото-меню (баннер /start), и на тексте.
    Раньше везде был c.message.edit_text: на фото-сообщении (подпись, не текст)
    Telegram отвечал «no text in the message to edit», хендлер падал до c.answer()
    и кнопка «зависала» (22.10, скрин 18:14). Теперь:
      фото -> edit_caption (лимит подписи 1024; длиннее — шлём новым сообщением),
      текст -> edit_text, «message is not modified» — штатно игнорируем."""
    msg = c.message
    try:
        if msg.photo:
            if len(text) <= 1024:
                await msg.edit_caption(caption=text, reply_markup=reply_markup)
            else:
                await msg.answer(text, reply_markup=reply_markup)
        else:
            await msg.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        low = str(e).lower()
        if "not modified" in low:
            return
        # сообщение слишком старое/удалено/без прав на правку — шлём новым
        try:
            await msg.answer(text, reply_markup=reply_markup)
        except TelegramAPIError:
            logging.warning("show() не смог ни править, ни отправить: %s", e)


# ---------- /start и меню ----------

@DP.message(CommandStart())
async def cmd_start(m: Message, command: CommandStart):
    # реферал действует ТОЛЬКО для новых юзеров: существующий в базе не может
    # быть «приглашён» повторным переходом по ссылке
    is_new = not db.get_user(m.from_user.id)
    db.upsert_user(m.from_user.id, m.from_user.username)
    payload = (command.args or "").strip()
    ref = None
    if is_new and payload.startswith("ref_"):
        try:
            ref = int(payload[4:])
        except ValueError:
            ref = None
        if ref and ref != m.from_user.id and db.get_user(ref):
            # +25 пригласившему СРАЗУ при заходе друга (раньше — после первого
            # задания, из-за чего тесты «не видели» бонус; v11.34)
            if db.set_ref(m.from_user.id, ref):
                db.add_gold(ref, REF_BONUS)
                try:
                    await BOT.send_message(
                        ref,
                        f"{pe('party')} По твоей ссылке присоединился друг "
                        f"— <b>+{REF_BONUS} {pe('coin')}</b>!",
                        parse_mode="HTML",
                    )
                except TelegramAPIError:
                    pass
    referrer = None
    if payload.startswith("ref_") and ref and db.get_user(ref):
        ru = db.get_user(ref)
        referrer = ru["username"] if ru and ru["username"] else None
    u = db.get_user(m.from_user.id)
    # мини-апп удалён: сбрасываем персональную menu-кнопку чата (если была «Gold App»)
    try:
        await BOT.set_chat_menu_button(chat_id=m.from_user.id, menu_button=MenuButtonDefault())
    except TelegramAPIError:
        pass
    text = welcome_text(m.from_user.first_name or "друг", referrer)
    if BANNER_URL:
        await m.answer_photo(
            BANNER_URL, caption=text,
            reply_markup=menu_kb(u["gold"]),
        )
    else:
        await m.answer(text, reply_markup=menu_kb(u["gold"]))   # /start: баннер + приветствие + полное меню кнопок (Задания/Профиль/
        # Игры/Магазин/Бонус/Пополнить/Топ); карточка заданий — только по кнопке.
        # В группах меню не нужно: там всё работает текстом («футбол 100»).


@DP.callback_query(F.data == "menu")
async def cb_menu(c: CallbackQuery):
    u = db.get_user(c.from_user.id)
    await show(c, 
        welcome_text(c.from_user.first_name or "друг"), reply_markup=menu_kb(u["gold"])
    )
    await c.answer()


# ---------- задания ----------

def tasks_view(uid):
    rows = []
    lines = [
        f"{pe('tasks')} <b>Задания</b>", "",
        "<blockquote>Подпишись на канал и нажми «Проверить».</blockquote>", "",
    ]
    ts = db.tasks()
    if not ts:
        lines.append("Сейчас заданий нет — загляни позже!")
    for t in ts:
        done = db.is_done(uid, t["id"])
        lines.append(
            f"• {t['channel']} — +{t['reward']} {pe('coin')}"
            + (f" {pe('check')}" if done else "")
        )
        if not done:
            rows.append([btn(f"Проверить {t['channel']}", f"tchk:{t['id']}", icon="check")])
    rows.append([btn("Меню", "menu", icon="home")])
    return "\n".join(lines), kb(*rows)


@DP.callback_query(F.data == "tasks")
async def cb_tasks(c: CallbackQuery):
    text, markup = tasks_view(c.from_user.id)
    await show(c, text, reply_markup=markup)
    await c.answer()


@DP.callback_query(F.data.startswith("tchk:"))
async def cb_check(c: CallbackQuery):
    uid = c.from_user.id
    tid = int(c.data.split(":")[1])
    t = db.get_task(tid)
    if not t or not t["active"]:
        await c.answer("Задание недоступно", show_alert=True)
        return
    if db.is_done(uid, tid):
        await c.answer("Уже выполнено", show_alert=True)
        return
    try:
        member = await BOT.get_chat_member(t["channel"], uid)
    except TelegramAPIError as e:
        low = str(e).lower()
        if "member list" in low or "chat not found" in low or "chat_id is empty" in low:
            # бот не админ канала — проверка невозможна, это не ошибка юзера
            await c.answer(
                "Канал временно не проверяется — админы уже знают. "
                f"Подпишись на {t['channel']}, награда придёт.",
                show_alert=True,
            )
        else:
            await c.answer(
                "Не могу проверить подписку. Убедись, что подписался, или напиши админу.",
                show_alert=True,
            )
        logging.warning("task check failed %s uid=%s: %s", t["channel"], uid, e)
        return
    if member.status in ("left", "kicked"):
        await c.answer("Ты ещё не подписался! Подпишись и нажми снова.", show_alert=True)
        return
    if not db.try_claim_task(uid, tid):      # атомарный клейм: гонка параллельных кликов мертва
        await c.answer("Уже выполнено", show_alert=True)
        return
    db.add_gold(uid, t["reward"])
    await c.answer(f"+{t['reward']} голды зачислено!", show_alert=True)
    text, markup = tasks_view(uid)
    await show(c, text, reply_markup=markup)


# ---------- игры ----------

@DP.callback_query(F.data == "games")
async def cb_games(c: CallbackQuery):
    u = db.get_user(c.from_user.id)
    text = (
        f"{pe('games')} <b>Игры</b> · баланс <b>{fg(u['gold'])} {pe('coin')}</b>\n\n"
        "Выбери игру: откроется описание и ставки.\n"
        "В группах играй текстом, например <b>Футбол 100</b>."
    )
    markup = kb(
        [btn("Футбол", "g:football", icon="ball"), btn("Баскетбол", "g:basketball")],
        [btn("Боулинг", "g:bowling"), btn("Кубы", "g:cubes", icon="cube")],
        [btn("Дартс", "g:dart", icon="target"), btn("Слоты", "g:slots", icon="spin")],
        [btn("Меню", "menu", icon="home")],
    )
    await show(c, text, reply_markup=markup)
    await c.answer()


STAKES = [10, 50, 100, 500, 1_000, 5_000]


def stakes_kb(game):
    rows = [
        [btn(str(s), f"stake:{game}:{s}", icon="coin") for s in STAKES[:3]],
        [btn(str(s), f"stake:{game}:{s}", icon="coin") for s in STAKES[3:]],
        [btn("Всё на кон", f"stake:{game}:0", icon="bolt")],
        [btn("Игры", "games", icon="back")],
    ]
    return kb(*rows)


# карточки игр: что за игра, как выиграть, как играть текстом (без чисел-выплат,
# кроме кубов: решение «тексты без численных значений» из SPEC)
# (иконка, название, описание, пример-команда, множители-строка для цитаты)
GAME_CARDS = {
    "football": ("ball", "Футбол",
        "Удар по воротам: мяч в сетке, в углу или «девятка» — это победа.",
        "Футбол 100",
        "мяч в сетке — x1.0 (возврат)\nв угол — x1.2\nдевятка — x1.8"),
    "basketball": ("games", "Баскетбол",
        "Бросок в кольцо: чистое попадание и попадание с щитком приносят выигрыш.",
        "Баскетбол 100",
        "с щитком — x1.7\nчисто в кольцо — x2.3"),
    "bowling": ("games", "Боулинг",
        "Нужен только страйк: все кегли с одного шара.",
        "Боулинг 100",
        "страйк — x4.8"),
    "cubes": ("cube", "Кубы",
        "Угадай сторону: малые (1-3) или большие (4-6).",
        "Кубы 100 малые · Кубы 100 большие",
        "угадал сторону — x1.64"),
    "dart": ("target", "Дартс",
        "Бросок в мишень: попади в «яблочко» и забирай крупный приз.",
        "Дартс 100",
        "яблочко — x5.0"),
    "slots": ("spin", "Слоты",
        "Крути барабаны: тройные комбинации дают призы, три семёрки — главный.",
        "Слоты 500",
        "бары в ряд (джекпот) — x15\nтри в ряд — x7.5\nпара — x4.5"),
}


def game_card_text(game, gold):
    """Карточка игры в стиле референса: описание + сворачиваемые цитаты
    «Множители» и «Как играть» (blockquote expandable, без вложенности)."""
    icon, title, desc, ex, mult = GAME_CARDS.get(
        game, ("games", "Игра", "", "", ""))
    return (
        f"{pe(icon)} <b>{title}</b>\n\n"
        f"{desc}\n"
        f"<blockquote expandable><b>Множители:</b>\n{mult}</blockquote>"
        f"<blockquote expandable><b>Как играть:</b>\n"
        f"Текстом в группе: {ex}\n"
        f"Ставка: число до 1 млрд · «все» · 1к · 1кк</blockquote>"
        f"\n\nБаланс: <b>{fg(gold)} {pe('coin')}</b> · выбери ставку:"
    )


@DP.callback_query(F.data.startswith("g:"))
async def cb_game(c: CallbackQuery):
    game = c.data[2:]
    u = db.get_user(c.from_user.id)
    text, markup = game_card_text(game, u["gold"]), stakes_kb(game)
    img = GAME_IMG.get(game)
    if img:
        try:  # карточка-фото в едином золотом стиле; кнопки ставок под ней
            await c.message.answer_photo(
                img, caption=text, reply_markup=markup, parse_mode="HTML")
            await c.answer()
            return
        except TelegramAPIError:
            pass          # картинка не поднялась — показываем текстом
    await show(c, text, reply_markup=markup)
    await c.answer()


async def charge(c: CallbackQuery, amt):
    """Списывает ставку. Возвращает stake или None."""
    stake, err = charge_uid(c.from_user.id, amt)
    if err:
        await c.answer(err, show_alert=True)
        return None
    return stake


# ---------- общее ядро игр (личка и группы) ----------

# ---------- антиспам: защита от блокировок Telegram ----------
GAME_CD = 2           # пауза между играми одного юзера, сек (по ТЗ: 2-3 с)
_last_cmd = {}        # ключ -> время последнего запуска


def cd_check(key, delay=None):
    """0 = можно выполнять, иначе сколько секунд ждать."""
    now = time.time()
    left = (delay or GAME_CD) - (now - _last_cmd.get(key, 0))
    if left > 0:
        return left
    _last_cmd[key] = now
    return 0


async def safe_reply(m, text, **kw):
    """Reply без падения: flood-wwait пережидаем, нет прав — молча."""
    try:
        return await m.reply(text, **kw)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        try:
            return await m.reply(text, **kw)
        except TelegramAPIError:
            return None
    except TelegramAPIError:
        return None


def charge_uid(uid, amt):
    """Атомарно списывает ставку. Возвращает (stake, err). amt=0 — весь баланс."""
    stake = db.balance(uid) if amt == 0 else amt
    if amt != 0 and stake <= 0:
        return None, "Ставка должна быть больше нуля"
    if stake <= 0:
        return None, "Недостаточно голды. Выполни задания в личке бота!"
    if not db.charge_gold(uid, stake):     # атомарный UPDATE ... WHERE gold >= ?
        return None, "Недостаточно голды! Пополни баланс в личке бота."
    return stake, None


async def play_dice_game(uid, game, stake, msg, back=True, group_id=None):
    emoji, pay_table = DICE_GAMES[game]
    head = {"dart": pe('target'), "slots": pe('spin'), "football": pe('ball')}.get(game, emoji)
    dice = await msg.answer_dice(emoji=emoji)
    await asyncio.sleep(2.8)
    val = dice.dice.value
    db.add_stat(game, val)
    pay = pay_table.get(val, 0)
    rm = back_kb("games", "Игры", icon="back") if back else None
    win_word = WIN_TEXT.get(game, {}).get(val, f"Выпало: {val}")
    fail_word = FAIL_TEXT.get(game, {}).get(val, "Не повезло")
    if pay:
        won = round(stake * pay)
        db.add_gold(uid, won)
        db.track_game(uid, pay >= 1, won - stake if pay >= 1 else 0)
        if group_id:
            db.track_group_game(group_id, uid, pay >= 1, won - stake if pay >= 1 else 0)
        if pay >= 1:
            if won == stake:               # гол минимальной силы: ставка возвращается
                await msg.answer(
                    f"<b>{win_word}</b>\n"
                    f"{pe('coin')} Ничья — ставка {fg(stake)} {pe('coin')} возвращается.",
                    reply_markup=rm,
                )
            else:
                await msg.answer(
                    f"<b>{win_word}</b>\n"
                    f"{pe('party')} Победа! +{fg(won - stake)} {pe('coin')} (множитель x{pay})",
                    reply_markup=rm,
                )
        else:
            await msg.answer(
                f"{head} {win_word}\n"
                f"{pe('coin')} Почти! Вернул {fg(won)} {pe('coin')} — половину ставки.",
                reply_markup=rm,
            )
    else:
        db.track_game(uid, False, 0)
        if group_id:
            db.track_group_game(group_id, uid, False, 0)
        await msg.answer(
            f"{head} {fail_word}\n"
            f"{pe('sad')} Ставка {fg(stake)} {pe('coin')} сгорела.",
            reply_markup=rm,
        )


def cube_outcome(side, val):
    """Исход кубов без числа: только сторона и результат."""
    win = (val <= 3) if side == "lo" else (val >= 4)
    if win:
        word = f"{pe('cube')} МАЛЫЕ! Точно!" if side == "lo" else f"{pe('cube')} БОЛЬШИЕ! Точно!"
    else:
        word = f"{pe('cube')} Выпали большие..." if side == "lo" else f"{pe('cube')} Выпали малые..."
    return win, word


async def roll_cube(uid, stake, side, msg, back=True, group_id=None):
    dice = await msg.answer_dice(emoji="🎲")
    await asyncio.sleep(2.8)
    val = dice.dice.value
    db.add_stat("cube", val)
    rm = back_kb("games", "Игры", icon="back") if back else None
    win, word = cube_outcome(side, val)
    if win:
        won = round(stake * CUBE_X)
        db.add_gold(uid, won)
        db.track_game(uid, True, won - stake)
        if group_id:
            db.track_group_game(group_id, uid, True, won - stake)
        await msg.answer(
            f"{word}\n{pe('party')} Победа! +{fg(won - stake)} {pe('coin')} (x{CUBE_X})",
            reply_markup=rm,
        )
    else:
        db.track_game(uid, False, 0)
        if group_id:
            db.track_group_game(group_id, uid, False, 0)
        await msg.answer(
            f"{word}\n{pe('sad')} Ставка {fg(stake)} {pe('coin')} сгорела.",
            reply_markup=rm,
        )


@DP.callback_query(F.data.startswith("stake:"))
async def cb_stake(c: CallbackQuery):
    _, game, amt = c.data.split(":")
    amt = int(amt)
    if game not in DICE_GAMES and game != "cubes":
        await c.answer("Неизвестная игра", show_alert=True)
        return
    left = cd_check(f"u{c.from_user.id}")
    if left:
        await c.answer(f"Секунду! Подожди {max(1, math.ceil(left))} сек", show_alert=True)
        return
    stake = await charge(c, amt)
    if stake is None:
        return
    uid = c.from_user.id

    if game in DICE_GAMES:
        await play_dice_game(uid, game, stake, c.message)

    elif game == "cubes":
        old = GAMES.pop(uid, None)               # прошлый заход без выбора стороны:
        if old and old.get("stake"):
            db.add_gold(uid, old["stake"])       # возвращаем зависшую ставку
        GAMES[uid] = {"stake": stake}
        await c.message.answer(   # отдельным сообщением: карточка выше — фото
            f"Ставка: <b>{fg(stake)} {pe('coin')}</b>\n\n"
            f"{pe('cube')} <blockquote>Кубы: 1-2-3 — малые\n4-5-6 — большие\n"
            "Множитель x1.64</blockquote>",
            reply_markup=kb(
                [btn("Малые (1-3)", "cube:lo", icon="cube"), btn("Большие (4-6)", "cube:hi", icon="cube")],
                [btn("Игры", "games", icon="back")],
            ),
        )


@DP.callback_query(F.data.startswith("cube:"))
async def cb_cube(c: CallbackQuery):
    uid = c.from_user.id
    side = c.data.split(":")[1]
    g = GAMES.pop(uid, None)
    if not g or "stake" not in g:
        await c.answer("Игра устарела, начни заново", show_alert=True)
        return
    stake = g["stake"]
    await roll_cube(uid, stake, side, c.message)


# ---------- магазин ----------

def shop_view(uid):
    u = db.get_user(uid)
    rows = []
    lines = [
        f"{pe('shop')} <b>Магазин</b>", "",
        f"Баланс: <b>{fg(u['gold'])} {pe('coin')}</b>", "",
    ]
    its = db.items()
    if not its:
        lines.append("Скоро пополнится.")
    for it in its:
        lines.append(f"{pe('tags')} {it['title']} — {fg(it['cost'])} {pe('coin')}")
        rows.append([btn(f"Купить: {it['title']}", f"buy:{it['id']}", icon="present")])
    rows.append([btn("Меню", "menu", icon="home")])
    return "\n".join(lines), kb(*rows)


@DP.callback_query(F.data == "shop")
async def cb_shop(c: CallbackQuery):
    text, markup = shop_view(c.from_user.id)
    await show(c, text, reply_markup=markup)
    await c.answer()


@DP.callback_query(F.data.startswith("buy:"))
async def cb_buy(c: CallbackQuery):
    uid = c.from_user.id
    iid = int(c.data.split(":")[1])
    it = db.get_item(iid)
    if not it or not it["active"]:
        await c.answer("Товар недоступен", show_alert=True)
        return
    if not db.charge_gold(uid, it["cost"]):   # атомарное списание: минус невозможен
        await c.answer("Не хватает голды", show_alert=True)
        return
    rid = db.add_request(uid, iid)
    username = "@" + u["username"] if u["username"] else str(uid)
    await notify_admins(
        f"{pe('present')} Заявка #{rid}\nПользователь: {username} ({uid})\n"
        f"Приз: {it['title']}\nВыдать: /done {rid}"
    )
    text, markup = shop_view(uid)
    await show(c, text, reply_markup=markup)
    await c.answer(f"Заявка #{rid} принята! Админ выдаст приз.", show_alert=True)


# ---------- остальное меню ----------

@DP.callback_query(F.data == "balance")
async def cb_balance(c: CallbackQuery):
    u = db.get_user(c.from_user.id)
    await show(c, 
        f"{pe('wallet')} Баланс: <b>{fg(u['gold'])} {pe('coin')}</b>\n\n"
        "Выполняй задания, играй в игры и забирай бонус!",
        reply_markup=kb(
            [btn("Пополнить", "topup", icon="stars"), btn("Меню", "menu", icon="home")]
        ),
    )
    await c.answer()


@DP.callback_query(F.data == "profile")
async def cb_profile(c: CallbackQuery):
    uid = c.from_user.id
    u = db.get_user(uid)
    n = db.invited_count(uid)
    await show(c, 
        f"{pe('wallet')} <b>Профиль</b>\n\n"
        f"Баланс: <b>{fg(u['gold'])} {pe('coin')}</b>\n"
        f"Приглашено друзей: {n}\n\n"
        f"{pe('people')} Ссылка для друзей:\n"
        f"<code>https://t.me/{ME}?start=ref_{uid}</code>\n\n"
        f"<blockquote>Друг по твоей ссылке — <b>+{REF_BONUS} {pe('coin')}</b> тебе.</blockquote>",
        reply_markup=kb(
            [btn("Пополнить", "topup", icon="stars"), btn("Задания", "tasks", icon="tasks")],
            [btn("Меню", "menu", icon="home")],
        ),
    )
    await c.answer()


@DP.callback_query(F.data == "refs")
async def cb_refs(c: CallbackQuery):
    uid = c.from_user.id
    n = db.invited_count(uid)
    await show(c, 
        f"{pe('people')} <b>Друзья</b> · приглашено: <b>{n}</b>\n\n"
        f"<code>https://t.me/{ME}?start=ref_{uid}</code>\n"
        f"<blockquote>Друг по ссылке — <b>+{REF_BONUS} {pe('coin')}</b> тебе, сразу.</blockquote>",
        reply_markup=kb(
            [btn("Пригласить друга", None, icon="people", switch_inline="invite")],
            [btn("Копировать ссылку", "refcopy", icon="tags")],
            [btn("Меню", "menu", icon="home")],
        ),
    )
    await c.answer()


@DP.inline_query()
async def on_inline(q: InlineQuery):
    """Карточка приглашения «via @бот»: фото + подпись + кнопка с ref-ID отправителя."""
    uid = q.from_user.id
    caption = (
        f"{pe('games')} <b>Funs</b> — играй и зарабатывай Голду!\n"
        f"Меняй её на NFT-подарки и TG Premium.\n"
        f"Заходи по моей ссылке и забирай бонус {pe('gift')}"
    )
    card = InlineQueryResultPhoto(
        id="ref",
        photo_url=BANNER_URL,
        thumbnail_url=BANNER_URL,
        caption=caption,
        parse_mode="HTML",
        reply_markup=kb(
            [btn("Играть", None, icon="games", url=f"https://t.me/{ME}?start=ref_{uid}")],
        ),
    )
    # is_personal: ref-ID у каждого свой; без кэша — всегда свежая ссылка
    await q.answer([card], is_personal=True, cache_time=0)


@DP.callback_query(F.data == "refcopy")
async def cb_refcopy(c: CallbackQuery):
    uid = c.from_user.id
    await c.message.answer(f"https://t.me/{ME}?start=ref_{uid}")
    await c.answer()


@DP.callback_query(F.data == "top")
async def cb_top(c: CallbackQuery):
    top = db.top_users(10)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, u in enumerate(top):
        name = f"@{u['username']}" if u["username"] else f"ID {u['id']}"
        mark = medals[i] if i < 3 else f" {i + 1}."
        lines.append(f"{mark} {name} · {fg(u['gold'])} {pe('coin')}")
    body = "\n".join(lines) or "Пока никого."
    text = (
        f"{pe('trophy')} <b>Топ по голде</b>\n\n{body}\n\n"
        "<blockquote expandable>Админ иногда разыгрывает банк по этому топу: "
        "топ-3 — крупные доли, 4–10 — средние, 11–20 — меньшие.</blockquote>"
    )
    await show(c, text, reply_markup=back_kb())
    await c.answer()


@DP.callback_query(F.data == "bonus")
async def cb_bonus(c: CallbackQuery):
    uid = c.from_user.id
    u = db.get_user(uid)
    now = int(time.time())
    if not db.claim_bonus(uid, now, BONUS_HOURS * 3600, BONUS_GOLD):
        left = BONUS_HOURS * 3600 - (now - u["last_bonus"])
        h, rem = divmod(left, 3600)
        mn = rem // 60
        await c.answer(f"Бонус будет доступен через {h} ч {mn} мин", show_alert=True)
        return
    await c.answer(f"+{BONUS_GOLD} голды! Возвращайся через {BONUS_HOURS} часов.", show_alert=True)
    await show(c, 
        f"{pe('wallet')} Баланс: <b>{fg(db.get_user(uid)['gold'])} {pe('coin')}</b>",
        reply_markup=back_kb(),
    )


# ---------- пополнение через Telegram Stars ----------

@DP.callback_query(F.data == "topup")
async def cb_topup(c: CallbackQuery):
    lines = [
        f"{pe('stars')} <b>Пополнение в Stars</b>", "",
        f"<blockquote>Курс: <b>1 {pe('stars')} = 10 G</b>. Пачка покрупнее — бонус сверху.</blockquote>", "",
    ]
    for stars, g in TOPUP.items():
        bonus = g - stars * 10
        lines.append(f"• <b>{stars} {pe('stars')}</b> → {fg(g)} G"
                     + (f" <i>+{fg(bonus)} в подарок</i>" if bonus else ""))
    lines += ["", f"{pe('party')} Голда прилетает сразу после оплаты."]
    rows = [[btn(f"{stars} Stars → {fg(g)} G", f"pay:{stars}", icon="stars")]
            for stars, g in TOPUP.items()]
    rows.append([btn("Меню", "menu", icon="home")])
    await show(c, 
        "\n".join(lines), reply_markup=kb(*rows)
    )
    await c.answer()


@DP.callback_query(F.data.startswith("pay:"))
async def cb_pay(c: CallbackQuery):
    uid = c.from_user.id
    stars = int(c.data.split(":")[1])
    gold_amt = TOPUP.get(stars)
    if not gold_amt:
        await c.answer("Пакет недоступен", show_alert=True)
        return
    db.upsert_user(uid, c.from_user.username)
    await c.message.answer_invoice(
        title=f"{fg(gold_amt)} голды",
        description=f"Пополнение баланса Gold Bot на {fg(gold_amt)} G",
        payload=f"topup:{gold_amt}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{fg(gold_amt)} G", amount=stars)],
    )
    await c.answer()


@DP.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    ok = q.invoice_payload.startswith("topup:")
    if ok:
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message="Пакет недоступен")


@DP.message(F.successful_payment)
async def on_paid(m: Message):
    uid = m.from_user.id
    db.upsert_user(uid, m.from_user.username)
    try:
        gold_amt = int(m.successful_payment.invoice_payload.split(":")[1])
    except (ValueError, IndexError):
        return
    if not db.process_payment_idempotent(m.successful_payment.telegram_payment_charge_id, uid, gold_amt):
        logging.info("duplicate payment ignored: uid=%s", uid)
        return
    u = db.get_user(uid)
    await m.answer(
        f"{pe('stars')} Оплата прошла!\n"
        f"+{fg(gold_amt)} {pe('coin')} зачислено.\n"
        f"Баланс: <b>{fg(u['gold'])} {pe('coin')}</b>",
        reply_markup=menu_kb(u["gold"]),
    )
    await notify_admins(
        f"{pe('stars')} Пополнение: {uid} +{fg(gold_amt)} G "
        f"за {m.successful_payment.total_amount} {pe('stars')}"
    )


# ---------- мини-апп: админ-панель ----------

def admin_only(handler):
    async def wrapper(m: Message, *a, **kw):
        if not is_admin(m.from_user.id):
            await m.answer(f"{pe('warn')} Только для админа.")
            return
        return await handler(m, *a, **kw)
    return wrapper


@DP.message(Command("addtask"))
@admin_only
async def cmd_addtask(m: Message):
    parts = m.text.split()
    if len(parts) != 3 or not parts[2].isdigit() or not (parts[1].startswith("@") or parts[1].startswith("-100")):
        await m.answer("Формат: /addtask @канал 50")
        return
    db.add_task(parts[1], int(parts[2]))
    await m.answer(f"{pe('check')} Задание добавлено: {parts[1]} (+{parts[2]} {pe('coin')})\n"
                   f"Не забудь добавить бота админом в {parts[1]}, иначе проверка подписки не сработает!")


@DP.message(Command("deltask"))
@admin_only
async def cmd_deltask(m: Message):
    parts = m.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await m.answer("Формат: /deltask ID")
        return
    db.del_task(int(parts[1]))
    await m.answer(f"{pe('check')} Задание удалено.")


@DP.message(Command("tasks"))
@admin_only
async def cmd_tasks_admin(m: Message):
    ts = db.tasks()
    if not ts:
        await m.answer("Заданий нет.")
        return
    await m.answer("\n".join(f"{t['id']}. {t['channel']} (+{t['reward']} {pe('coin')})" for t in ts))


@DP.message(Command("additem"))
@admin_only
async def cmd_additem(m: Message):
    parts = m.text.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await m.answer("Формат: /additem 150 TG Premium на 1 месяц")
        return
    db.add_item(parts[2], int(parts[1]))
    await m.answer(f"{pe('check')} Товар добавлен: {parts[2]} ({parts[1]} {pe('coin')})")


@DP.message(Command("delitem"))
@admin_only
async def cmd_delitem(m: Message):
    parts = m.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await m.answer("Формат: /delitem ID")
        return
    db.del_item(int(parts[1]))
    await m.answer(f"{pe('check')} Товар удалён.")


@DP.message(Command("items"))
@admin_only
async def cmd_items(m: Message):
    its = db.items()
    if not its:
        await m.answer("Товаров нет.")
        return
    await m.answer("\n".join(f"{i['id']}. {i['title']} ({i['cost']} {pe('coin')})" for i in its))


@DP.message(Command("requests"))
@admin_only
async def cmd_requests(m: Message):
    rs = db.pending_requests()
    if not rs:
        await m.answer("Заявок нет.")
        return
    lines = []
    for r in rs:
        u = db.get_user(r["user_id"])
        username = "@" + u["username"] if u and u["username"] else str(r["user_id"])
        lines.append(f"#{r['id']} — {username} ({r['user_id']}) — {r['title']} — /done {r['id']}")
    await m.answer("\n".join(lines))


@DP.message(Command("done"))
@admin_only
async def cmd_done(m: Message):
    parts = m.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await m.answer("Формат: /done ID_заявки")
        return
    r = db.done_request(int(parts[1]))
    if not r:
        await m.answer("Заявка не найдена или уже выдана.")
        return
    try:
        await BOT.send_message(r["user_id"], f"{pe('party')} Админ выдал твой приз! Проверь сообщения от бота.")
    except TelegramAPIError:
        pass
    await m.answer(f"{pe('check')} Заявка #{r['id']} выдана. Напомни приз вручную, если это подарок.")


@DP.message(Command("give"))
@admin_only
async def cmd_give(m: Message):
    parts = m.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].lstrip("-").isdigit():
        await m.answer("Формат: /give ID 100")
        return
    uid, amt = int(parts[1]), int(parts[2])
    if not db.get_user(uid):
        await m.answer("Пользователь не найден (нужно, чтобы он нажал /start).")
        return
    db.add_gold(uid, amt)
    await m.answer(f"{pe('check')} {uid}: {amt:+d} {pe('coin')}")


@DP.message(Command("take"))
@admin_only
async def cmd_take(m: Message):
    parts = m.text.split()
    if len(parts) != 3:
        await m.answer("Формат: /take ID 100 или /take ID все")
        return
    uid = int(parts[1]) if parts[1].isdigit() else None
    if uid is None or not db.get_user(uid):
        await m.answer("Пользователь не найден (нужно, чтобы он нажал /start).")
        return
    if parts[2].lower() in ("все", "всё"):
        old_gold = db.zero_gold(uid)
        await m.answer(f"{pe('sad')} {uid}: баланс обнулён (было {fg(old_gold)})")
        return
    if not parts[2].isdigit():
        await m.answer("Формат: /take ID 100 или /take ID все")
        return
    amount = int(parts[2])
    if amount <= 0:
        await m.answer("Сумма должна быть больше нуля.")
        return
    new_gold = db.take_gold(uid, amount)
    await m.answer(f"{pe('sad')} {uid}: -{amount} {pe('coin')} · осталось {fg(new_gold)}")


async def broadcast_all(text: str, chat_to_notify=None) -> tuple:
    """Рассылка всем: HTML (премиум-эмодзи <tg-emoji>), при ошибке — чистый текст."""
    ok = 0
    ids = db.all_user_ids()
    for uid in ids:
        try:
            await BOT.send_message(uid, text, parse_mode="HTML")
            ok += 1
        except TelegramAPIError:
            try:
                await BOT.send_message(uid, text, parse_mode=None)
                ok += 1
            except TelegramAPIError:
                pass
        await asyncio.sleep(0.05)
    return ok, len(ids)


@DP.message(Command("broadcast"))
@admin_only
async def cmd_broadcast(m: Message):
    text = m.text.split(maxsplit=1)
    if len(text) < 2:
        await m.answer("Формат: /broadcast текст сообщения")
        return
    await m.answer(f"Рассылка для {len(db.all_user_ids())} пользователей...")
    ok, total = await broadcast_all(text[1])
    await m.answer(f"{pe('check')} Доставлено: {ok}/{total}")


@DP.message(Command("stats"))
@admin_only
async def cmd_stats(m: Message):
    users, gold, reqs = db.stats()
    await m.answer(
        f"{pe('stats')} <b>Статистика</b>\n\n"
        f"Пользователей: {users}\n"
        f"Голды в обороте: {fg(gold)} {pe('coin')}\n"
        f"Заявок в ожидании: {reqs}"
    )



# ---------- слэш-команды (меню «Меню» в Telegram) ----------

async def _open_screen(m: Message, text, markup):
    await m.answer(text, reply_markup=markup)


@DP.message(Command("balance"), F.chat.type == "private")
async def cmd_balance(m: Message):
    db.upsert_user(m.from_user.id, m.from_user.username)
    u = db.get_user(m.from_user.id)
    await _open_screen(m,
        f"{pe('wallet')} Баланс: <b>{fg(u['gold'])} {pe('coin')}</b>",
        kb([btn("Пополнить", "topup", icon="stars"), btn("Игры", "games", icon="games")],
           [btn("Меню", "menu", icon="home")]))


@DP.message(Command("bonus"), F.chat.type == "private")
async def cmd_bonus(m: Message):
    uid = m.from_user.id
    db.upsert_user(uid, m.from_user.username)
    u = db.get_user(uid)
    now = int(time.time())
    if db.claim_bonus(uid, now, BONUS_HOURS * 3600, BONUS_GOLD):
        await _open_screen(m,
            f"{pe('party')} +{BONUS_GOLD} {pe('coin')}! Возвращайся через {BONUS_HOURS} ч.\n"
            f"Баланс: <b>{fg(db.get_user(uid)['gold'])} {pe('coin')}</b>",
            back_kb())
    else:
        left = BONUS_HOURS * 3600 - (now - u["last_bonus"])
        h, rem = divmod(left, 3600)
        await _open_screen(m,
            f"{pe('clock')} Бонус будет доступен через {h} ч {rem // 60} мин",
            back_kb())


@DP.message(Command("plays"), F.chat.type == "private")
async def cmd_plays(m: Message):
    db.upsert_user(m.from_user.id, m.from_user.username)
    u = db.get_user(m.from_user.id)
    await _open_screen(m,
        f"{pe('games')} <b>Игры</b> · баланс <b>{fg(u['gold'])} {pe('coin')}</b>\n\n"
        "Выбери игру: откроется описание и ставки.\n"
        "В группах играй текстом, например <b>Футбол 100</b>.",
        kb([btn("Футбол", "g:football", icon="ball"), btn("Баскетбол", "g:basketball")],
           [btn("Боулинг", "g:bowling"), btn("Кубы", "g:cubes", icon="cube")],
           [btn("Дартс", "g:dart", icon="target"), btn("Слоты", "g:slots", icon="spin")],
           [btn("Меню", "menu", icon="home")]))


@DP.message(Command("referrals"), F.chat.type == "private")
async def cmd_referrals(m: Message):
    uid = m.from_user.id
    db.upsert_user(uid, m.from_user.username)
    n = db.invited_count(uid)
    await _open_screen(m,
        f"{pe('people')} <b>Друзья</b> · приглашено: <b>{n}</b>\n\n"
        f"<code>https://t.me/{ME}?start=ref_{uid}</code>\n"
        f"<blockquote>Друг по ссылке — <b>+{REF_BONUS} {pe('coin')}</b> тебе, сразу.</blockquote>",
        kb(
            [btn("Пригласить друга", None, icon="people", switch_inline="invite")],
            [btn("Копировать ссылку", "refcopy", icon="tags")],
            [btn("Меню", "menu", icon="home")],
        ))


@DP.message(Command("help"), F.chat.type == "private")
async def cmd_help(m: Message):
    db.upsert_user(m.from_user.id, m.from_user.username)
    adm = is_admin(m.from_user.id)
    await _open_screen(m,
        f"{pe('info')} <b>Помощь</b>\n"
        f"{pe('games')} /plays · {pe('wallet')} /balance · {pe('gift')} /bonus (раз в {BONUS_HOURS} ч) · {pe('people')} /referrals\n\n"
        "<blockquote><b>В группах</b> — текстом: Футбол 100 · Слоты 500 · Кубы 100 малые</blockquote>"
        + (f"\n\n{pe('crown')} Админ: /admin" if adm else ""),
        back_kb())

# ---------- CRM-админка внутри бота ----------
import crm
crm.setup({
    "bot": BOT, "dp": DP, "pe": pe, "btn": btn, "kb": kb, "fg": fg,
    "is_admin": is_admin, "broadcast_all": broadcast_all,
})

# ---------- синхронизация базы в git ----------

# ---------- конфиденциальность: база только в шифрованном виде ----------

def db_decrypt_if_needed():
    """На старте: если bot.db нет, а зашифрованная копия есть — расшифровываем.
    Если ключ не задан при наличии шифра — halt. Молча стартовать с пустой
    базей НЕЛЬЗЯ: это выглядело бы как обнуление всех балансов (22.10)."""
    if os.path.exists(db.DB_PATH) or not os.path.exists(db.DB_PATH + ".enc"):
        return
    if not DB_KEY:
        raise SystemExit("DB_KEY не задан, bot.db.enc есть, bot.db нет — старт отменён")
    r = subprocess.run(["openssl", "aes-256-cbc", "-d", "-pbkdf2", "-in", db.DB_PATH + ".enc",
                        "-out", db.DB_PATH, "-pass", f"env:DB_KEY"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"расшифровка bot.db.enc не удалась: {r.stderr[:200]}")
    logging.info("bot.db расшифрована из bot.db.enc")

def db_encrypt_snapshot():
    """Свежая зашифрованная копия базы для коммита (bot.db остаётся локальной,
    в git уходит только .enc). Возвращает True при успехе."""
    if not DB_KEY:
        return False
    db.wal_checkpoint()
    r = subprocess.run(["openssl", "aes-256-cbc", "-pbkdf2", "-in", db.DB_PATH,
                        "-out", db.DB_PATH + ".enc", "-pass", f"env:DB_KEY"],
                       capture_output=True, text=True)
    return r.returncode == 0


RENEW_EXIT = 42   # код выхода: «передай эстафету следующему рану»

def self_renew_loop():
    """Гарантия 24/7: за 18 мин до таймаута джобы (330 мин) сам вызываем
    workflow_dispatch следующего рана и выходим кодом 42. Шелл-цикл рана
    видит 42 и завершает джобу спокойно. Это главный механизм непрерывности;
    watchdog по cron — лишь страховка (GitHub cron задерживает до десятков
    минут, замер 22.10: */5 не сработал ни разу за 30 мин)."""
    RENEW_AFTER = 330 * 60 - 18 * 60   # ~5ч12м
    time.sleep(RENEW_AFTER)
    try:
        # финальный синк, чтобы не потерять последние секунды базы
        db_encrypt_snapshot()
        if not GH_RENEW_TOKEN:
            logging.warning("GITHUB_TOKEN нет — само-продление пропущено, надеемся на watchdog")
            return
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GH_REPO}/actions/workflows/run-bot.yml/dispatches",
            json.dumps({"ref": "main"}).encode(),
            {"Authorization": f"Bearer {GH_RENEW_TOKEN}",
             "Accept": "application/vnd.github+json"}, method="POST")
        resp = urllib.request.urlopen(req)
        logging.info("эстафета передана (dispatch %s), выходим кодом %s", resp.status, RENEW_EXIT)
        os._exit(RENEW_EXIT)
    except Exception as e:
        logging.warning("само-продление не удалось: %s — работаем до таймаута", e)


def git_sync_loop():
    """Каждые 45с коммитит bot.db в репозиторий (для GitHub Actions)."""
    time.sleep(90)
    while True:
        try:
            def run(*args):
                return subprocess.run(
                    list(args), cwd=BASE_DIR, capture_output=True, text=True, timeout=120
                )
            run("git", "config", "user.name", "bot-sync")
            run("git", "config", "user.email", "bot@users.noreply.github.com")
            st = run("git", "status", "--porcelain", "--", "bot.db", "bot.db.enc")
            if st.stdout.strip():
                if not db_encrypt_snapshot():
                    logging.warning("DB_KEY нет — синк базы в репо ПРОПУЩЕН (без шифрования не публикуем)")
                    time.sleep(45)
                    continue
                # ВАЖНО: никаких pull --rebase — ребейз подменяет открытый
                # sqlite-файл прямо под ботом, и база становится readonly
                # (инцидент 22.10 09:52). Код из remote подхватываем только
                # указателем HEAD, рабочую папку git не трогает. Живая
                # bot.db всегда важнее версии в репо — коммитим её поверх.
                run("git", "fetch", "origin", "main")
                # --mixed (НЕ --soft): индекс берётся из origin/main, иначе
                # в коммит уезжает старая рабочая копия и синк откатывает код
                run("git", "reset", "--mixed", "origin/main")
                run("git", "rm", "--cached", "--ignore-unmatch", "-q", "bot.db")
                run("git", "add", "bot.db.enc")
                run("git", "commit", "-m", "chore: sync database")
                push = run("git", "push")
                if push.returncode != 0:
                    logging.warning("git push failed: %s", push.stderr)
                else:
                    logging.info("bot.db synced to repo")
        except Exception as e:  # локальный запуск без git — это нормально
            logging.warning("git sync skipped: %s", e)
        time.sleep(45)



# ---------- запуск ----------

async def main():
    global ME
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан (секреты репозитория)")
    db_decrypt_if_needed()
    db.init()
    me = await BOT.me()
    ME = me.username
    logging.info(
        "admins: %s | premium_ui: %s | inline_mode: %s",
        sorted(ADMIN_IDS), PREMIUM_UI, getattr(me, "supports_inline_queries", None),
    )
    if not getattr(me, "supports_inline_queries", False):
        logging.warning("INLINE OFF: включи в BotFather /setinline (placeholder: Пригласить друга)")
    # сброс старой web_app-кнопки меню (мини-апп удалён — возвращаем стандартное меню команд)
    try:
        await BOT.set_chat_menu_button(menu_button=MenuButtonCommands())
    except TelegramAPIError as e:
        logging.warning("menu button reset: %s", e)
    try:
        user_cmds = [
            BotCommand(command="bonus", description="бесплатная голда"),
            BotCommand(command="balance", description="твой баланс"),
            BotCommand(command="plays", description="доступные игры"),
            BotCommand(command="referrals", description="пригласить друзей"),
            BotCommand(command="help", description="помощь по боту"),
        ]
        await BOT.set_my_commands(user_cmds, scope=BotCommandScopeAllPrivateChats())
        await BOT.set_my_commands(
            [BotCommand(command="help", description="помощь по боту")],
            scope=BotCommandScopeAllGroupChats())
        for aid in ADMIN_IDS:      # админам к общему меню добавляем /admin
            await BOT.set_my_commands(
                user_cmds + [BotCommand(command="admin", description="панель управления")],
                scope=BotCommandScopeChat(chat_id=aid))
    except TelegramAPIError as e:
        logging.warning("set_my_commands: %s", e)
    threading.Thread(target=git_sync_loop, daemon=True).start()
    if GH_RENEW_TOKEN:
        threading.Thread(target=self_renew_loop, daemon=True).start()
    # Сердцебиение: раз в 4 минуты пишем метку в базу. По коммитам bot.db в репо
    # извне видно, жив ли бот (git_sync_loop коммитит только при изменениях базы).
    async def heartbeat():
        while True:
            try:
                db.put_meta("heartbeat", str(int(time.time())))
                db.prune_old()
            except Exception as e:
                logging.warning("heartbeat: %s", e)
            await asyncio.sleep(240)
    asyncio.create_task(heartbeat())
    await DP.start_polling(BOT, allowed_updates=["message", "callback_query",
                                               "pre_checkout_query", "my_chat_member",
                                               "inline_query"])
    # Сюда дойти нельзя: поллинг живёт вечно. Если умер молча (например, конфликт
    # 409) — выходим, workflow-цикл перезапустит бота через 5 секунд.
    logging.critical("start_polling завершился — выходим на перезапуск")
    os._exit(1)



# ---------- групповой режим ----------
# «Баланс», «Профиль», «Топ», «Бонус». Задания и каналы — только в личке бота.
# ---------- текстовые команды: одна таблица, расширяется строкой ----------
TEXT_CMDS = {
    "баланс": "баланс", "б": "баланс", "голда": "баланс",
    "профиль": "профиль", "я": "профиль", "проф": "профиль",
    "бонус": "бонус",
    "задания": "задания",
    "магазин": "магазин",
    "топ": "топ",
    "игры": "игры",
    "помощь": "помощь", "команды": "помощь",
    # слэш-команды из меню Telegram работают и в группах
    "balance": "баланс", "bonus": "бонус", "help": "помощь",
    "plays": "игры", "top": "топ", "profile": "профиль",
}
GAME_ALIASES = {
    "футбол": "football", "футб": "football", "⚽": "football",
    "баскетбол": "basketball", "🏀": "basketball",
    "боулинг": "bowling", "🎳": "bowling",
    "дартс": "dart", "🎯": "dart",
    "слоты": "slots", "слот": "slots", "казино": "slots", "🎰": "slots",
    "кубы": "cube", "кубик": "cube", "кубок": "cube", "🎲": "cube",
}
CUBE_SIDES = {"малые": "lo", "м": "lo", "мал": "lo",
              "большие": "hi", "б": "hi", "больш": "hi"}
ALL_IN = {"все", "всё", "all", "всё на кон", "все на кон"}
GAMES_LIST_TEXT = "⚽ Футбол · 🏀 Баскетбол · 🎳 Боулинг · 🎲 Кубы · 🎯 Дартс · 🎰 Слоты"


def parse_amount_tok(tok):
    """'100' → 100 · '1к' → 1000 · '2кк' → 2 000 000 · 'все' → весь баланс."""
    if tok in ALL_IN:
        return "all"
    m = re.fullmatch(r"(\d+)\s*(кк|kk|к|k)", tok)
    if m:
        v = int(m.group(1)) * (1_000_000 if m.group(2) in ("кк", "kk") else 1_000)
        return v if v <= 1_000_000_000 else None     # выше 1 млрд — не ставка
    if tok.isdigit():
        v = int(tok)
        return v if v <= 1_000_000_000 else None
    return None


RETIRED_GAMES = {"рулетка", "монетка", "мины", "башня", "колесо", "кнб"}
# удалённые игры: пользователь явно пытается играть — подскажем, что их больше нет


def parse_group_cmd(text):
    """Разбирает текст: инфо-команда → игра+ставка → «unknown» → None (молча)."""
    parts = (text or "").strip().lower().lstrip("/").split()
    if parts and "@" in parts[0]:            # снимаем упоминание СВОЕГО бота
        name0, _, men = parts[0].partition("@")
        if ME and men == ME.lower():
            parts[0] = name0
        else:
            return None                      # команда адресована другому боту
    if not parts:
        return None
    name, rest = parts[0], parts[1:]
    name = name.strip(",.!?;:")          # «бот,» / «футбол!» — та же команда
    if name == "бот":                    # к боту обращаются по имени
        if not rest:
            return {"info": "зов"}       # «бот» — спросим, что хотят
        return parse_group_cmd(" ".join(rest))   # «бот баланс», «бот футбол 100»
    if name in TEXT_CMDS:
        if len(name) == 1 and rest:      # «я пошёл спать» — не команда профиля
            return None
        return {"info": TEXT_CMDS[name]}
    amt = None
    side = None
    for t in rest:
        if t in ALL_IN and amt is None:
            amt = "all"
        elif side is None and t in CUBE_SIDES:
            side = CUBE_SIDES[t]
        else:
            a = parse_amount_tok(t)
            if amt is None and a is not None:
                amt = a
    if name in GAME_ALIASES:
        game = GAME_ALIASES[name]
        if game == "cube":
            if amt is None:
                return {"game": "cube", "stake": None}
            if side is None:
                return {"game": "cube", "stake": amt, "need_side": True}
            return {"game": "cube", "stake": amt, "side": side}
        if amt is None:
            return {"game": game, "stake": None}
        return {"game": game, "stake": amt}
    if amt is not None and ((text or "").strip().startswith("/")
                            or name in RETIRED_GAMES):
        return {"unknown": name}              # /команда или удалённая игра — подскажем
    return None                               # бытовая фраза с числом — молчим


async def group_info(m: Message, uid, kind):
    """Разделы по тексту. Отвечаем reply — в группе видно, кому адресовано."""
    u = db.get_user(uid)
    if kind == "баланс":
        await safe_reply(m, f"{pe('wallet')} Баланс: <b>{fg(u['gold'])} {pe('coin')}</b>")
    elif kind == "профиль":
        n = db.invited_count(uid)
        name = m.from_user.first_name or "—"
        if m.chat.type in ("group", "supergroup"):
            gs = db.one("SELECT * FROM group_stats WHERE chat_id=? AND user_id=?",
                        (m.chat.id, uid))
            played, won, lost = ((gs["played"], gs["won"], gs["lost"]) if gs else (0, 0, 0))
            twon = gs["total_won"] if gs else 0
        else:
            played, won, lost, twon = (u["games_played"], u["games_won"],
                                       u["games_lost"], u["total_won"])
        await safe_reply(m, 
            f"{pe('wallet')} <b>Профиль в этой группе</b>\n"
            f"Имя: <b>{name}</b>\n"
            f"ID: <code>{uid}</code>\n"
            f"Баланс: <b>{fg(u['gold'])} {pe('coin')}</b>\n"
            f"Игр сыграно: <b>{played}</b> · Побед: <b>{won}</b> · Поражений: <b>{lost}</b>\n"
            f"Выигрыш в группе: <b>{fg(twon)} {pe('coin')}</b>\n"
            f"Приглашено друзей: {n}\n\n"
            f"Задания, магазин и призы — в личке: https://t.me/{ME}"
        )
    elif kind in ("задания", "магазин"):
        await safe_reply(
            m, f"Открой в личных сообщениях со мной — https://t.me/{ME}\n"
               "Задания, магазин и призы живут там, а не в группе.")
    elif kind == "зов":
        name = m.from_user.first_name or "друг"
        await safe_reply(m,
            f"{pe('games')} Я здесь, <b>{name}</b>! Кубы 100 <b>малые</b> или <b>большие</b> · Футбол 100 · Слоты 500\n"
            "Или спроси: <b>баланс</b> · <b>топ</b> · <b>помощь</b>")
    elif kind == "игры":
        await safe_reply(m,
            f"{pe('games')} <b>Игры</b>\n\n"
            f"{pe('ball')} Футбол · {pe('games')} Баскетбол · {pe('games')} Боулинг\n"
            f"{pe('cube')} Кубы · {pe('target')} Дартс · {pe('spin')} Слоты\n\n"
            "<blockquote>Футбол 100 · Слоты 500 · Кубы 100 малые\n"
            "Ставка: число · «все» · 1к · 1кк</blockquote>"
        )
    elif kind == "помощь":
        await safe_reply(m,
            f"{pe('info')} <b>Помощь</b>\n\n"
            "<blockquote>Футбол 100 · Кубы 100 большие — название и ставка</blockquote>\n"
            f"{pe('wallet')} Баланс (б) · Профиль (я) · Бонус · Топ\n"
            f"{pe('tasks')} Задания и магазин — в личке: https://t.me/{ME}"
        )
    elif kind == "топ":
        if m.chat.type in ("group", "supergroup"):
            rows = db.group_top(m.chat.id, 10)
            medals = ["🥇", "🥈", "🥉"]
            lines = []
            for i, t in enumerate(rows):
                tname = f"@{t['username']}" if t["username"] else f"ID {t['user_id']}"
                mark = medals[i] if i < 3 else f" {i + 1}."
                lines.append(f"{mark} {tname} · {fg(t['total_won'])} {pe('coin')}")
            body = "\n".join(lines) or "Пока никто не играл в этой группе."
            await safe_reply(m, f"{pe('trophy')} <b>Топ этой группы</b>\n\n{body}")
            return
        top = db.top_users(10)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, t in enumerate(top):
            tname = f"@{t['username']}" if t["username"] else f"ID {t['id']}"
            mark = medals[i] if i < 3 else f" {i + 1}."
            lines.append(f"{mark} {tname} · {fg(t['gold'])} {pe('coin')}")
        body = "\n".join(lines) or "Пока никого."
        await safe_reply(m, f"{pe('trophy')} <b>Топ по голде</b>\n\n{body}")
    elif kind == "бонус":
        now = int(time.time())
        left = BONUS_HOURS * 3600 - (now - u["last_bonus"])
        if left > 0:
            h, rem = divmod(left, 3600)
            await safe_reply(m, f"{pe('clock')} Бонус будет доступен через {h} ч {rem // 60} мин")
        else:
            db.add_gold(uid, BONUS_GOLD)
            db.set_last_bonus(uid, now)
            await safe_reply(m, f"{pe('party')} +{BONUS_GOLD} {pe('coin')}! Возвращайся через {BONUS_HOURS} ч.")


_seen = {}      # (chat_id, message_id) -> ts: одно сообщение = одна игра



@DP.my_chat_member()
async def on_my_chat_member(u: ChatMemberUpdated):
    """Добавление/удаление бота в ЛЮБОЙ группе, без настройки на каждую.
    Каналы игнорируем. Голда у юзера общая на все группы."""
    if u.chat.type not in ("group", "supergroup"):
        return
    st = u.new_chat_member.status
    if st in ("member", "administrator", "restricted"):
        db.upsert_group(u.chat.id, u.chat.title or "",
                        u.from_user.id if u.from_user else 0)
        try:
            await BOT.send_message(
                u.chat.id,
                GROUP_HELLO_ADMIN if st == "administrator"
                else GROUP_HELLO_NOT_ADMIN.format(me=ME))
        except TelegramAPIError:
            pass                       # бот не может писать — не падаем
    elif st in ("left", "kicked"):
        db.deactivate_group(u.chat.id)


# ---------- настройки группы (только админ группы) ----------

GSET_PRESETS = {
    "min_stake": [("1", 1), ("10", 10), ("50", 50), ("100", 100)],
    "max_stake": [("1к", 1000), ("10к", 10000), ("100к", 100000), ("∞", 0)],
    "flood_sec": [("2с", 2), ("3с", 3), ("5с", 5), ("10с", 10)],
}


async def is_group_admin(uid, chat_id):
    """Админ ли юзер в группе (creator/administrator)."""
    try:
        mem = await BOT.get_chat_member(chat_id, uid)
        return mem.status in ("administrator", "creator")
    except TelegramAPIError:
        return False


def group_settings_view(chat_id, g):
    text = (
        f"<b>Настройки группы</b>\n\n"
        f"Игры: <b>{'включены' if g['games_on'] else 'выключены'}</b>\n"
        f"Мин. ставка: <b>{fg(g['min_stake'])} G</b>\n"
        f"Макс. ставка: <b>{fg(g['max_stake']) if g['max_stake'] else 'без лимита'}</b>\n"
        f"Анти-флуд: <b>{g['flood_sec']} с</b> между играми группы\n\n"
        f"Менять может только админ группы."
    )
    cur = {k: g[k] for k in GSET_PRESETS}
    rows = [[btn("Игры: ВКЛ" if not g["games_on"] else "Игры: ВЫКЛ",
                 f"gset:{chat_id}:games_on:{0 if g['games_on'] else 1}", icon="games")]]
    for key, presets in GSET_PRESETS.items():
        rows.append([btn(f"{'● ' if v == cur[key] else ''}{lbl}",
                         f"gset:{chat_id}:{key}:{v}") for lbl, v in presets])
    return text, kb(*rows)


@DP.message(Command("settings"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_group_settings(m: Message):
    if not await is_group_admin(m.from_user.id, m.chat.id):
        await safe_reply(m, "Настройки — только для админов группы")
        return
    g = db.get_group(m.chat.id)
    text, markup = group_settings_view(m.chat.id, g)
    await safe_reply(m, text, reply_markup=markup)


@DP.message(Command("games_on"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_games_on(m: Message):
    if not await is_group_admin(m.from_user.id, m.chat.id):
        await safe_reply(m, "Только админ группы")
        return
    db.set_group(m.chat.id, games_on=1)
    await safe_reply(m, f"{pe('party')} Игры в этой группе включены")


@DP.message(Command("games_off"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_games_off(m: Message):
    if not await is_group_admin(m.from_user.id, m.chat.id):
        await safe_reply(m, "Только админ группы")
        return
    db.set_group(m.chat.id, games_on=0)
    await safe_reply(m, f"{pe('sad')} Игры в этой группе выключены")


@DP.callback_query(F.data.startswith("gset:"))
async def cb_gset(c: CallbackQuery):
    _, cid, key, val = c.data.split(":")
    cid = int(cid)
    if not await is_group_admin(c.from_user.id, cid):
        await c.answer("Только админ группы", show_alert=True)
        return
    ival = int(val)
    if key == "games_on" and ival in (0, 1):
        db.set_group(cid, games_on=ival)
    elif key in GSET_PRESETS:
        allowed = [v for _, v in GSET_PRESETS[key]]      # только пресеты панели,
        if ival in allowed:                              # подделка callback не пройдёт
            db.set_group(cid, **{key: ival})
    g = db.get_group(cid)
    text, markup = group_settings_view(cid, g)
    try:
        await show(c, text, reply_markup=markup)
    except TelegramAPIError:
        pass
    await c.answer("Сохранено")


@DP.message(F.chat.type.in_({"group", "supergroup", "private"}), F.text)
async def text_cmd(m: Message):
    """Текстовые команды в группах и личке, без «/» и кнопок.
    Порядок: инфо-команды → игры → «unknown» → молча игнорируем."""
    if not m.from_user or m.from_user.is_bot:
        return
    mk = (m.chat.id, m.message_id)
    if mk in _seen:                     # защита от двойной обработки
        return
    _seen[mk] = time.time()
    if len(_seen) > 5000:               # не даём памяти расти
        for k in sorted(_seen, key=_seen.get)[:2500]:
            _seen.pop(k, None)
    db.log_update(m.chat.id, m.chat.type, m.from_user.id, (m.text or "")[:60])
    cmd = parse_group_cmd(m.text)
    if not cmd:
        return                          # обычная болтовня — молча
    uid = m.from_user.id
    g = db.get_group(m.chat.id) if m.chat.type != "private" else None
    if g and "game" in cmd and not g["games_on"]:
        return                          # админ группы выключил игры
    u = db.get_user(uid)
    if not u:                           # авто-регистрация, /start не нужен
        db.upsert_user(uid, m.from_user.username)
        u = db.get_user(uid)
    if "info" in cmd:                   # инфо-команды: без кулдауна вообще
        await group_info(m, uid, cmd["info"])
        return
    if "unknown" in cmd:
        await safe_reply(m, f"Такой игры нет. Доступны: {GAMES_LIST_TEXT}")
        return
    stake = cmd.get("stake")
    if stake is None:                   # валидация ДО группового лимита:
        await safe_reply(m,
            f"{pe('coin')} Сколько ставишь? Число до 1 млрд · «все» = весь баланс\n"
            "Например: <b>Футбол 100</b> · <b>Слоты 1к</b> · <b>Кубы 100 малые</b>")
        return                          # «Футбол 0» больше не блокирует группу
    if cmd.get("need_side"):
        await safe_reply(m, "Выбери сторону: <b>Кубы 100 малые</b> или <b>Кубы 100 большие</b>")
        return
    left = cd_check(f"u{uid}")
    if left:
        await safe_reply(m, f"{pe('clock')} Подожди {max(1, math.ceil(left))} с")
        return
    if g:
        gleft = cd_check(f"g{m.chat.id}", g["flood_sec"])   # лимит на группу
        if gleft:
            await safe_reply(m, f"{pe('clock')} Играем по очереди — подожди {max(1, math.ceil(gleft))} с")
            return
    stake_amt = db.balance(uid) if stake == "all" else stake
    if g and stake_amt is not None:
        if stake_amt < g["min_stake"]:
            await safe_reply(m, f"Мин. ставка в этой группе: <b>{fg(g['min_stake'])} G</b>")
            return
        if g["max_stake"] and stake_amt > g["max_stake"]:
            await safe_reply(m, f"Макс. ставка в этой группе: <b>{fg(g['max_stake'])} G</b>")
            return
    if stake == "all":
        stake, err = charge_uid(uid, 0)             # 0 = весь баланс
    else:
        if stake <= 0:
            await safe_reply(m, "Ставка должна быть больше нуля")
            return
        stake, err = charge_uid(uid, stake)
    if err:
        await safe_reply(m, err)
        return
    gid = g["chat_id"] if g else None
    game = cmd["game"]
    if game in DICE_GAMES:
        await play_dice_game(uid, game, stake, m, back=False, group_id=gid)
    elif game == "cube":
        await roll_cube(uid, stake, cmd["side"], m, back=False, group_id=gid)


# ---------- приветствие при добавлении в группу ----------
GROUP_HELLO_ADMIN = (
    f"{pe('ball')} Готов играть в этой группе!\n\n"
    "Просто напиши название игры и ставку — без команд и кавычек:\n"
    "<b>Футбол 100</b> · <b>Баскетбол 100</b> · <b>Боулинг 100</b>\n"
    "<b>Кубы 100 малые</b> · <b>Дартс 100</b> · <b>Слоты 100</b>\n\n"
    "Ставка: число, «все» (весь баланс), «1к» = 1 000, «1кк» = 1 000 000\n\n"
    "Ещё: Баланс · Профиль · Топ · Бонус · Задания · Магазин · Помощь"
)
GROUP_HELLO_NOT_ADMIN = (
    f"{pe('ball')} Привет! Я тут, но пока НЕ вижу ваши сообщения — так устроен Telegram.\n\n"
    "Как включить (10 секунд):\n"
    "1. Нажми на название группы\n"
    "2. Нажми на меня → «Назначить админом» → подтвердить\n"
    "3. Напиши <b>Футбол 100</b> — и поехали!\n\n"
    "Или глобально: @BotFather → /setprivacy → выбрать бота → Disable.\n\n"
    "А пока есть два способа без админки:\n"
    "• напиши <b>Футбол 100</b> ответом на это сообщение\n"
    "• или с упоминанием: <b>/футбол@{me} 100</b>"
)


@DP.message(F.new_chat_members)
async def on_added(m: Message):
    """Страховка: если my_chat_member пропущен, группу всё равно пишем в БД.
    Приветствие шлёт ТОЛЬКО on_my_chat_member — иначе в группе будет дубль."""
    if not m.new_chat_members or not any(u.is_bot and u.id == BOT.id for u in m.new_chat_members):
        return
    if m.chat.type in ("group", "supergroup"):
        db.upsert_group(m.chat.id, m.chat.title or "",
                        m.from_user.id if m.from_user else 0)


if __name__ == "__main__":
    asyncio.run(main())


# перехват неизвестных кнопок: ВСЕГДА последним,
# иначе заберёт коллбэки настроек групп (gset:)
@DP.callback_query()
async def cb_fallback(c: CallbackQuery):
    await c.answer()
