import pytest

from error_detection import ErrorEvent
from error_ranking import rank_error_events
from game_scan import MoveAnalysis


def make_move(
    move_number: int,
    *,
    player: str = "B",
    phase: str = "middlegame",
) -> MoveAnalysis:
    return MoveAnalysis(
        move_number=move_number,
        player=player,
        actual_move="D4",
        best_move_before="Q16",

        black_winrate_before=0.60,
        black_score_lead_before=5.0,
        black_winrate_after=0.50,
        black_score_lead_after=2.0,

        player_winrate_loss=0.10,
        player_score_loss=3.0,

        visits=100,

        stones_on_board=50,
        occupancy_ratio=0.20,
        phase=phase,

        warnings=[],
    )


def make_event(
    move_number: int,
    severity: float,
    *,
    player: str = "B",
    peak_winrate_loss: float = 0.10,
    peak_score_loss: float = 3.0,
) -> ErrorEvent:
    return ErrorEvent(
        start_move=move_number,
        end_move=move_number,
        player=player,

        move_numbers=[
            move_number
        ],

        sum_winrate_loss=peak_winrate_loss,
        sum_score_loss=peak_score_loss,

        net_winrate_loss=peak_winrate_loss,
        net_score_loss=peak_score_loss,

        peak_winrate_loss=peak_winrate_loss,
        peak_score_loss=peak_score_loss,

        worst_move_number=move_number,

        severity_score=severity,
    )


def test_highest_severity_ranks_first():
    moves = [
        make_move(10),
        make_move(20),
        make_move(30),
    ]

    events = [
        make_event(10, 1.0),
        make_event(20, 3.0),
        make_event(30, 2.0),
    ]

    ranked = rank_error_events(
        events,
        moves,
        user_color="B",
    )

    assert [
        item.worst_move_number
        for item in ranked
    ] == [20, 30, 10]

    assert [
        item.rank
        for item in ranked
    ] == [1, 2, 3]


def test_limit_returns_only_top_events():
    moves = [
        make_move(10),
        make_move(20),
        make_move(30),
    ]

    events = [
        make_event(10, 1.0),
        make_event(20, 3.0),
        make_event(30, 2.0),
    ]

    ranked = rank_error_events(
        events,
        moves,
        user_color="B",
        limit=2,
    )

    assert len(ranked) == 2

    assert [
        item.worst_move_number
        for item in ranked
    ] == [20, 30]


def test_phase_comes_from_worst_move():
    moves = [
        make_move(
            150,
            phase="endgame",
        )
    ]

    events = [
        make_event(
            150,
            2.0,
        )
    ]

    ranked = rank_error_events(
        events,
        moves,
        user_color="B",
    )

    assert ranked[0].phase == "endgame"


def test_empty_events_return_empty_list():
    moves = [
        make_move(10)
    ]

    assert rank_error_events(
        [],
        moves,
        user_color="B",
    ) == []


def test_invalid_limit_is_rejected():
    with pytest.raises(ValueError):
        rank_error_events(
            [],
            [],
            user_color="B",
            limit=0,
        )


def test_missing_worst_move_is_rejected():
    events = [
        make_event(
            99,
            2.0,
        )
    ]

    with pytest.raises(ValueError):
        rank_error_events(
            events,
            [],
            user_color="B",
        )


def test_ties_are_deterministic():
    moves = [
        make_move(10),
        make_move(20),
    ]

    events = [
        make_event(
            20,
            2.0,
            peak_winrate_loss=0.10,
        ),
        make_event(
            10,
            2.0,
            peak_winrate_loss=0.15,
        ),
    ]

    ranked = rank_error_events(
        events,
        moves,
        user_color="B",
    )

    assert ranked[0].worst_move_number == 10


def test_only_user_events_are_ranked():
    moves = [
        make_move(10, player="B"),
        make_move(20, player="W"),
    ]
    events = [
        make_event(10, 1.0, player="B"),
        make_event(20, 100.0, player="W"),
    ]

    ranked = rank_error_events(events, moves, user_color="B", limit=1)

    assert [item.worst_move_number for item in ranked] == [10]


def test_top_k_limit_is_applied_after_player_filtering():
    moves = [
        make_move(10, player="B"),
        make_move(20, player="W"),
        make_move(30, player="B"),
    ]
    events = [
        make_event(10, 2.0, player="B"),
        make_event(20, 100.0, player="W"),
        make_event(30, 1.0, player="B"),
    ]

    ranked = rank_error_events(events, moves, user_color="B", limit=2)

    assert [item.worst_move_number for item in ranked] == [10, 30]


def test_invalid_user_color_is_rejected():
    with pytest.raises(ValueError):
        rank_error_events([], [], user_color="black")
