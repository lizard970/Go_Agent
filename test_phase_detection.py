import pytest

from game_scan import infer_phase, _board_features
from sgf_ingestion import parse_sgf


def test_early_sparse_position_is_opening():
    assert infer_phase(
        move_number=20,
        occupancy_ratio=0.10,
    ) == "opening"


def test_middle_position_is_middlegame():
    assert infer_phase(
        move_number=70,
        occupancy_ratio=0.20,
    ) == "middlegame"


def test_late_dense_position_is_endgame():
    assert infer_phase(
        move_number=130,
        occupancy_ratio=0.40,
    ) == "endgame"


def test_late_but_sparse_position_is_not_forced_into_endgame():
    assert infer_phase(
        move_number=130,
        occupancy_ratio=0.20,
    ) == "middlegame"


def test_board_occupancy_uses_actual_stones_after_capture():
    # 5x5:
    #
    # B[ba]
    # W[aa]
    # B[ab]
    #
    # White stone at aa has no liberties and is captured.
    # Therefore 3 moves have been played, but only 2 stones
    # should remain on the board.
    sgf = b"(;GM[1]FF[4]SZ[5]KM[6.5];B[ba];W[aa];B[ab])"

    game = parse_sgf(sgf)

    stones_on_board, occupancy_ratio = _board_features(
        game,
        3,
    )

    assert stones_on_board == 2
    assert occupancy_ratio == pytest.approx(2 / 25)