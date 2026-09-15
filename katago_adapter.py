"""Local KataGo adapters; all evaluations use Black's perspective."""
from dataclasses import dataclass, field
from typing import Literal, Protocol
from pathlib import Path
import itertools
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time
from dotenv import dotenv_values
from board_state import Position, point_to_gtp


class KataGoProcessError(RuntimeError):
    """A persistent KataGo process could not complete the requested query."""

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

def build_query(position: Position, *, max_visits: int | None = None):
    size = position.board_data['board_size']
    if type(size) is not int or not 2 <= size <= 19:
        raise ValueError('Unsupported board size')
    colors = {'black':'B', 'white':'W'}
    stones = position.board_data['stones'] if position.moves is None else position.initial_stones
    query = {'id':'position', 'boardXSize':size, 'boardYSize':size,
        'initialStones':[[colors[s['color']], point_to_gtp((s['x'],s['y']),size)] for s in stones],
        'moves':[[colors[m.color], point_to_gtp(m.point,size)] for m in (position.moves or [])],
        'initialPlayer':colors[position.next_player if position.moves is None else position.initial_player],
        'rules':position.rules, 'komi':_number(position.komi), 'includePolicy':True}
    if max_visits is not None:
        if _visits(max_visits) == 0:
            raise ValueError('max_visits must be positive')
        query['maxVisits'] = max_visits
    return query


def _launch_settings(adapter):
    executable = shutil.which(adapter.executable) if adapter.executable else None
    if (not executable or not adapter.config or not adapter.model
            or not Path(adapter.config).is_file() or not Path(adapter.model).is_file()):
        return AnalysisResult('unavailable',
            'Configure valid KATAGO_EXECUTABLE, KATAGO_CONFIG and KATAGO_MODEL paths')
    executable = str(Path(executable).resolve())
    timeout = float(adapter.timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Timeout must be positive and finite')
    environment = os.environ.copy()
    if adapter.dll_directory:
        if not Path(adapter.dll_directory).is_dir():
            return AnalysisResult('unavailable', 'KATAGO_DLL_DIRECTORY is not a valid runtime directory')
        environment['PATH'] = adapter.dll_directory + os.pathsep + environment.get('PATH', '')
    command = [executable, 'analysis', '-config', str(Path(adapter.config).resolve()),
        '-model', str(Path(adapter.model).resolve()),
        '-override-config', 'reportAnalysisWinratesAs=BLACK']
    return command, timeout, environment, str(Path(executable).parent)

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
            settings = _launch_settings(self)
            if isinstance(settings, AnalysisResult):
                return settings
            command, timeout, environment, cwd = settings
            query = build_query(position, max_visits=max_visits)
            completed = subprocess.run(command,
                input=json.dumps(query)+'\n', capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=timeout, env=environment,
                cwd=cwd,
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


_EOF = object()


@dataclass
class PersistentKataGoAdapter(LocalKataGoAdapter):
    """One long-lived Analysis Engine process with serialized request handling."""

    _process: subprocess.Popen | None = field(default=None, init=False, repr=False)
    _stdout_queue: queue.Queue | None = field(default=None, init=False, repr=False)
    _reader_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _query_ids: itertools.count = field(default_factory=lambda: itertools.count(1), init=False, repr=False)

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None and process.poll() is None else None

    @staticmethod
    def _read_stdout(stream, output_queue):
        try:
            for line in stream:
                output_queue.put(line)
        finally:
            output_queue.put(_EOF)

    def _start_locked(self, command, environment, cwd):
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace',
                bufsize=1, env=environment, cwd=cwd,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except OSError as exc:
            raise KataGoProcessError(f'Unable to start KataGo: {exc}') from exc
        if process.stdin is None or process.stdout is None:
            process.kill()
            raise KataGoProcessError('KataGo process did not expose stdin/stdout')
        output_queue = queue.Queue()
        reader = threading.Thread(target=self._read_stdout,
            args=(process.stdout, output_queue), name='katago-stdout', daemon=True)
        self._process = process
        self._stdout_queue = output_queue
        self._reader_thread = reader
        try:
            reader.start()
        except RuntimeError as exc:
            self._stop_locked()
            raise KataGoProcessError(f'Unable to start KataGo stdout reader: {exc}') from exc

    def _stop_locked(self):
        process = self._process
        reader = self._reader_thread
        self._process = None
        self._stdout_queue = None
        self._reader_thread = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            except OSError:
                pass
        try:
            if process.stdout is not None:
                process.stdout.close()
        except OSError:
            pass
        if reader is not None and reader.ident is not None and reader is not threading.current_thread():
            reader.join(timeout=1)

    def close(self):
        with self._lock:
            self._stop_locked()

    shutdown = close

    def analyze(self, position: Position, *, max_visits: int | None = None) -> AnalysisResult:
        with self._lock:
            try:
                settings = _launch_settings(self)
                if isinstance(settings, AnalysisResult):
                    return settings
                command, timeout, environment, cwd = settings
                query = build_query(position, max_visits=max_visits)
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                return AnalysisResult('error', str(exc))

            process = self._process
            if process is not None and process.poll() is not None:
                return_code = process.returncode
                self._stop_locked()
                raise KataGoProcessError(f'KataGo process exited before query (code {return_code})')
            if process is None:
                self._start_locked(command, environment, cwd)
                process = self._process

            query['id'] = f'position-{next(self._query_ids)}'
            try:
                process.stdin.write(json.dumps(query) + '\n')
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                self._stop_locked()
                raise KataGoProcessError(f'Unable to send KataGo query: {exc}') from exc

            warnings = []
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._stop_locked()
                    raise KataGoProcessError(f'KataGo query {query["id"]} timed out')
                try:
                    line = self._stdout_queue.get(timeout=remaining)
                except queue.Empty as exc:
                    self._stop_locked()
                    raise KataGoProcessError(f'KataGo query {query["id"]} timed out') from exc
                if line is _EOF:
                    return_code = process.poll()
                    self._stop_locked()
                    raise KataGoProcessError(f'KataGo process closed stdout (code {return_code})')
                try:
                    data = json.loads(line)
                except (json.JSONDecodeError, TypeError) as exc:
                    self._stop_locked()
                    raise KataGoProcessError(f'Malformed KataGo response: {exc}') from exc
                if data.get('id') != query['id']:
                    continue
                if 'error' in data:
                    result = normalize_output(data)
                    result.warnings = warnings
                    return result
                if 'warning' in data:
                    warnings.append(str(data['warning']))
                    continue
                if (data.get('turnNumber') != len(query['moves'])
                        or data.get('isDuringSearch', False)):
                    continue
                result = normalize_output(data)
                result.warnings = warnings
                if result.status == 'ok' and result.current_player != position.next_player:
                    return AnalysisResult('error',
                        'KataGo current player does not match requested position', warnings=warnings)
                return result
