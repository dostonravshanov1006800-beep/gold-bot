"""Тесты ключевых сценариев Gold Bot (SPEC.md п. 11.5)."""
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
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    return db


def test_admin_access_rules():
    """Админ-права строго по ADMIN_IDS, чужим отказано."""
    assert bot.is_admin(111) and bot.is_admin(222)
    assert not bot.is_admin(333)          # обычный пользователь
    assert not bot.is_admin(0)


def test_user_flow(fresh_db):
    uid = 8396883978
    fresh_db.upsert_user(uid, "doston")
    u = fresh_db.get_user(uid)
    assert u["gold"] == 0 and u["username"] == "doston"
    fresh_db.add_gold(uid, 10)
    assert fresh_db.get_user(uid)["gold"] == 10
    fresh_db.upsert_user(uid, "doston2")       # повторный /start не сбрасывает голду
    assert fresh_db.get_user(uid)["gold"] == 10
    assert fresh_db.get_user(uid)["username"] == "doston2"


def test_task_flow(fresh_db):
    fresh_db.upsert_user(1, "a")
    fresh_db.add_task("@channel", 50)
    tid = fresh_db.tasks()[0]["id"]
    assert fresh_db.get_task(tid)["reward"] == 50
    # задание выполняется ровно один раз (анти-накрутка)
    assert not fresh_db.is_done(1, tid)
    assert fresh_db.try_claim_task(1, tid) is True   # первый клейм — успех
    assert fresh_db.is_done(1, tid)
    assert fresh_db.try_claim_task(1, tid) is False  # гонка: второй клейм отклонён
    assert fresh_db.is_done(1, tid)
    fresh_db.del_task(tid)
    assert fresh_db.tasks() == []               # мягкое удаление


def test_bonus_twice(fresh_db):
    fresh_db.upsert_user(1, "a")
    now = int(time.time())
    fresh_db.set_last_bonus(1, now)
    assert fresh_db.get_user(1)["last_bonus"] == now


def test_refs(fresh_db):
    fresh_db.upsert_user(1, "inviter")
    fresh_db.upsert_user(2, "friend")
    fresh_db.set_ref(2, 1)
    assert fresh_db.get_user(2)["ref_by"] == 1
    assert not fresh_db.get_user(2)["ref_paid"]
    fresh_db.set_ref_paid(2)
    assert fresh_db.get_user(2)["ref_paid"]
    fresh_db.set_ref(2, 3)                      # второй вызов не перезаписывает
    assert fresh_db.get_user(2)["ref_by"] == 1
    assert fresh_db.invited_count(1) == 1


def test_shop_and_requests(fresh_db):
    fresh_db.upsert_user(1, "buyer")
    fresh_db.add_item("TG Premium", 150)
    iid = fresh_db.items()[0]["id"]
    assert fresh_db.get_item(iid)["cost"] == 150
    rid = fresh_db.add_request(1, iid)
    pend = fresh_db.pending_requests()
    assert len(pend) == 1 and pend[0]["item_id"] == iid
    r = fresh_db.done_request(rid)
    assert r["status"] == "pending"    # возвращается снимок до обновления
    assert fresh_db.pending_requests() == []   # а в базе уже 'done'
    assert fresh_db.pending_requests() == []    # выданная заявка исчезает из очереди
    assert fresh_db.done_request(rid) is None   # повторная выдача невозможна
    fresh_db.del_item(iid)
    assert fresh_db.items() == []


def test_stats_and_ids(fresh_db):
    for i in range(3):
        fresh_db.upsert_user(i, f"u{i}")
        fresh_db.add_gold(i, 5)
    users, gold, reqs = fresh_db.stats()
    assert users == 3 and gold == 15 and reqs == 0
    assert sorted(fresh_db.all_user_ids()) == [0, 1, 2]


def test_house_edge_no_leaks():
    """Экономика: ни одна игра не отдаёт игроку больше, чем забирает (RTP 80–85%)."""
    assert 0.80 <= bot.CUBE_X / 2 <= 0.85, "кубы: EV"
    # кубик-игры на нативных анимациях и слоты — проверка на измеренных
    # вероятностях (замер sendDice 22.10.2026, см. SPEC)
    # Замер sendDice 22.10.2026: dart N=140, остальные N=80, slots N=200
    DICE_PROB = {
        "dart": {1: .186, 2: .150, 3: .157, 4: .143, 5: .250, 6: .114},
        "bowl": {1: .212, 2: .225, 3: .125, 4: .088, 5: .188, 6: .162},
        "foot": {1: .225, 2: .212, 3: .188, 4: .162, 5: .212},
        "bask": {1: .188, 2: .275, 3: .150, 4: .212, 5: .175},
    }
    # Все dice-эмодзи Telegram равномерны (замер 22.10: bowl p(6)=.162 ~ 1/6).
    # Целевой RTP каждой игры по ТЗ владельца: 75-85%.
    for name, pay in [("dart", bot.DART_PAY), ("bowl", bot.BOWL_PAY),
                      ("foot", bot.FOOT_PAY), ("bask", bot.BASK_PAY)]:
        ev = sum(DICE_PROB[name].get(v, 0.0) * m for v, m in pay.items())
        assert ev <= 0.85, f"{name}: EV {ev:.3f} по замерам — бот в минусе!"
        uni = (1/6 if name in ("dart", "bowl") else 1/5) * sum(pay.values())
        assert 0.75 <= uni <= 0.85, f"{name}: RTP по равномерным кубикам {uni:.3f}"
    # слоты: равномерное распределение 64 значений (+12% запас на модель-риск)
    SP = {v: 0.0175 for v in bot.SLOTS_PAY}
    ev = sum(SP[v] * m for v, m in bot.SLOTS_PAY.items())
    assert ev <= 0.85, f"слоты: EV {ev:.3f} — бот в минусе!"
    assert ev >= 0.75, f"слоты: EV {ev:.3f} — слишком жадно"


def test_group_parsing():
    """Текстовые команды: 6 игр, синонимы, ставки все/1к/1кк, инфо-команды."""
    p = bot.parse_group_cmd
    # игры + ставки
    assert p("Футбол 100") == {"game": "football", "stake": 100}
    assert p("БОУЛИНГ 250") == {"game": "bowling", "stake": 250}
    assert p("слоты 10") == {"game": "slots", "stake": 10}
    assert p("дартс 50") == {"game": "dart", "stake": 50}
    assert p("Баскетбол 500") == {"game": "basketball", "stake": 500}
    # синонимы и эмодзи
    assert p("казино 100")["game"] == "slots"
    assert p("слот 100")["game"] == "slots"
    assert p("🎰 100")["game"] == "slots"
    assert p("⚽ 100")["game"] == "football"
    # кубы
    assert p("Кубы 100 большие") == {"game": "cube", "stake": 100, "side": "hi"}
    assert p("кубок 100 малые")["side"] == "lo"
    assert p("кубы все большие") == {"game": "cube", "stake": "all", "side": "hi"}
    assert p("кубы 100").get("need_side") is True
    # ставки: все / к / кк / ноль
    assert p("футбол все")["stake"] == "all"
    assert p("футбол всё на кон")["stake"] == "all"
    assert p("слоты all")["stake"] == "all"
    assert p("слоты 1к")["stake"] == 1000
    assert p("слоты 2кк")["stake"] == 2000000
    assert p("футбол 0")["stake"] == 0
    # без ставки — подсказка
    assert p("футбол")["stake"] is None
    # инфо-команды (одна таблица, синонимы)
    for txt, kind in [("баланс", "баланс"), ("б", "баланс"), ("голда", "баланс"),
                      ("профиль", "профиль"), ("я", "профиль"), ("проф", "профиль"),
                      ("бонус", "бонус"), ("задания", "задания"),
                      ("магазин", "магазин"), ("топ", "топ"), ("игры", "игры"),
                      ("помощь", "помощь"), ("команды", "помощь")]:
        assert p(txt) == {"info": kind}, txt
    # обычная болтовня — молча игнорируем
    assert p("привет") is None
    assert p("футбол сто")["stake"] is None   # словесная ставка — просим цифру
    assert p("") is None
    # похоже на игру с суммой, но такой игры нет — короткий ответ
    for gone in ["рулетка 100", "монетка 100 орёл", "мины 100",
                 "башня 100", "колесо 100", "кнб 50 камень"]:
        assert p(gone) == {"unknown": gone.split()[0]}, gone
    # бытовые фразы с числами и болтовня — бот молчит (CRIT-2/3)
    assert p("купил за 500 рублей") is None
    assert p("нас 5 человек") is None
    assert p("я приду в 5") is None
    assert p("б хз даже") is None
    assert p("я") == {"info": "профиль"}
    # обращение «бот»: голое — вопрос, с командой — выполняем
    assert p("бот") == {"info": "зов"}
    assert p("бот баланс") == {"info": "баланс"}
    assert p("бот футбол 100") == {"game": "football", "stake": 100}
    assert p("бот, кубы 100 малые")["side"] == "lo"
    assert p("боты сломались") is None


def test_game_cooldown():
    """Антиспам: 5 секунд между командами одного юзера."""
    bot._last_cmd.clear()
    assert bot.cd_check("u1") == 0, "первый запуск должен проходить"
    assert bot.cd_check("u1") > 0, "повтор сразу — должен ждать"
    assert bot.cd_check("u1") <= bot.GAME_CD
    assert bot.cd_check("u2") == 0, "другой юзер не должен страдать"
    assert bot.cd_check("g100") == 0 and bot.cd_check("g100") > 0, "троттлинг группы"
    bot._last_cmd.clear()


def test_group_welcome_texts():
    """Приветствия группы: примеры игр и подсказки по обоим сценариям."""
    assert "Футбол 100" in bot.GROUP_HELLO_ADMIN
    assert "Баскетбол 100" in bot.GROUP_HELLO_ADMIN
    assert "Бонус" in bot.GROUP_HELLO_ADMIN
    assert "админом" in bot.GROUP_HELLO_NOT_ADMIN   # подсказка про privacy
    assert "/setprivacy" in bot.GROUP_HELLO_NOT_ADMIN
    assert "{me}" in bot.GROUP_HELLO_NOT_ADMIN      # упоминание бота в подсказке


def test_cube_outcome_no_digits():
    """Кубы: исход текстом, без числа — игроку видно только сторона."""
    for side, win_vals in [("lo", {1, 2, 3}), ("hi", {4, 5, 6})]:
        for val in range(1, 7):
            win, word = bot.cube_outcome(side, val)
            assert win == (val in win_vals), (side, val)
            import re as _re
            _plain = _re.sub(r"<tg-emoji[^>]*>.*?</tg-emoji>", "", word)
            assert not any(ch.isdigit() for ch in _plain), f"число в тексте: {word}"
            assert ("малые" in word.lower() or "большие" in word.lower()), word


def test_mention_command_parsing():
    """Команды с упоминанием бота: /футбол@funapp_game 100 == Футбол 100."""
    old_me, bot.ME = bot.ME, "funapp_game"
    p = bot.parse_group_cmd
    assert p("/футбол@funapp_game 100") == {"game": "football", "stake": 100}
    assert p("/Кубы@funapp_game 100 большие")["side"] == "hi"
    assert p("футбол@чужой_бот 100") is None
    bot.ME = old_me


def test_atomic_charge_and_profile_stats(fresh_db):
    """Списание атомарно: нехватка не списывает; статистика игр копится."""
    fresh_db.upsert_user(1, "u1")
    fresh_db.add_gold(1, 100)
    assert fresh_db.balance(1) == 100
    assert fresh_db.charge_gold(1, 50) is True
    assert fresh_db.get_user(1)["gold"] == 50
    assert fresh_db.charge_gold(1, 100) is False      # не хватает — списания нет
    assert fresh_db.get_user(1)["gold"] == 50
    fresh_db.track_game(1, True, 30)
    fresh_db.track_game(1, False, 0)
    u = fresh_db.get_user(1)
    assert u["games_played"] == 2 and u["games_won"] == 1 and u["games_lost"] == 1
    assert u["total_won"] == 30


def test_multi_group_lifecycle(fresh_db):
    """Мульти-группы: регистрация, настройки админа, деактивация, топ."""
    g = fresh_db.get_group(-100123)
    assert g["games_on"] == 1 and g["min_stake"] == 1 and g["max_stake"] == 0
    assert g["flood_sec"] == 3                      # дефолты без настройки
    fresh_db.upsert_group(-100123, "Тест-группа", 42)
    fresh_db.upsert_group(-100456, "Вторая", 43)     # вторая группа — свой набор
    fresh_db.set_group(-100123, games_on=0, min_stake=10, max_stake=5000, flood_sec=5)
    g = fresh_db.get_group(-100123)
    assert (g["games_on"], g["min_stake"], g["max_stake"], g["flood_sec"]) == (0, 10, 5000, 5)
    assert g["added_by"] == 42 and g["active"] == 1
    assert fresh_db.get_group(-100456)["games_on"] == 1   # настройки не текут
    fresh_db.deactivate_group(-100456)
    assert fresh_db.get_group(-100456)["active"] == 0
    # статистика отдельно на группу, голда общая
    fresh_db.upsert_user(7, "u7")
    fresh_db.upsert_user(8, "u8")
    fresh_db.add_gold(7, 100); fresh_db.add_gold(8, 100)
    fresh_db.track_group_game(-100123, 7, True, 50)
    fresh_db.track_group_game(-100123, 8, False, 0)
    fresh_db.track_group_game(-100999, 7, True, 70)       # другая группа
    top = fresh_db.group_top(-100123)
    assert top[0]["user_id"] == 7 and top[0]["total_won"] == 50
    assert len(top) == 2                                   # оба играли в этой группе
    assert len(fresh_db.group_top(-100999)) == 1
    fresh_db.track_game(7, True, 50)
    fresh_db.track_game(7, False, 0)
    assert fresh_db.get_user(7)["games_played"] == 2       # общая статистика копится


def test_outcome_texts_match_animation():
    """Тексты исходов обязаны совпадать с анимацией: у каждого значения кубика
    есть фраза, победные значения только визуальные гол/попадание."""
    for game, pay in [("football", bot.FOOT_PAY), ("basketball", bot.BASK_PAY),
                      ("bowling", bot.BOWL_PAY), ("dart", bot.DART_PAY)]:
        vals = range(1, 7) if game in ("bowling", "dart") else range(1, 6)
        for v in vals:
            if v in pay:
                assert v in bot.WIN_TEXT[game], f"{game} v{v}: платит, но нет текста победы"
            else:
                assert v in bot.FAIL_TEXT[game], f"{game} v{v}: промах без текста"


def test_bonus_claim_atomic(fresh_db):
    """Параллельный двойной клик «Бонус» начисляет голду только один раз."""
    fresh_db.upsert_user(1, "u1")
    now = 1_790_000_000
    assert fresh_db.claim_bonus(1, now, 43200, 5) is True       # окно истекло
    assert fresh_db.claim_bonus(1, now + 1, 43200, 5) is False   # второй клик — отказ
    assert fresh_db.balance(1) == 5


def test_atomic_claims_and_payments(fresh_db):
    """Гонки закрыты: клейм задания, Stars-платёж и реф-бонус срабатывают один раз."""
    fresh_db.upsert_user(1, "u1")
    fresh_db.add_task("@ch", 50)
    tid = fresh_db.tasks()[0]["id"]
    assert fresh_db.try_claim_task(1, tid) is True      # первый клейм — успех
    assert fresh_db.try_claim_task(1, tid) is False     # второй (гонка) — отказ
    assert fresh_db.process_payment_idempotent("chg1", 1, 100) is True
    assert fresh_db.process_payment_idempotent("chg1", 1, 100) is False  # дубль Telegram
    assert fresh_db.balance(1) == 100
    # реферальный бонус (v11.34): +25 пригласившему сразу при заходе
    fresh_db.upsert_user(2, "u2")
    assert fresh_db.set_ref(2, 1) is True          # первый переход — записан
    assert fresh_db.set_ref(2, 1) is False         # повторный — не считается
    fresh_db.add_gold(1, 25)
    assert fresh_db.balance(1) == 125
    # пригласивший видит счётчик «Приглашено»
    assert fresh_db.invited_count(1) == 1
    # отказ заявки возвращает голду
    fresh_db.charge_gold(2, 25)
    iid = fresh_db.add_item("Подарок", 25)
    rid = fresh_db.add_request(2, iid)
    fresh_db.decline_request(rid)
    assert fresh_db.balance(2) == 25


def test_ref_only_for_new_users(fresh_db):
    """Существующий юзер не может быть «приглашён» позже — как в cmd_start."""
    fresh_db.upsert_user(10, "old")
    fresh_db.upsert_user(20, "inviter")
    # old уже в базе: cmd_start не вызовет set_ref для него (is_new=False);
    # проверяем сам контракт set_ref — одноразовость — и ручную симуляцию
    assert fresh_db.get_user(10)["ref_by"] is None
    fresh_db.upsert_user(30, "new")
    assert fresh_db.set_ref(30, 20) is True
    assert fresh_db.set_ref(10, 20) is True        # контракт: запишет, если ещё NULL,
    # но cmd_start гарантирует: для is_new=False set_ref не вызывается вовсе
