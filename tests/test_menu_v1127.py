import bot


def test_slash_commands_parse_as_info():
    for cmd, kind in [("/balance", "баланс"), ("/bonus", "бонус"), ("/help", "помощь"),
                      ("/plays", "игры"), ("/top", "топ")]:
        assert bot.parse_group_cmd(cmd) == {"info": kind}, cmd


def test_game_cards_cover_all_games():
    for g in ("football", "basketball", "bowling", "cubes", "dart", "slots"):
        assert g in bot.GAME_CARDS
        text = bot.game_card_text(g, 1234)
        assert "1 234" in text and "выбери ставку" in text


def test_cards_show_multipliers_in_expandable_quote():
    """v11.40 (просьба юзера 22.10): у КАЖДОЙ игры — свои множители
    в сворачиваемой цитате, референс = первое фото юзера."""
    import re
    for g in ("football", "basketball", "bowling", "cubes", "dart", "slots"):
        t = bot.game_card_text(g, 0)
        assert "<blockquote expandable>" in t
        m = re.search(r"Множители:</b>\n([^<]+)</blockquote>", t)
        assert m and re.search(r"x\d", m.group(1)), f"у {g} нет множителей"
        assert len(t) <= 1024, f"caption {g} длиннее 1024"


def test_cube_multiplier_matches_constant():
    assert f"x{bot.CUBE_X}" in bot.GAME_CARDS["cubes"][4]


def test_game_images_cover_all_cards():
    assert set(bot.GAME_IMG) == set(bot.GAME_CARDS)
    for url in bot.GAME_IMG.values():
        assert url.startswith("https://") and url.endswith(".png")


def test_stakes_grid_extended():
    assert bot.STAKES == [10, 50, 100, 500, 1000, 5000]
    kb = bot.stakes_kb("football")
    flat = [b.text for row in kb.inline_keyboard for b in row]
    assert "10" in flat and "5000" in flat and "Всё на кон" in flat


def test_stake_prompt_mentions_limit():
    import inspect
    src = inspect.getsource(bot)
    assert "до 1 млрд" in src
