"""Opt-in real engine contract checks; normal pytest never starts KataGo."""
import json
import os
from dataclasses import asdict
from pathlib import Path
import pytest
from katago_adapter import LocalKataGoAdapter
from sgf_ingestion import parse_sgf

SGF = b'(;SZ[9]KM[6.5];B[ba];W[aa];B[ab];W[])'
pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv('RUN_KATAGO_INTEGRATION') != '1', reason='Set RUN_KATAGO_INTEGRATION=1 for real KataGo')]

@pytest.mark.parametrize('move_number',[2,4])
def test_real_engine(move_number, tmp_path):
    result = LocalKataGoAdapter.from_env().analyze(parse_sgf(SGF).position(move_number))
    assert result.status == 'ok', result.error
    assert result.current_player == 'black' and result.move_number == move_number
    assert 0 <= result.winrate <= 1 and isinstance(result.score_lead,float)
    assert result.visits > 0 and result.best_move
    assert result.candidates and result.pv
    assert result.pv == result.candidates[0].pv
    assert all(candidate.visits >= 0 for candidate in result.candidates)
    # Artifacts contain normalized engine evidence, never environment variables.
    artifact_dir = Path(os.getenv('KATAGO_TEST_ARTIFACT_DIR',str(tmp_path)))
    artifact_dir.mkdir(parents=True,exist_ok=True)
    (artifact_dir/f'move-{move_number}.json').write_text(json.dumps(asdict(result),indent=2),encoding='utf-8')
    print(json.dumps({'move':move_number,'winrate':result.winrate,'score_lead':result.score_lead,
                      'best_move':result.best_move,'visits':result.visits,'pv':result.pv}))
