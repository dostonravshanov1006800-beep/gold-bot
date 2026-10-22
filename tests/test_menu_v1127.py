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


def test_cards_have_no_payout_numbers_except_cubes():
    for g in ("football", "basketball", "bowling", "dart", "slots"):
        assert "x" not in bot.game_card_text(g, 0).split("Текстом")[0].lower().replace("текстом", "")
    assert "x1.64" in bot.game_card_text("cubes", 0)


def test_cube_multiplier_matches_constant():
    assert f"x{bot.CUBE_X}" in bot.GAME_CARDS["cubes"][2]


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
