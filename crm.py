"""CRM-админка внутри бота: один редактируемый экран, мгновенные действия.

Callback-схема: a:<раздел>[:<действие>[:<id>]]. Ввод текста (канал, цена, сумма,
рассылка) ловится через in-memory состояние админа AWAIT[uid].
"""
import asyncio
import json
import logging
import random
import re
import time

from aiogram import F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

import db

# ---------- распределение банка ----------
def draw_bank(bank, users):
    """Банк случайно делится по топу: топ-3 вес 5, места 4-10 вес 3, 11-20 вес 2,
    внутри тира разброс рандомный. Возвращает [(uid, сумма)] ранжированно."""
    users = users[:20]  # розыгрыш только по топ-20
    n = len(users)
    if n == 0 or bank <= 0:
        return []
    ws = [(5 if i < 3 else 3 if i < 10 else 2) * random.uniform(0.8, 1.25)
          for i in range(n)]
    tot = sum(ws)
    exact = [bank * w / tot for w in ws]
    shares = [int(e) for e in exact]
    rem = bank - sum(shares)
    order = sorted(range(n), key=lambda i: exact[i] - shares[i], reverse=True)
    for k in range(rem):
        shares[order[k % n]] += 1
    return [(users[i]["id"], shares[i]) for i in range(n)]


# uid -> {"kind": str, "msg": (chat_id, message_id), ...}
AWAIT = {}


def _await_set(uid, st):
    """Ожидание ввода админа пишется и в базу: смена рана GitHub Actions
    каждые ~5 ч убивала in-memory состояние — «Новое задание» и прочие
    вводы молча отваливались посреди заполнения (22.10, 20:49)."""
    AWAIT[uid] = st
    db.put_meta(f"await:{uid}", json.dumps(st))


def _await_pop(uid):
    st = AWAIT.pop(uid, None)
    raw = db.get_meta(f"await:{uid}")
    db.del_meta(f"await:{uid}")
    if st is None and raw:
        try:
            st = json.loads(raw)
        except Exception:
            st = None
    return st


def _await_get(uid):
    st = AWAIT.get(uid)
    if st:
        return st
    raw = db.get_meta(f"await:{uid}")
    if raw:
        try:
            st = json.loads(raw)
            AWAIT[uid] = st
        except Exception:
            st = None
    return st


def setup(ctx):
    """ctx: bot, dp, pe, btn, kb, fg, is_admin, broadcast_all."""
    DP, BOT = ctx["dp"], ctx["bot"]
    pe, btn, kb, fg = ctx["pe"], ctx["btn"], ctx["kb"], ctx["fg"]
    is_admin = ctx["is_admin"]
    broadcast_all = ctx["broadcast_all"]
    broadcast_groups = ctx["broadcast_groups"]

    # ---------- экраны ----------
    def nav(active):
        def b(label, key, icon):
            return btn(("● " if active == key else "") + label, f"a:{key}", icon)
        return [
            [b("Обзор", "home", "stats"), b("Задания", "tasks", "tasks")],
            [b("Магазин", "shop", "gift"), b("Заявки", "reqs", "tags")],
            [b("Юзеры", "users", "people"), b("Рассылка", "bc", "info")],
            [b("Банк", "bank", "coin"), b("Переводы", "tx", "right")],
            [b("Промокоды", "promo", "star"), b("В группы", "bcg", "info")],
            [b("Экономика", "econ", "stats"), b("Обмен G→Stars", "exch", "stars")],
        ]

    def screen_home():
        users, gold, pend = db.stats()
        text = (
            f"{pe('crown')} <b>CRM · Обзор</b>\n\n"
            f"{pe('people')} Юзеров: <b>{fg(users)}</b>\n"
            f"{pe('coin')} Голды в обороте: <b>{fg(gold)}</b>\n"
            f"{pe('tags')} Заявок в ожидании: <b>{fg(pend)}</b>\n"
            f"{pe('tasks')} Заданий: <b>{len(db.tasks())}</b>\n"
            f"{pe('gift')} Товаров: <b>{len(db.items())}</b>"
        )
        return text, kb(*nav("home"))

    def screen_tasks():
        rows, lines = [], []
        for t in db.tasks():
            lines.append(f"<b>#{t['id']}</b> {t['channel']} · +{fg(t['reward'])} {pe('coin')}")
            rows.append([btn(f"Удалить #{t['id']} {t['channel']}", f"a:tasks:del:{t['id']}", "trash")])
        body = "\n".join(lines) or "Заданий пока нет."
        text = f"{pe('tasks')} <b>CRM · Задания</b>\n\n{body}"
        rows.append([btn("Добавить задание", "a:tasks:add", "plus")])
        return text, kb(*rows, *nav("tasks"))

    def screen_shop():
        rows, lines = [], []
        for i in db.items():
            lines.append(f"<b>#{i['id']}</b> {i['title']} · {fg(i['cost'])} {pe('coin')}")
            rows.append([btn(f"Удалить #{i['id']} {i['title'][:24]}", f"a:shop:del:{i['id']}", "trash")])
        body = "\n".join(lines) or "Товаров пока нет."
        text = f"{pe('gift')} <b>CRM · Магазин</b>\n\n{body}"
        rows.append([btn("Добавить товар", "a:shop:add", "plus")])
        return text, kb(*rows, *nav("shop"))

    def screen_reqs():
        rows, lines = [], []
        reqs = db.pending_requests()
        for r in reqs[:8]:
            who = f"@{db.get_user(r['user_id'])['username']}" if db.get_user(r['user_id']) and db.get_user(r['user_id'])['username'] else f"ID {r['user_id']}"
            lines.append(f"<b>#{r['id']}</b> {who} → {r['title']}")
            rows.append([
                btn(f"Выдать #{r['id']}", f"a:reqs:done:{r['id']}", "check"),
                btn("Отклонить", f"a:reqs:no:{r['id']}", "trash"),
            ])
        body = "\n".join(lines) or "Новых заявок нет."
        if len(reqs) > 8:
            body += f"\n…и ещё {len(reqs) - 8}"
        text = f"{pe('tags')} <b>CRM · Заявки на призы</b>\n\n{body}"
        return text, kb(*rows, *nav("reqs"))

    def screen_users(page=0):
        per = 8
        allu = db.q("SELECT id, username, gold FROM users ORDER BY joined DESC")
        chunk = allu[page * per:(page + 1) * per]
        rows = []
        for u in chunk:
            name = f"@{u['username']}" if u["username"] else f"ID {u['id']}"
            # одна полная кнопка на юзера: имя и баланс не обрезаются,
            # начисление/списание — внутри экрана юзера
            rows.append([btn(f"{name} · {fg(u['gold'])}",
                             f"a:users:v:{u['id']}", "people")])
        pager = []
        if page > 0:
            pager.append(btn("Назад", f"a:users:p:{page - 1}", "back"))
        if (page + 1) * per < len(allu):
            pager.append(btn("Дальше", f"a:users:p:{page + 1}", "right"))
        if pager:
            rows.append(pager)
        body = "Нажми на юзера — внутри начисление и списание." if allu else "Пока никого."
        text = f"{pe('people')} <b>CRM · Юзеры</b> ({len(allu)})\n\n{body}"
        return text, kb(*rows, *nav("users"))

    def screen_user(target):
        u = db.get_user(int(target))
        if not u:
            return screen_users(0)
        name = f"@{u['username']}" if u["username"] else f"ID {u['id']}"
        text = (
            f"{pe('people')} <b>{name}</b>\n\n"
            f"{pe('coin')} Баланс: <b>{fg(u['gold'])}</b>\n"
            f"{pe('info')} ID: <code>{u['id']}</code>"
        )
        return text, kb(
            [btn("Начислить", f"a:users:give:{u['id']}", "plus")],
            [btn("Отнять", f"a:users:take:{u['id']}", "cross")],
            [btn("Переводы", f"a:users:tx:{u['id']}", "right")],
            [btn("Юзеры", "a:users", "back")],
        )

    def screen_bc():
        text = (
            f"{pe('info')} <b>CRM · Рассылка</b>\n\n"
            "Нажми кнопку и пришли текст следующим сообщением. "
            "Поддерживается HTML и премиум-эмодзи."
        )
        return text, kb([btn("Написать рассылку", "a:bc:new", "edit")], *nav("bc"))

    def screen_bank():
        g = db.last_giveaway()
        if g:
            winners = json.loads(g["winners"])
            lines = []
            for w in winners[:5]:
                name = f"@{w['username']}" if w.get("username") else f"ID {w['id']}"
                lines.append(f"{w['rank']}. {name} · +{fg(w['amount'])} {pe('coin')}")
            tail = f"\n…и ещё {len(winners) - 5}" if len(winners) > 5 else ""
            body = (
                f"Последний розыгрыш: банк {fg(g['amount'])} {pe('coin')} на {g['players']} чел\n"
                + "\n".join(lines) + tail
            )
        else:
            body = "Розыгрышей ещё не было."
        text = (
            f"{pe('coin')} <b>CRM · Банк</b>\n\n{body}\n\n"
            "Банк случайно делится по топ-20 по голде: топ-3 — крупные доли, "
            "места 4–10 — средние, 11–20 — меньшие. Все получают уведомления."
        )
        return text, kb([btn("Провести розыгрыш", "a:bank:new", "party")], *nav("bank"))

    def screen_tx():
        trs = db.recent_transfers(10)
        lines = []
        for t in trs:
            fn = f"@{t['fu']}" if t["fu"] else f"ID {t['from_id']}"
            tn = f"@{t['tu']}" if t["tu"] else f"ID {t['to_id']}"
            chat = t["chat"] or "без названия"
            lines.append(f"{fn} → {tn} · {fg(t['amount'])} {pe('coin')} · {chat}")
        body = "\n".join(lines) if lines else "Переводов ещё не было."
        chats = db.q("SELECT chat_id, title, tx_on, tx_confirm FROM groups "
                     "WHERE active=1 ORDER BY added_at DESC")
        rows = []
        for g in chats:
            st = "вкл" if g["tx_on"] else "выкл"
            th = f" · от {fg(g['tx_confirm'])}" if g["tx_confirm"] else ""
            rows.append([btn(f"{g['title'] or g['chat_id']}: {st}{th}",
                             f"a:tx:c:{g['chat_id']}", "right")])
        if not chats:
            rows.append([btn("Чатов с ботом пока нет", "a:tx:none", "right")])
        text = (f"{pe('right')} <b>CRM · Переводы</b>\n\n{body}\n\n"
                "Чаты — вкл/выкл и порог подтверждения:")
        return text, kb(*rows, *nav("tx"))

    def screen_tx_chat(chat_id):
        g = db.one("SELECT chat_id, title, tx_on, tx_confirm FROM groups WHERE chat_id=?",
                   (chat_id,))
        if not g:
            return screen_tx()
        st = "вкл" if g["tx_on"] else "выкл"
        th = f"{fg(g['tx_confirm'])}" if g["tx_confirm"] else "выкл"
        text = (f"{pe('right')} <b>{g['title'] or g['chat_id']}</b>\n\n"
                f"Переводы reply-ом: <b>{st}</b>\n"
                f"Подтверждение сумм больше: <b>{th}</b>\n\n"
                "Порог — суммы выше него отправитель подтверждает кнопкой (30 сек).")
        return text, kb(
            [btn(f"{'Выключить' if g['tx_on'] else 'Включить'}", f"a:tx:sw:{chat_id}",
                 "cross" if g["tx_on"] else "check")],
            [btn(f"Порог: выкл" if not g["tx_confirm"] else f"Порог: {fg(g['tx_confirm'])}",
                 "a:tx:none", "right")],
            [btn("Без подтверждения", f"a:tx:th:{chat_id}:0", "cross"),
             btn("от 10к", f"a:tx:th:{chat_id}:10000", "warn")],
            [btn("от 100к", f"a:tx:th:{chat_id}:100000", "warn"),
             btn("от 1кк", f"a:tx:th:{chat_id}:1000000", "warn")],
            [btn("от 10кк", f"a:tx:th:{chat_id}:10000000", "warn")],
            [btn("Переводы", "a:tx", "back")],
        )

    def screen_user_tx(target):
        u = db.get_user(int(target))
        if not u:
            return screen_users(0)
        name = f"@{u['username']}" if u["username"] else f"ID {u['id']}"
        trs = db.user_transfers(u["id"], 15)
        lines = []
        for t in trs:
            other_id = t["to_id"] if t["from_id"] == u["id"] else t["from_id"]
            other = t["tu"] if t["from_id"] == u["id"] else t["fu"]
            oname = f"@{other}" if other else f"ID {other_id}"
            arrow = "→" if t["from_id"] == u["id"] else "←"
            chat = t["chat"] or "—"
            lines.append(f"{arrow} {oname} · {fg(t['amount'])} {pe('coin')} · {chat}")
        body = "\n".join(lines) if lines else "Переводов не было."
        text = f"{pe('right')} <b>Переводы · {name}</b>\n\n{body}"
        return text, kb([btn("К юзеру", f"a:users:v:{u['id']}", "back")], *nav("users"))

    def screen_promo():
        rows, lines = [], []
        for p in db.promos():
            lim = f"{p['uses']}/{p['max_uses']}" if p["max_uses"] else f"{p['uses']}/∞"
            st = "" if p["active"] else " · выкл"
            lines.append(f"<b>{p['code']}</b> · +{fg(p['amount'])} {pe('coin')} · {lim}{st}")
            rows.append([
                btn(("Выключить" if p["active"] else "Включить") + f" {p['code']}",
                     f"a:promo:t:{p['id']}", "right"),
                btn(f"Удалить {p['code']}", f"a:promo:del:{p['id']}", "trash")])
        body = "\n".join(lines) or "Промокодов пока нет."
        text = (f"{pe('star')} <b>CRM · Промокоды</b>\n\n{body}\n\n"
                "Формат создания: <code>КОД СУММА [лимит]</code>. Лимит — сколько "
                "раз можно активировать, без него — ∞. Один юзер — одна активация. "
                "Активация в чате: просто код или «промокод КОД».")
        rows.append([btn("Создать промокод", "a:promo:new", "plus")])
        return text, kb(*rows, *nav("promo"))

    def screen_bcg():
        chats = db.q("SELECT chat_id, title FROM groups "
                     "WHERE active=1 ORDER BY added_at DESC")
        rows = [[btn(g["title"] or f"ID {g['chat_id']}",
                     f"a:bcg:g:{g['chat_id']}", "right")] for g in chats]
        if not rows:
            rows = [[btn("Групп с ботом пока нет", "a:tx:none", "right")]]
        rows.append([btn("Во все группы сразу", "a:bcg:new", "party")])
        body = ("\n".join("• " + (g["title"] or f"ID {g['chat_id']}")
                           for g in chats)) or "Групп пока нет."
        text = (f"{pe('info')} <b>CRM · Пост в группы</b>\n\n{body}\n\n"
                "Выбери группу — текст уйдёт только в неё. "
                "«Во все группы сразу» — в каждую, где бот активен.")
        return text, kb(*rows, *nav("bcg"))

    def screen_econ():
        """Баланс экономики: добыто/сожжено/в обороте + джекпот + инфляция."""
        KIND_RU = {
            "task": "Задания", "bonus": "Бонусы", "promo": "Промокоды",
            "topup": "Пополнения Stars", "ref": "Рефералы", "bank": "Банк админа",
            "case": "Кейсы (сожжено)", "case_refund": "Возвраты кейсов",
            "level": "Уровни (сожжено)", "shop": "Магазин (сожжено)",
            "clan": "Создание кланов (сожжено)", "clanfee": "Взносы в кланы",
            "exchange": "Обмен в Stars (сожжено)", "jackpot": "Джекпот-выплаты",
            "duel": "Ставки дуэлей", "duel_win": "Победы дуэлей",
            "stake": "Ставки игр", "gwin": "Выигрыши игр",
            "transfer_in": "Переводы (входящие)", "transfer_out": "Переводы (исходящие)",
        }
        BURN = {"case", "level", "shop", "clan", "clanfee", "exchange"}
        MINE = {"task", "bonus", "promo", "topup", "ref", "bank"}

        def render(kinds, title):
            lines = [f"{pe('stats')} <b>{title}</b>"]
            for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
                lines.append(f"• {KIND_RU.get(k, k)}: <b>{fg(v)}</b>")
            return "\n".join(lines) if len(lines) > 1 else f"{title}: нет движения"

        today, week, all_ = db.econ_report()

        def total(kinds, keys):
            return sum(v for k, v in kinds.items() if k in keys)

        burned_today = total(today, BURN)
        mine_today = total(today, MINE)
        users, gold, pend = db.stats()
        infl = mine_today - burned_today
        text = (
            f"{pe('stats')} <b>CRM · Экономика Голды</b>\n\n"
            f"{pe('coin')} В обороте у юзеров: <b>{fg(gold)}</b>\n"
            f"{pe('gem')} Джекпот: <b>{fg(db.jackpot_pool())}</b> · "
            f"Банк войны кланов: <b>{fg(db.clan_pool())}</b>\n"
            f"{pe('up')} Эмиссия за день (добыто − сожжено): "
            f"<b>{'+' if infl >= 0 else ''}{fg(infl)}</b>\n\n"
            + render(today, "Сегодня")
            + "\n\n" + render(week, "За 7 дней") + "\n\n"
            + render(all_, "За всё время")
        )
        return text, kb(*nav("econ"))

    def screen_exch():
        rate = db.stars_rate()
        pend = db.pending_exchanges()
        hist = db.rate_history(4)
        hist_s = " → ".join(
            f"{fg(h['rate'])} ({time.strftime('%d.%m', time.gmtime(h['ts']))})"
            for h in reversed(hist))
        lines = [f"{pe('stars')} <b>CRM · Обмен Голды на Stars</b>\n\n"
                 f"Курс: <b>1 Star = {fg(rate)} G</b> (голда сгорает, Stars шлёт админ вручную)\n"
                 f"История курса: {hist_s}\n"]
        if pend:
            lines.append(f"{pe('inbox')} <b>Заявки:</b>")
            for e in pend:
                u = db.get_user(e["user_id"])
                un = "@" + u["username"] if u and u["username"] else str(e["user_id"])
                lines.append(
                    f"#{e['id']} · {un} · {fg(e['gold'])} G → {e['stars']} Stars")
        else:
            lines.append("Заявок нет.")
        rows = [[btn("Изменить курс", "exch:rate", "edit"),
                 btn("Создать заявку", "exch:add", "plus")]]
        for e in pend:
            rows.append([btn(f"#{e['id']} выдать Stars", f"exch:done:{e['id']}", "check"),
                         btn(f"#{e['id']} отклонить", f"exch:dec:{e['id']}", "cross")])
        return "\n".join(lines), kb(*rows, *nav("exch"))

    SCREENS = {
        "home": screen_home, "tasks": screen_tasks, "shop": screen_shop,
        "reqs": screen_reqs, "users": screen_users, "bc": screen_bc,
        "bank": screen_bank, "tx": screen_tx,
        "promo": screen_promo, "bcg": screen_bcg,
        "econ": screen_econ, "exch": screen_exch,
    }

    async def show(c: CallbackQuery, key, note=None):
        text, markup = SCREENS[key]()
        if note:
            text = f"{note}\n\n{text}"
        try:
            await c.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                logging.warning("crm edit: %s", e)

    async def redraw(chat_id, message_id, key, note=None):
        text, markup = SCREENS[key]()
        if note:
            text = f"{note}\n\n{text}"
        try:
            await BOT.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                logging.warning("crm redraw: %s", e)

    # ---------- вход ----------
    @DP.message(Command("admin"))
    async def cmd_admin(m: Message):
        if not is_admin(m.from_user.id):
            await m.answer(f"{pe('warn')} Только для админа.")
            return
        db.del_meta(f"await:{m.from_user.id}")
        AWAIT.pop(m.from_user.id, None)
        text, markup = screen_home()
        # снос старой reply-клавиатуры «Панель» (мини-апп удалён)
        await m.answer(f"{pe('check')} Клавиатура мини-аппа убрана", reply_markup=ReplyKeyboardRemove())
        await m.answer(text, reply_markup=markup)

    # ---------- навигация и действия ----------
    @DP.callback_query(F.data.startswith("a:"))
    async def cb_crm(c: CallbackQuery):
        if not is_admin(c.from_user.id):
            await c.answer("Только для админа", show_alert=True)
            return
        parts = c.data.split(":")
        sec = parts[1]
        act = parts[2] if len(parts) > 2 else None
        arg = parts[3] if len(parts) > 3 else None
        uid = c.from_user.id
        ref = (c.message.chat.id, c.message.message_id)

        if act is None:
            _await_pop(uid)
            await show(c, sec)
            await c.answer()
            return

        if sec == "tasks" and act == "del":
            db.del_task(int(arg))
            await show(c, "tasks", f"{pe('check')} Задание удалено")
            await c.answer("Удалено")
        elif sec == "shop" and act == "del":
            db.del_item(int(arg))
            await show(c, "shop", f"{pe('check')} Товар удалён")
            await c.answer("Удалено")
        elif sec == "tasks" and act == "check":
            lines = []
            me = await BOT.me()
            for t in db.tasks():
                try:
                    mem = await BOT.get_chat_member(t["channel"], me.id)
                    ok = mem.status in ("administrator", "creator")
                    lines.append(f"{pe('check') if ok else pe('warn')} {t['channel']} — бот {'админ' if ok else 'участник, не админ'}")
                except TelegramAPIError as e:
                    lines.append(f"{pe('cross')} {t['channel']} — бот НЕ в канале (проверка подписок невозможна)")
            if not lines:
                lines = ["Заданий нет — проверять нечего."]
            await c.message.edit_text(
                f"{pe('check')} <b>Доступ бота к каналам</b>\n\n"
                + "\n".join(lines)
                + "\n\n<i>Добавь <b>@funapp_game</b> админом канала, иначе Telegram "
                "не даст боту проверять подписки.</i>",
                reply_markup=kb(*nav("tasks")),
            )
            await c.answer()
        elif sec == "tasks" and act == "add":
            _await_set(uid, {"kind": "addtask", "msg": ref})
            await c.message.edit_text(
                f"{pe('plus')} <b>Новое задание</b>\n\n"
                "Пришли одним сообщением: <code>@канал награда</code>\n"
                "Пример: <code>@mychannel 50</code>",
                reply_markup=kb([btn("Отмена", "a:tasks", "back")]),
            )
            await c.answer()
        elif sec == "shop" and act == "add":
            _await_set(uid, {"kind": "additem", "msg": ref})
            await c.message.edit_text(
                f"{pe('plus')} <b>Новый товар</b>\n\n"
                "Пришли одним сообщением: <code>цена название</code>\n"
                "Пример: <code>150 Telegram Premium 1 мес</code>",
                reply_markup=kb([btn("Отмена", "a:shop", "back")]),
            )
            await c.answer()
        elif sec == "tx" and act == "c":
            text, markup = screen_tx_chat(int(arg))
            await c.message.edit_text(text, reply_markup=markup)
            await c.answer()
        elif sec == "tx" and act == "sw":
            db.tx_toggle(int(arg))
            text, markup = screen_tx_chat(int(arg))
            await c.message.edit_text(text, reply_markup=markup)
            await c.answer()
        elif sec == "exch" and act == "rate":
            _await_set(uid, {"kind": "rate", "msg": ref})
            await c.message.edit_text(
                f"{pe('edit')} <b>Новый курс обмена</b>\n\n"
                "Пришли голду за 1 Star (целое число).\n"
                f"Сейчас: <b>{fg(db.stars_rate())} G</b>",
                reply_markup=kb([btn("Отмена", "a:exch", "back")]))
            await c.answer()
        elif sec == "exch" and act == "add":
            _await_set(uid, {"kind": "exch", "msg": ref})
            await c.message.edit_text(
                f"{pe('plus')} <b>Новая заявка на обмен</b>\n\n"
                "Пришли: <code>user_id количество_Stars</code>\n"
                f"Курс: 1 Star = {fg(db.stars_rate())} G — голда спишется и сгорит,\n"
                "Stars нужно отправить игроку вручную.",
                reply_markup=kb([btn("Отмена", "a:exch", "back")]))
            await c.answer()
        elif sec == "exch" and act == "done":
            e = db.one("SELECT * FROM exchanges WHERE id=?", (int(arg),))
            db.done_exchange(int(arg))
            if e:
                try:
                    await BOT.send_message(
                        e["user_id"],
                        f"{pe('stars')} Обмен выполнен — Stars отправлены админом. "
                        "Проверь сообщение от него.")
                except Exception:
                    pass
            await show(c, "exch", f"{pe('check')} Заявка #{arg} закрыта — Stars выданы")
            await c.answer()
        elif sec == "exch" and act == "dec":
            e = db.decline_exchange(int(arg))
            if e:
                try:
                    await BOT.send_message(
                        e["user_id"],
                        f"{pe('sad')} Заявка на обмен отклонена — "
                        f"{fg(e['gold'])} {pe('coin')} вернулись на баланс.")
                except Exception:
                    pass
            await show(c, "exch", f"{pe('check')} Заявка #{arg} отклонена, голда возвращена")
            await c.answer()
        elif sec == "tx" and act == "th":
            db.tx_set_confirm(int(arg), int(parts[4]))
            text, markup = screen_tx_chat(int(arg))
            await c.message.edit_text(text, reply_markup=markup)
            await c.answer("Порог обновлён")
        elif sec == "tx" and act == "none":
            await c.answer()
        elif sec == "promo" and act == "new":
            _await_set(uid, {"kind": "promo", "msg": ref})
            await c.message.edit_text(
                f"{pe('star')} <b>Новый промокод</b>\n\n"
                "Пришли одним сообщением: <code>КОД СУММА [лимит]</code>\n"
                "Примеры: <code>GOLD500 500</code> · <code>NEWYEAR 1000 100</code>\n"
                "Лимит — без него код безлимитный. Активация — просто код в чате.",
                reply_markup=kb([btn("Отмена", "a:promo", "back")]))
            await c.answer()
        elif sec == "promo" and act == "t":
            p = db.one("SELECT active FROM promos WHERE id=?", (int(arg),))
            db.set_promo(int(arg), not p["active"])
            await show(c, "promo", f"{pe('check')} Промокод обновлён")
            await c.answer()
        elif sec == "promo" and act == "del":
            db.del_promo(int(arg))
            await show(c, "promo", f"{pe('check')} Промокод удалён")
            await c.answer()
        elif sec == "bcg" and act == "new":
            _await_set(uid, {"kind": "bcg", "target": None, "msg": ref})
            await c.message.edit_text(
                f"{pe('info')} <b>Пост во все группы</b>\n\n"
                "Пришли сообщение — оно появится в каждой группе, где бот активен.",
                reply_markup=kb([btn("Отмена", "a:bcg", "back")]))
            await c.answer()
        elif sec == "bcg" and act == "g":
            g = db.one("SELECT title FROM groups WHERE chat_id=?", (int(arg),))
            if not g:
                await show(c, "bcg", f"{pe('warn')} Группа не найдена")
                await c.answer()
                return
            _await_set(uid, {"kind": "bcg", "target": int(arg), "msg": ref})
            await c.message.edit_text(
                f"{pe('info')} <b>Пост в «{g['title'] or arg}»</b>\n\n"
                "Пришли сообщение — оно появится только в этой группе.",
                reply_markup=kb([btn("Отмена", "a:bcg", "back")]))
            await c.answer()
        elif sec == "bcg" and act == "go":
            st = _await_pop(uid)
            if not st or st.get("kind") != "bcg_ready":
                await c.answer("Нечего отправлять", show_alert=True)
                return
            await c.answer("Отправляю…")
            target = st.get("target")
            await c.message.edit_text(f"{pe('clock')} Публикую…")
            if target:
                try:
                    await BOT.send_message(target, st["text"], parse_mode="HTML")
                    note = f"{pe('check')} Отправлено"
                except TelegramAPIError:
                    try:
                        await BOT.send_message(target, st["text"], parse_mode=None)
                        note = f"{pe('check')} Отправлено (без оформления)"
                    except TelegramAPIError:
                        note = f"{pe('sad')} Не доставлено — бот исключён из группы"
            else:
                ok, total = await broadcast_groups(st["text"])
                note = f"{pe('check')} Опубликовано в {ok}/{total} группах"
            await redraw(*st["msg"], "bcg", note)
        elif sec == "users" and act == "tx":
            text, markup = screen_user_tx(int(arg))
            await c.message.edit_text(text, reply_markup=markup)
            await c.answer()
        elif sec == "users" and act == "p":
            text, markup = screen_users(int(arg))
            await c.message.edit_text(text, reply_markup=markup)
            await c.answer()
        elif sec == "users" and act == "v":
            text, markup = screen_user(arg)
            await c.message.edit_text(text, reply_markup=markup)
            await c.answer()
        elif sec == "users" and act == "take":
            _await_set(uid, {"kind": "take", "target": int(arg), "msg": ref})
            u = db.get_user(int(arg))
            name = f"@{u['username']}" if u and u["username"] else f"ID {arg}"
            await c.message.edit_text(
                f"{pe('sad')} <b>Списать у {name}</b> (баланс {fg(u['gold'])} {pe('coin')})\n\n"
                "Пришли сумму списания или «все» — обнулит баланс до нуля.",
                reply_markup=kb([btn("Отмена", f"a:users:v:{arg}", "back")]),
            )
            await c.answer()
        elif sec == "users" and act == "give":
            _await_set(uid, {"kind": "give", "target": int(arg), "msg": ref})
            u = db.get_user(int(arg))
            name = f"@{u['username']}" if u and u["username"] else f"ID {arg}"
            await c.message.edit_text(
                f"{pe('coin')} <b>Начислить {name}</b>\n\n"
                "Пришли число. Отрицательное спишет голду.",
                reply_markup=kb([btn("Отмена", f"a:users:v:{arg}", "back")]),
            )
            await c.answer()
        elif sec == "reqs" and act in ("done", "no"):
            rid = int(arg)
            r = db.done_request(rid) if act == "done" else db.decline_request(rid)
            if not r:
                await show(c, "reqs", f"{pe('warn')} Заявка уже обработана")
                await c.answer()
                return
            try:
                if act == "done":
                    await BOT.send_message(r["user_id"], f"{pe('party')} Твой приз «{r['title']}» выдан!")
                else:
                    await BOT.send_message(r["user_id"], f"{pe('sad')} Заявка на «{r['title']}» отклонена")
            except TelegramAPIError:
                pass
            await show(c, "reqs", f"{pe('check')} Выдано" if act == "done" else f"{pe('check')} Отклонено")
            await c.answer("Готово")
        elif sec == "bank" and act == "new":
            _await_set(uid, {"kind": "bank", "msg": ref})
            await c.message.edit_text(
                f"{pe('coin')} <b>Банк розыгрыша</b>\n\n"
                "Пришли сумму одним сообщением (например <code>1000</code>). "
                "Она случайно распределится по топ-20 по голде: топ-3 — крупные доли, "
                "места 4–10 — средние, 11–20 — меньшие. Все получат уведомления.",
                reply_markup=kb([btn("Отмена", "a:bank", "back")]),
            )
            await c.answer()
        elif sec == "bc" and act == "new":
            _await_set(uid, {"kind": "bc", "msg": ref})
            await c.message.edit_text(
                f"{pe('info')} <b>Текст рассылки</b>\n\nПришли сообщение, которое уйдёт всем.",
                reply_markup=kb([btn("Отмена", "a:bc", "back")]),
            )
            await c.answer()
        elif sec == "bc" and act == "go":
            st = _await_pop(uid)
            if not st or st.get("kind") != "bc_ready":
                await c.answer("Нечего отправлять", show_alert=True)
                return
            await c.answer("Отправляю…")
            await c.message.edit_text(f"{pe('clock')} Рассылка идёт…")
            ok, total = await broadcast_all(st["text"])
            await redraw(*st["msg"], "bc", f"{pe('check')} Рассылка: доставлено {ok}/{total}")
        else:
            await c.answer()

    @DP.message(F.text.in_(["Панель", "Скрыть"]))
    async def legacy_panel(m: Message):
        if not is_admin(m.from_user.id):
            raise SkipHandler
        if m.text == "Панель":
            await m.answer(
                "Мини-апп удалён. Вся админка теперь внутри бота — команда /admin",
                reply_markup=ReplyKeyboardRemove(),
            )
        else:
            await m.answer("Клавиатура убрана.", reply_markup=ReplyKeyboardRemove())

    # ---------- ввод текста админом ----------
    @DP.message(F.text & ~F.text.startswith("/"))
    async def crm_text(m: Message):
        uid = m.from_user.id
        st = _await_get(uid)
        if not st or not is_admin(uid):
            raise SkipHandler  # чужие/обычные сообщения идут дальше по хендлерам
        kind = st["kind"]
        chat_id, mid = st["msg"]

        async def done(key, note):
            _await_pop(uid)
            try:
                await m.delete()
            except TelegramAPIError:
                pass
            await redraw(chat_id, mid, key, note)

        async def bad(note):
            try:
                await m.delete()
            except TelegramAPIError:
                pass
            await BOT.send_message(chat_id, note)

        if kind == "rate":
            try:
                rate = int(m.text.strip())
            except ValueError:
                return await bad(f"{pe('warn')} Нужно целое число (голда за 1 Star)")
            if rate < 100:
                return await bad(f"{pe('warn')} Курс не ниже 100 G за Star")
            db.set_stars_rate(rate)
            await done("exch", f"{pe('check')} Курс: 1 Star = {fg(rate)} G")
        elif kind == "exch":
            parts = m.text.split()
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                return await bad(f"{pe('warn')} Формат: user_id Stars (например 8396883978 10)")
            target, stars = int(parts[0]), int(parts[1])
            u = db.get_user(target)
            if not u or stars <= 0 or stars > 10000:
                return await bad(f"{pe('warn')} Юзер не найден или Stars вне 1–10000")
            ok, gold, reason = db.create_exchange(target, stars)
            if not ok:
                return await bad(f"{pe('warn')} {reason}")
            try:
                await BOT.send_message(
                    target,
                    f"{pe('stars')} Заявка на обмен создана: {stars} Stars за "
                    f"{fg(gold)} {pe('coin')}. Голда списана, Stars придут от админа.")
            except Exception:
                pass
            await done("exch", f"{pe('check')} Заявка: {stars} Stars у юзера {target} (сожжено {fg(gold)} G)")
        elif kind == "addtask":
            parts = m.text.split()
            if len(parts) != 2 or not parts[0].startswith("@") or not parts[1].isdigit() or int(parts[1]) <= 0:
                return await bad(f"{pe('warn')} Формат: @канал награда (например @mychannel 50)")
            db.add_task(parts[0], int(parts[1]))
            await done("tasks", f"{pe('check')} Задание {parts[0]} · +{parts[1]}")
        elif kind == "additem":
            parts = m.text.split(maxsplit=1)
            if len(parts) != 2 or not parts[0].isdigit() or int(parts[0]) <= 0:
                return await bad(f"{pe('warn')} Формат: цена название "
                                "(например 150 Telegram Premium).\n"
                                "Лимиты: <code>150 Товар шт=10 дней=14 рост=25</code> — "
                                "10 штук, коллекция 14 дней, +25% к цене за штуку.")
            opts = {"stock": 0, "days": 0, "step": 0}
            rest = parts[1].strip()
            for key in ("шт", "дней", "рост"):
                m2 = re.search(rf"{key}=(\d+)", rest)
                if m2:
                    opts[{"шт": "stock", "дней": "days", "рост": "step"}[key]] = int(m2.group(1))
                    rest = re.sub(rf"\s*{key}=\d+", "", rest).strip()
            db.add_item(rest, int(parts[0]), stock=opts["stock"],
                        days=opts["days"], step=opts["step"])
            extra = (f" · лимит {opts['stock']} шт" if opts["stock"] else "") + \
                    (f" · коллекция {opts['days']} дн" if opts["days"] else "") + \
                    (f" · +{opts['step']}%/шт" if opts["step"] else "")
            await done("shop", f"{pe('check')} Товар «{rest}» · {parts[0]}{extra}")
        elif kind == "give":
            try:
                amount = int(m.text.strip())
            except ValueError:
                return await bad(f"{pe('warn')} Нужно целое число")
            if amount == 0 or not db.get_user(st["target"]):
                return await bad(f"{pe('warn')} Сумма нулевая или юзер не найден")
            db.add_gold(st["target"], amount)
            try:
                await BOT.send_message(st["target"], f"{pe('gift')} Админ начислил тебе {amount} {pe('coin')}")
            except TelegramAPIError:
                pass
            await done("users", f"{pe('check')} Начислено {amount} юзеру {st['target']}")
        elif kind == "take":
            txt = (m.text or "").strip().lower()
            target = st["target"]
            u = db.get_user(target)
            if not u:
                return await bad(f"{pe('warn')} Юзер не найден")
            if txt in ("все", "всё", "весь", "вс"):
                old_gold = db.zero_gold(target)
                try:
                    await BOT.send_message(target, f"{pe('sad')} Админ списал весь твой баланс.")
                except TelegramAPIError:
                    pass
                await done("users", f"{pe('check')} Баланс {target} обнулён (было {fg(old_gold)})")
            else:
                try:
                    amount = int(txt)
                except ValueError:
                    return await bad(f"{pe('warn')} Число или «все»")
                if amount <= 0:
                    return await bad(f"{pe('warn')} Сумма должна быть больше нуля")
                new_gold = db.take_gold(target, amount)
                try:
                    await BOT.send_message(target, f"{pe('sad')} Админ списал у тебя {fg(amount)} {pe('coin')}")
                except TelegramAPIError:
                    pass
                await done("users", f"{pe('check')} Списано {fg(amount)} у {target} · осталось {fg(new_gold)}")
        elif kind == "bank":
            try:
                bank = int(m.text.strip())
            except ValueError:
                return await bad(f"{pe('warn')} Нужно целое число")
            if bank <= 0:
                return await bad(f"{pe('warn')} Сумма должна быть больше нуля")
            users = db.top_users(20)
            if not users:
                return await bad(f"{pe('warn')} В боте ещё нет пользователей")
            winners = draw_bank(bank, users)
            names = {u["id"]: u["username"] for u in users}
            for rank, (wuid, amt) in enumerate(winners, 1):
                db.add_gold(wuid, amt)
                try:
                    await BOT.send_message(
                        wuid,
                        f"{pe('party')} <b>Розыгрыш банка</b>\n"
                        f"Место {rank} из {len(winners)} — тебе <b>+{fg(amt)} {pe('coin')}</b>",
                    )
                except TelegramAPIError:
                    pass
            db.add_giveaway(bank, len(winners), json.dumps(
                [{"rank": r, "id": w, "username": names.get(w), "amount": a}
                 for r, (w, a) in enumerate(winners, 1)]
            ))
            await done("bank", f"{pe('check')} Банк {fg(bank)} {pe('coin')} раздан {len(winners)} юзерам, уведомления отправлены")
        elif kind == "promo":
            parts = m.text.split()
            code = parts[0].upper()
            ok_fmt = (2 <= len(parts) <= 3 and code.isalnum() and 3 <= len(code) <= 15
                      and parts[1].isdigit() and int(parts[1]) > 0
                      and (len(parts) == 2 or (parts[2].isdigit() and int(parts[2]) > 0)))
            if not ok_fmt:
                return await bad(f"{pe('warn')} Формат: КОД СУММА [лимит] — например GOLD500 500")
            if db.one("SELECT 1 FROM promos WHERE code=?", (code,)):
                return await bad(f"{pe('warn')} Промокод {code} уже существует")
            db.add_promo(code, int(parts[1]), int(parts[2]) if len(parts) == 3 else 0)
            await done("promo", f"{pe('check')} Промокод {code} создан: +{fg(int(parts[1]))} {pe('coin')}")
        elif kind == "bcg":
            _await_set(uid, {"kind": "bcg_ready", "text": m.html_text,
                             "target": st.get("target"), "msg": st["msg"]})
            try:
                await m.delete()
            except TelegramAPIError:
                pass
            tgt = st.get("target")
            if tgt:
                g = db.one("SELECT title FROM groups WHERE chat_id=?", (tgt,))
                dst = f"Группа: <b>{(g and g['title']) or tgt}</b>"
            else:
                dst = f"Групп: <b>{len(db.all_group_ids())}</b>"
            await BOT.edit_message_text(
                f"{pe('info')} <b>Предпросмотр поста</b>\n\n{m.html_text}\n\n{dst}",
                chat_id=chat_id, message_id=mid,
                reply_markup=kb([btn("Отправить", "a:bcg:go", "check")],
                                [btn("Отмена", "a:bcg", "back")]))
        elif kind == "bc":
            _await_set(uid, {"kind": "bc_ready", "text": m.html_text, "msg": st["msg"]})
            try:
                await m.delete()
            except TelegramAPIError:
                pass
            await BOT.edit_message_text(
                f"{pe('info')} <b>Предпросмотр рассылки</b>\n\n{m.html_text}\n\n"
                f"Получателей: <b>{len(db.all_user_ids())}</b>",
                chat_id=chat_id, message_id=mid,
                reply_markup=kb([btn("Отправить всем", "a:bc:go", "check")], [btn("Отмена", "a:bc", "back")]),
            )
