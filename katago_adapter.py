"""One-shot local KataGo analysis protocol; all evaluations use Black's perspective."""
from dataclasses import dataclass, field
from typing import Protocol
from pathlib import Path
import json
import math
import os
import shutil
import subprocess
from sgf_ingestion import Position

@dataclass(frozen=True)
class Candidate:
    move: str
    winrate: float
    score_lead: float
    probability: float | None = None

@dataclass
class AnalysisResult:
    status: str
    error: str | None = None
    winrate: float | None = None
    score_lead: float | None = None
    best_move: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    policy: list[float] | None = None
    perspective: str = 'black'
    warnings: list[str] = field(default_factory=list)

class KataGoAdapter(Protocol):
    def analyze(self, position: Position) -> AnalysisResult: ...

def _number(value, probability=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid numeric analysis field')
    if probability and not 0 <= value <= 1:
        raise ValueError('Probability outside [0, 1]')
    return float(value)

def normalize_output(data: dict) -> AnalysisResult:
    try:
        if 'error' in data:
            return AnalysisResult('error', str(data['error']))
        root = data['rootInfo']
        candidates = []
        for item in sorted(data['moveInfos'], key=lambda item: item['order']):
            move = item['move']
            if not isinstance(move, str) or not move:
                raise ValueError('Missing candidate move')
            candidates.append(Candidate(move, _number(item['winrate'], True),
                _number(item.get('scoreLead', item.get('scoreMean'))),
                _number(item['prior'], True) if 'prior' in item else None))
        policy = data.get('policy')
        if policy is not None:
            policy = [_number(value) for value in policy]
            if any(value != -1 and not 0 <= value <= 1 for value in policy):
                raise ValueError('Invalid policy probability')
        if not candidates:
            raise ValueError('No candidate moves in completed analysis')
        return AnalysisResult('ok', winrate=_number(root['winrate'], True),
            score_lead=_number(root.get('scoreLead', root.get('scoreMean'))),
            best_move=candidates[0].move, candidates=candidates, policy=policy)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return AnalysisResult('error', f'Malformed KataGo output: {exc}')

def _vertex(point, size):
    if point is None:
        return 'pass'
    x, y = point
    if type(x) is not int or type(y) is not int or not (0 <= x < size and 0 <= y < size):
        raise ValueError('Stone coordinate outside board')
    return 'ABCDEFGHJKLMNOPQRST'[x] + str(size-y)

def build_query(position):
    size = position.board_data['board_size']
    if type(size) is not int or not 2 <= size <= 19:
        raise ValueError('Unsupported board size')
    colors = {'black':'B', 'white':'W'}
    stones = position.board_data['stones'] if position.moves is None else position.initial_stones
    query = {'id':'position', 'boardXSize':size, 'boardYSize':size,
        'initialStones':[[colors[s['color']], _vertex((s['x'],s['y']),size)] for s in stones],
        'moves':[[colors[m.color], _vertex(m.point,size)] for m in (position.moves or [])],
        'initialPlayer':colors[position.next_player if position.moves is None else position.initial_player],
        'rules':position.rules, 'komi':_number(position.komi), 'includePolicy':True,
        'overrideSettings':{'reportAnalysisWinratesAs':'BLACK'}}
    return query

@dataclass
class LocalKataGoAdapter:
    executable: str = ''
    config: str = ''
    model: str = ''
    timeout: float = 120

    @classmethod
    def from_env(cls):
        return cls(os.getenv('KATAGO_EXECUTABLE',''), os.getenv('KATAGO_CONFIG',''),
                   os.getenv('KATAGO_MODEL',''), float(os.getenv('KATAGO_TIMEOUT','120')))

    def analyze(self, position):
        executable = shutil.which(self.executable) if self.executable else None
        if not executable or not self.config or not self.model or not Path(self.config).is_file() or not Path(self.model).is_file():
            return AnalysisResult('unavailable', 'Configure valid KATAGO_EXECUTABLE, KATAGO_CONFIG and KATAGO_MODEL paths')
        try:
            if not math.isfinite(self.timeout) or self.timeout <= 0:
                raise ValueError('Timeout must be positive and finite')
            query = build_query(position)
            completed = subprocess.run([executable, 'analysis', '-config', self.config, '-model', self.model],
                input=json.dumps(query)+'\n', capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=self.timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if completed.returncode:
                return AnalysisResult('error', f'KataGo exited {completed.returncode}: {completed.stderr[-2000:]}')
            warnings = []
            result = None
            for line in completed.stdout.splitlines():
                data = json.loads(line)
                if data.get('id') not in (None, query['id']):
                    continue
                if 'error' in data:
                    return normalize_output(data)
                if 'warning' in data:
                    warnings.append(str(data['warning']))
                elif data.get('id') == query['id'] and data.get('turnNumber') == len(query['moves']) and not data.get('isDuringSearch', False):
                    result = normalize_output(data)
            if result is None:
                return AnalysisResult('error', 'No completed response for requested position', warnings=warnings)
            result.warnings = warnings
            return result
        except subprocess.TimeoutExpired:
            return AnalysisResult('error', 'KataGo analysis timed out')
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            return AnalysisResult('error', str(exc))
