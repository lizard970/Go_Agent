from unittest.mock import Mock

import pytest

from game_scan import (DEFAULT_SCAN_VISITS, GameScanResult, metrics_for_color,
                       scan_game, scan_visits_from_env)
from katago_adapter import AnalysisResult
from sgf_ingestion import parse_sgf


TWENTY_MOVE_SGF = b"""(;SZ[9]KM[6.5]
;B[aa];W[cc];B[ee];W[gg];B[ii];W[ac];B[ce];W[eg];B[gi];W[]
;B[ia];W[bc];B[de];W[fg];B[hi];W[ca];B[ec];W[ge];B[ig];W[ai])"""


def analysis_for(position, *, max_visits=None):
    number = position.move_number
    return AnalysisResult('ok', winrate=.5 + number * .01, score_lead=float(number),
        best_move=f'M{number}', current_player=position.next_player,
        visits=100 + number, move_number=number, warnings=[f'warning-{number}'])


def test_twenty_move_scan_uses_exactly_n_plus_one_aligned_calls():
    game = parse_sgf(TWENTY_MOVE_SGF)
    adapter = Mock()
    adapter.analyze.side_effect = analysis_for

    result = scan_game(game, adapter)

    assert isinstance(result, GameScanResult)
    assert len(game.moves) == 20
    assert adapter.analyze.call_count == 21
    positions = [call.args[0].move_number for call in adapter.analyze.call_args_list]
    assert positions == list(range(21))
    assert {call.kwargs['max_visits'] for call in adapter.analyze.call_args_list} == {100}
    assert len(result.position_analyses) == 21 and len(result.moves) == 20
    assert [move.move_number for move in result.moves] == list(range(1, 21))
    assert result.moves[0].best_move_before == 'M0'
    assert result.moves[19].best_move_before == 'M19'
    assert result.moves[0].black_winrate_before == .5
    assert result.moves[0].black_winrate_after == .51
    assert result.moves[0].visits == 100
    assert result.moves[0].warnings == ['warning-0', 'warning-1']


def test_actual_moves_pass_and_player_perspective_losses():
    result = scan_game(parse_sgf(TWENTY_MOVE_SGF), Mock(analyze=Mock(side_effect=analysis_for)))
    black_move = result.moves[0]
    white_move = result.moves[1]
    pass_move = result.moves[9]

    assert (black_move.player, black_move.actual_move) == ('B', 'A9')
    assert black_move.player_winrate_loss == pytest.approx(-.01)
    assert black_move.player_score_loss == pytest.approx(-1.0)
    assert (white_move.player, white_move.actual_move) == ('W', 'C7')
    assert white_move.player_winrate_loss == pytest.approx(.01)
    assert white_move.player_score_loss == pytest.approx(1.0)
    assert (pass_move.player, pass_move.actual_move) == ('W', 'pass')


def test_black_and_white_metric_conversion():
    assert metrics_for_color(.72, 4.5, 'B') == (.72, 4.5)
    white = metrics_for_color(.72, 4.5, 'W')
    assert white[0] == pytest.approx(.28) and white[1] == -4.5
    with pytest.raises(ValueError):
        metrics_for_color(.5, 0, 'black')


def test_scan_visits_configuration(tmp_path, monkeypatch):
    env_file = tmp_path / '.env'
    env_file.write_text('', encoding='utf-8')
    monkeypatch.delenv('KATAGO_SCAN_VISITS', raising=False)
    assert scan_visits_from_env(env_file) == DEFAULT_SCAN_VISITS == 100
    monkeypatch.setenv('KATAGO_SCAN_VISITS', '35')
    assert scan_visits_from_env(env_file) == 35
    monkeypatch.setenv('KATAGO_SCAN_VISITS', '0')
    with pytest.raises(ValueError):
        scan_visits_from_env(env_file)
