from unittest.mock import Mock

import pytest

from deep_analysis import (DEFAULT_DEEP_VISITS, DeepAnalysisError,
                           deep_analyze_ranked_events)
from error_detection import ErrorEvent
from error_ranking import RankedErrorEvent
from game_scan import MoveAnalysis
from katago_adapter import AnalysisResult
from sgf_ingestion import parse_sgf


GAME = parse_sgf(b"(;SZ[9];B[aa];W[bb];B[cc])")


def make_move(number, player, actual_move, *, phase="opening"):
    return MoveAnalysis(
        move_number=number,
        player=player,
        actual_move=actual_move,
        best_move_before="D4",
        black_winrate_before=.5,
        black_score_lead_before=0.0,
        black_winrate_after=.5,
        black_score_lead_after=0.0,
        player_winrate_loss=0.0,
        player_score_loss=0.0,
        visits=100,
        stones_on_board=number,
        occupancy_ratio=number / 81,
        phase=phase,
        warnings=[],
    )


def make_ranked(rank, move, *, player=None, phase=None, event_worst=None):
    event = ErrorEvent(
        start_move=move.move_number,
        end_move=move.move_number,
        player=player or move.player,
        move_numbers=[move.move_number],
        sum_winrate_loss=.1,
        sum_score_loss=3.0,
        net_winrate_loss=.1,
        net_score_loss=3.0,
        peak_winrate_loss=.1,
        peak_score_loss=3.0,
        worst_move_number=event_worst or move.move_number,
        severity_score=2.0,
    )
    return RankedErrorEvent(
        rank=rank,
        event=event,
        phase=phase or move.phase,
        start_move=event.start_move,
        end_move=event.end_move,
        worst_move_number=move.move_number,
        severity_score=event.severity_score,
    )


def deep_result(position, *, max_visits=None):
    values = {
        0: (.70, 5.0),
        1: (.60, 2.0),
        2: (.75, 6.0),
    }
    winrate, score = values[position.move_number]
    return AnalysisResult(
        "ok",
        winrate=winrate,
        score_lead=score,
        best_move=f"best-{position.move_number}",
        current_player=position.next_player,
        visits=500 + position.move_number,
        move_number=position.move_number,
        warnings=[f"warning-{position.move_number}"],
    )


def test_deep_analysis_alignment_perspectives_visits_and_deduplication():
    black = make_move(1, "B", "A9")
    white = make_move(2, "W", "B8")
    ranked = [make_ranked(1, black), make_ranked(2, white)]
    adapter = Mock()
    adapter.analyze.side_effect = deep_result

    evidence = deep_analyze_ranked_events(GAME, ranked, [black, white], adapter)

    assert [call.args[0].move_number for call in adapter.analyze.call_args_list] == [0, 1, 2]
    assert adapter.analyze.call_count == 3
    assert {call.kwargs["max_visits"] for call in adapter.analyze.call_args_list} == {
        DEFAULT_DEEP_VISITS
    }
    first, second = evidence
    assert first.rank == 1 and first.actual_move == "A9"
    assert first.deep_best_move_before == "best-0"
    assert first.deep_black_winrate_before == .70
    assert first.deep_black_winrate_after == .60
    assert first.player_winrate_loss == pytest.approx(.10)
    assert first.player_score_loss == pytest.approx(3.0)
    assert first.visits == 500
    assert first.warnings == ["warning-0", "warning-1"]
    assert second.player == "W" and second.actual_move == "B8"
    assert second.deep_best_move_before == "best-1"
    assert second.deep_black_winrate_before == .60
    assert second.deep_black_winrate_after == .75
    assert second.player_winrate_loss == pytest.approx(.15)
    assert second.player_score_loss == pytest.approx(4.0)
    assert second.visits == 501


@pytest.mark.parametrize("visits", [0, -1, False, 1.5])
def test_invalid_deep_visits_are_rejected(visits):
    with pytest.raises(ValueError):
        deep_analyze_ranked_events(GAME, [], [], Mock(), max_visits=visits)


def test_empty_ranked_events_make_no_queries():
    adapter = Mock()

    assert deep_analyze_ranked_events(GAME, [], [], adapter) == []
    adapter.analyze.assert_not_called()


def test_missing_move_reference_is_rejected_before_analysis():
    black = make_move(1, "B", "A9")
    adapter = Mock()

    with pytest.raises(ValueError, match="Missing MoveAnalysis"):
        deep_analyze_ranked_events(GAME, [make_ranked(1, black)], [], adapter)
    adapter.analyze.assert_not_called()


@pytest.mark.parametrize(
    "move,ranked,error",
    [
        (make_move(1, "W", "A9"), None, "Player mismatch"),
        (make_move(1, "B", "J9"), None, "Actual move mismatch"),
        (make_move(1, "B", "A9"), "middlegame", "Phase mismatch"),
    ],
)
def test_inconsistent_move_reference_is_rejected(move, ranked, error):
    selected = make_ranked(1, move, phase=ranked) if ranked else make_ranked(1, move)

    with pytest.raises(ValueError, match=error):
        deep_analyze_ranked_events(GAME, [selected], [move], Mock())


def test_deep_analysis_error_is_not_replaced_with_low_visits_evidence():
    black = make_move(1, "B", "A9")
    adapter = Mock()
    adapter.analyze.return_value = AnalysisResult("error", "engine failed")

    with pytest.raises(DeepAnalysisError, match="engine failed"):
        deep_analyze_ranked_events(GAME, [make_ranked(1, black)], [black], adapter)
