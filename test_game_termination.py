from game_scan import infer_termination
from sgf_ingestion import parse_sgf


def make_game(result: str | None):
    re_property = (
        f"RE[{result}]"
        if result is not None
        else ""
    )

    sgf = (
        f"(;GM[1]FF[4]SZ[9]KM[6.5]{re_property};B[dd];W[ee])"
    ).encode()

    return parse_sgf(sgf)


def test_resignation_is_detected():
    game = make_game("W+R")

    assert infer_termination(game) == "resigned"


def test_long_resignation_form_is_detected():
    game = make_game("B+Resign")

    assert infer_termination(game) == "resigned"


def test_numeric_result_is_scored():
    game = make_game("B+3.5")

    assert infer_termination(game) == "scored"


def test_draw_is_scored():
    game = make_game("0")

    assert infer_termination(game) == "scored"


def test_missing_result_is_incomplete():
    game = make_game(None)

    assert infer_termination(game) == "incomplete"


def test_unknown_result_is_incomplete():
    game = make_game("?")

    assert infer_termination(game) == "incomplete"


def test_timeout_is_other_result():
    game = make_game("W+T")

    assert infer_termination(game) == "other_result"