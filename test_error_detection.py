import pytest

from error_detection import (
    ErrorDetectionConfig,
    detect_error_events,
)
from game_scan import MoveAnalysis


def make_move(
    move_number: int,
    player: str,
    *,
    winrate_before: float = 0.60,
    winrate_after: float = 0.60,
    score_before: float = 5.0,
    score_after: float = 5.0,
    stones_on_board: int = 20,
    occupancy_ratio: float = 0.20,
    phase: str = "middlegame",
) -> MoveAnalysis:
    """
    Build a MoveAnalysis using BLACK-perspective raw metrics.

    Board/phase values are defaults because ErrorEvent tests do not
    currently depend on phase; they are included so the fixture matches
    the real MoveAnalysis schema.
    """

    if player == "B":
        player_winrate_loss = (
            winrate_before
            - winrate_after
        )

        player_score_loss = (
            score_before
            - score_after
        )

    else:
        # White perspective:
        #
        # white winrate = 1 - black winrate
        # white score lead = -black score lead

        player_winrate_loss = (
            (1.0 - winrate_before)
            - (1.0 - winrate_after)
        )

        player_score_loss = (
            -score_before
            - (-score_after)
        )

    return MoveAnalysis(
        move_number=move_number,
        player=player,
        actual_move="D4",
        best_move_before="Q16",

        black_winrate_before=winrate_before,
        black_score_lead_before=score_before,
        black_winrate_after=winrate_after,
        black_score_lead_after=score_after,

        player_winrate_loss=player_winrate_loss,
        player_score_loss=player_score_loss,

        visits=100,

        stones_on_board=stones_on_board,
        occupancy_ratio=occupancy_ratio,
        phase=phase,

        warnings=[],
    )


def test_no_error_returns_no_events():
    moves = [
        make_move(
            1,
            "B",
            winrate_before=0.60,
            winrate_after=0.58,
            score_before=5.0,
            score_after=4.5,
        )
    ]

    events = detect_error_events(moves)

    assert events == []


def test_single_large_error_becomes_one_event():
    moves = [
        make_move(
            1,
            "B",
            winrate_before=0.70,
            winrate_after=0.60,
            score_before=6.0,
            score_after=3.0,
        )
    ]

    events = detect_error_events(moves)

    assert len(events) == 1

    event = events[0]

    assert event.start_move == 1
    assert event.end_move == 1
    assert event.player == "B"
    assert event.move_numbers == [1]

    assert event.net_winrate_loss == pytest.approx(0.10)
    assert event.net_score_loss == pytest.approx(3.0)

    assert event.peak_winrate_loss == pytest.approx(0.10)
    assert event.peak_score_loss == pytest.approx(3.0)

    assert event.worst_move_number == 1


def test_consecutive_black_candidate_moves_merge():
    moves = [
        make_move(
            1,
            "B",
            winrate_before=0.70,
            winrate_after=0.62,
            score_before=8.0,
            score_after=5.5,
        ),
        make_move(
            3,
            "B",
            winrate_before=0.62,
            winrate_after=0.57,
            score_before=5.5,
            score_after=3.0,
        ),
        make_move(
            5,
            "B",
            winrate_before=0.57,
            winrate_after=0.50,
            score_before=3.0,
            score_after=0.0,
        ),
    ]

    events = detect_error_events(moves)

    assert len(events) == 1

    event = events[0]

    assert event.start_move == 1
    assert event.end_move == 5
    assert event.move_numbers == [1, 3, 5]

    assert event.sum_winrate_loss == pytest.approx(0.20)
    assert event.sum_score_loss == pytest.approx(8.0)

    assert event.net_winrate_loss == pytest.approx(0.20)
    assert event.net_score_loss == pytest.approx(8.0)


def test_one_normal_own_move_may_be_bridged():
    moves = [
        make_move(
            1,
            "B",
            winrate_before=0.70,
            winrate_after=0.62,
            score_before=8.0,
            score_after=5.0,
        ),
        # Black's move 3 is normal and therefore not a candidate.
        make_move(
            3,
            "B",
            winrate_before=0.62,
            winrate_after=0.61,
            score_before=5.0,
            score_after=4.8,
        ),
        make_move(
            5,
            "B",
            winrate_before=0.61,
            winrate_after=0.54,
            score_before=4.8,
            score_after=2.0,
        ),
    ]

    events = detect_error_events(moves)

    assert len(events) == 1
    assert events[0].move_numbers == [1, 5]
    assert events[0].start_move == 1
    assert events[0].end_move == 5


def test_far_apart_errors_become_two_events():
    moves = [
        make_move(
            1,
            "B",
            winrate_before=0.70,
            winrate_after=0.60,
            score_before=8.0,
            score_after=4.0,
        ),
        make_move(
            9,
            "B",
            winrate_before=0.65,
            winrate_after=0.55,
            score_before=6.0,
            score_after=2.0,
        ),
    ]

    events = detect_error_events(moves)

    assert len(events) == 2
    assert events[0].move_numbers == [1]
    assert events[1].move_numbers == [9]


def test_black_and_white_errors_never_merge():
    moves = [
        make_move(
            1,
            "B",
            winrate_before=0.70,
            winrate_after=0.60,
            score_before=8.0,
            score_after=4.0,
        ),
        make_move(
            2,
            "W",
            # Black improves here, therefore White becomes worse.
            winrate_before=0.60,
            winrate_after=0.68,
            score_before=4.0,
            score_after=7.0,
        ),
    ]

    events = detect_error_events(moves)

    assert len(events) == 2
    assert events[0].player == "B"
    assert events[1].player == "W"


def test_white_net_loss_uses_white_perspective():
    move = make_move(
        2,
        "W",
        # Before:
        # black 40% -> white 60%
        #
        # After:
        # black 55% -> white 45%
        #
        # White therefore lost 15 percentage points.
        winrate_before=0.40,
        winrate_after=0.55,
        # Before:
        # black -3 -> white +3
        #
        # After:
        # black +1 -> white -1
        #
        # White therefore lost 4 points.
        score_before=-3.0,
        score_after=1.0,
    )

    events = detect_error_events([move])

    assert len(events) == 1

    event = events[0]

    assert event.player == "W"
    assert event.net_winrate_loss == pytest.approx(0.15)
    assert event.net_score_loss == pytest.approx(4.0)


def test_custom_thresholds_change_candidate_detection():
    moves = [
        make_move(
            1,
            "B",
            winrate_before=0.60,
            winrate_after=0.54,
            score_before=4.0,
            score_after=2.5,
        )
    ]

    strict = ErrorDetectionConfig(
        min_winrate_loss=0.10,
        min_score_loss=3.0,
    )

    relaxed = ErrorDetectionConfig(
        min_winrate_loss=0.05,
        min_score_loss=1.0,
    )

    assert detect_error_events(moves, strict) == []
    assert len(detect_error_events(moves, relaxed)) == 1


def test_invalid_config_is_rejected():
    moves = []

    with pytest.raises(ValueError):
        detect_error_events(
            moves,
            ErrorDetectionConfig(min_winrate_loss=0),
        )

    with pytest.raises(ValueError):
        detect_error_events(
            moves,
            ErrorDetectionConfig(min_score_loss=0),
        )

    with pytest.raises(ValueError):
        detect_error_events(
            moves,
            ErrorDetectionConfig(max_intervening_own_moves=-1),
        )


def test_recovery_break_splits_nearby_errors():
    moves = [
        make_move(
            1,
            "B",
            winrate_before=0.70,
            winrate_after=0.55,
            score_before=8.0,
            score_after=4.0,
        ),

        # Normal Black move, but the position has recovered strongly.
        make_move(
            3,
            "B",
            winrate_before=0.55,
            winrate_after=0.68,
            score_before=4.0,
            score_after=7.0,
        ),

        make_move(
            5,
            "B",
            winrate_before=0.68,
            winrate_after=0.58,
            score_before=7.0,
            score_after=3.0,
        ),
    ]

    events = detect_error_events(moves)

    assert len(events) == 2
    assert events[0].move_numbers == [1]
    assert events[1].move_numbers == [5]


def test_small_recovery_does_not_split_event():
    moves = [
        make_move(
            1,
            "B",
            winrate_before=0.70,
            winrate_after=0.60,
            score_before=8.0,
            score_after=5.0,
        ),

        # Only a small recovery: +4 percentage points.
        make_move(
            3,
            "B",
            winrate_before=0.60,
            winrate_after=0.64,
            score_before=5.0,
            score_after=5.5,
        ),

        make_move(
            5,
            "B",
            winrate_before=0.64,
            winrate_after=0.56,
            score_before=5.5,
            score_after=2.5,
        ),
    ]

    events = detect_error_events(moves)

    assert len(events) == 1
    assert events[0].move_numbers == [1, 5]


def test_custom_recovery_threshold_changes_merging():
    moves = [
        make_move(
            1,
            "B",
            winrate_before=0.70,
            winrate_after=0.55,
            score_before=8.0,
            score_after=4.0,
        ),
        make_move(
            3,
            "B",
            winrate_before=0.55,
            winrate_after=0.63,
            score_before=4.0,
            score_after=6.0,
        ),
        make_move(
            5,
            "B",
            winrate_before=0.63,
            winrate_after=0.53,
            score_before=6.0,
            score_after=2.0,
        ),
    ]

    strict_break = ErrorDetectionConfig(
        recovery_winrate_break=0.05,
    )

    tolerant_break = ErrorDetectionConfig(
        recovery_winrate_break=0.15,
    )

    assert len(detect_error_events(moves, strict_break)) == 2
    assert len(detect_error_events(moves, tolerant_break)) == 1

def test_score_recovery_break_splits_nearby_errors():
    moves = [
        make_move(
            1,
            "B",
            winrate_before=0.70,
            winrate_after=0.60,
            score_before=8.0,
            score_after=3.0,
        ),

        # Winrate barely recovers, but score lead recovers strongly.
        make_move(
            3,
            "B",
            winrate_before=0.60,
            winrate_after=0.64,
            score_before=3.0,
            score_after=7.0,
        ),

        make_move(
            5,
            "B",
            winrate_before=0.64,
            winrate_after=0.56,
            score_before=7.0,
            score_after=2.5,
        ),
    ]

    events = detect_error_events(moves)

    assert len(events) == 2
    assert events[0].move_numbers == [1]
    assert events[1].move_numbers == [5]


def test_small_score_recovery_does_not_split_event():
    moves = [
        make_move(
            1,
            "B",
            winrate_before=0.70,
            winrate_after=0.60,
            score_before=8.0,
            score_after=4.0,
        ),

        make_move(
            3,
            "B",
            winrate_before=0.60,
            winrate_after=0.63,
            score_before=4.0,
            score_after=5.0,
        ),

        make_move(
            5,
            "B",
            winrate_before=0.63,
            winrate_after=0.55,
            score_before=5.0,
            score_after=2.0,
        ),
    ]

    events = detect_error_events(moves)

    assert len(events) == 1
    assert events[0].move_numbers == [1, 5]


def test_custom_score_recovery_threshold_changes_merging():
    moves = [
        make_move(
            1,
            "B",
            winrate_before=0.70,
            winrate_after=0.60,
            score_before=8.0,
            score_after=3.0,
        ),
        make_move(
            3,
            "B",
            winrate_before=0.60,
            winrate_after=0.63,
            score_before=3.0,
            score_after=5.5,
        ),
        make_move(
            5,
            "B",
            winrate_before=0.63,
            winrate_after=0.55,
            score_before=5.5,
            score_after=2.0,
        ),
    ]

    strict_break = ErrorDetectionConfig(
        recovery_score_break=2.0,
    )

    tolerant_break = ErrorDetectionConfig(
        recovery_score_break=4.0,
    )

    assert len(detect_error_events(moves, strict_break)) == 2
    assert len(detect_error_events(moves, tolerant_break)) == 1