"""One-shot local KataGo analysis protocol; all evaluations use Black's perspective."""
from dataclasses import dataclass, field
from typing import Literal, Protocol
from pathlib import Path
import json
import math
import os
import shutil
import subprocess
from dotenv import dotenv_values
from board_state import Position

@dataclass(frozen=True)
class Candidate:
    move: str
    winrate: float
    score_lead: float
    probability: float | None = None
    visits: int = 0
    pv: list[str] = field(default_factory=list)

@dataclass
class AnalysisResult:
    status: Literal['ok', 'error', 'unavailable']
    error: str | None = None
    winrate: float | None = None
    score_lead: float | None = None
    best_move: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    policy: list[float] | None = None
    perspective: str = 'black'
    warnings: list[str] = field(default_factory=list)
    current_player: str | None = None
    visits: int | None = None
    pv: list[str] = field(default_factory=list)
    move_number: int | None = None

class KataGoAdapter(Protocol):
    def analyze(self, position: Position, *, max_visits: int | None = None) -> AnalysisResult: ...


def _visits(value):
    if type(value) is not int or value < 0:
        raise ValueError('Visits must be a nonnegative integer')
    return value


def _pv(value):
    # A terminal pass can legitimately have an empty continuation.
    if not isinstance(value, list) or any(not isinstance(move, str) or not move for move in value):
        raise ValueError('Missing or malformed principal variation')
    return list(value)

def _number(value, probability=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid numeric analysis field')
    if probability and not 0 <= value <= 1:
        raise ValueError('Probability outside [0, 1]')
    return float(value)

def normalize_output(data: dict) -> AnalysisResult:
    """Normalize a completed response requested with Black-perspective reporting."""
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
                _number(item['prior'], True) if 'prior' in item else None,
                _visits(item['visits']), _pv(item['pv'])))
        policy = data.get('policy')
        if policy is not None:
            policy = [_number(value) for value in policy]
            if any(value != -1 and not 0 <= value <= 1 for value in policy):
                raise ValueError('Invalid policy probability')
        if not candidates:
            raise ValueError('No candidate moves in completed analysis')
        return AnalysisResult('ok', winrate=_number(root['winrate'], True),
            score_lead=_number(root.get('scoreLead', root.get('scoreMean'))),
            best_move=candidates[0].move, candidates=candidates, policy=policy,
            current_player={'B': 'black', 'W': 'white'}[root['currentPlayer']],
            visits=_visits(root['visits']), pv=list(candidates[0].pv),
            move_number=_visits(data['turnNumber']))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return AnalysisResult('error', f'Malformed KataGo output: {exc}')

def _vertex(point, size):
    if point is None:
        return 'pass'
    x, y = point
    if type(x) is not int or type(y) is not int or not (0 <= x < size and 0 <= y < size):
        raise ValueError('Stone coordinate outside board')
    return 'ABCDEFGHJKLMNOPQRST'[x] + str(size-y)

def build_query(position: Position, *, max_visits: int | None = None):
    size = position.board_data['board_size']
    if type(size) is not int or not 2 <= size <= 19:
        raise ValueError('Unsupported board size')
    colors = {'black':'B', 'white':'W'}
    stones = position.board_data['stones'] if position.moves is None else position.initial_stones
    query = {'id':'position', 'boardXSize':size, 'boardYSize':size,
        'initialStones':[[colors[s['color']], _vertex((s['x'],s['y']),size)] for s in stones],
        'moves':[[colors[m.color], _vertex(m.point,size)] for m in (position.moves or [])],
        'initialPlayer':colors[position.next_player if position.moves is None else position.initial_player],
        'rules':position.rules, 'komi':_number(position.komi), 'includePolicy':True}
    if max_visits is not None:
        if _visits(max_visits) == 0:
            raise ValueError('max_visits must be positive')
        query['maxVisits'] = max_visits
    return query

@dataclass
class LocalKataGoAdapter:
    executable: str = ''
    config: str = ''
    model: str = ''
    timeout: float | str = 120
    dll_directory: str = ''

    @classmethod
    def from_env(cls, env_file=None):
        """Read project-local settings without mutating the process environment.

        Explicit environment values take precedence over .env, including empty
        values. No OpenAI settings are used by this adapter.
        """
        values = dotenv_values(env_file or Path(__file__).with_name('.env'))
        def setting(name, default=''):
            return os.environ.get(name, values.get(name) or default)
        return cls(setting('KATAGO_EXECUTABLE'), setting('KATAGO_CONFIG'),
                   setting('KATAGO_MODEL'), setting('KATAGO_TIMEOUT', '120'),
                   setting('KATAGO_DLL_DIRECTORY'))

    def analyze(self, position: Position, *, max_visits: int | None = None) -> AnalysisResult:
        try:
            executable = shutil.which(self.executable) if self.executable else None
            if not executable or not self.config or not self.model or not Path(self.config).is_file() or not Path(self.model).is_file():
                return AnalysisResult('unavailable', 'Configure valid KATAGO_EXECUTABLE, KATAGO_CONFIG and KATAGO_MODEL paths')
            executable = str(Path(executable).resolve())
            timeout = float(self.timeout)
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError('Timeout must be positive and finite')
            environment = os.environ.copy()
            if self.dll_directory:
                if not Path(self.dll_directory).is_dir():
                    return AnalysisResult('unavailable', 'KATAGO_DLL_DIRECTORY is not a valid runtime directory')
                environment['PATH'] = self.dll_directory + os.pathsep + environment.get('PATH', '')
            query = build_query(position, max_visits=max_visits)
            completed = subprocess.run([executable, 'analysis', '-config', str(Path(self.config).resolve()),
                '-model', str(Path(self.model).resolve()),
                '-override-config', 'reportAnalysisWinratesAs=BLACK'],
                input=json.dumps(query)+'\n', capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=timeout, env=environment,
                cwd=str(Path(executable).resolve().parent),
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
            if result.status == 'ok' and result.current_player != position.next_player:
                return AnalysisResult('error', 'KataGo current player does not match requested position', warnings=warnings)
            return result
        except subprocess.TimeoutExpired:
            return AnalysisResult('error', 'KataGo analysis timed out')
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            return AnalysisResult('error', str(exc))
