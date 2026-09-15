import json
import queue
import subprocess
from unittest.mock import patch
import pytest
from sgf_ingestion import parse_sgf, Position
from katago_adapter import (KataGoProcessError, LocalKataGoAdapter,
                            PersistentKataGoAdapter, normalize_output, build_query)
from board_state import stones_to_grid, grid_to_stones


def test_parse_positions_and_metadata():
    game = parse_sgf(b'(;GM[1]SZ[9]PB[A]PW[B]KM[0.5];B[aa];W[bc])')
    assert game.metadata['PB'] == 'A'
    assert game.moves[1].number == 2 and game.moves[1].color == 'white'
    assert game.position(0).board_data['stones'] == []
    assert game.position(1).board_data['stones'] == [{'x':0,'y':0,'color':'black'}]
    assert game.position().board_data['stones'][1] == {'x':1,'y':2,'color':'white'}
    assert game.position().komi == .5
    with pytest.raises(ValueError): game.position(3)


def test_capture_and_shared_conversion():
    game = parse_sgf(b'(;SZ[9];B[ba];W[aa];B[ab])')
    data = game.position().board_data
    assert len(data['stones']) == 2
    assert grid_to_stones(stones_to_grid(9,data['stones']),9) == data['stones']


def test_pass_and_history():
    game = parse_sgf(b'(;SZ[19];B[aa];W[];B[tt])')
    assert [m.point for m in game.moves] == [(0,0),None,None]
    position = game.position()
    assert position.next_player == 'white' and position.move_number == 3
    assert build_query(position)['moves'] == [['B','A19'],['W','pass'],['B','pass']]


def test_setup_variation_and_escaped_metadata():
    game = parse_sgf(b'(;SZ[9]HA[2]AB[aa][ii]PB[A\\]B](;W[bb])(;W[cc]))')
    assert game.initial_player == 'white' and game.metadata['PB'] == 'A]B'
    assert game.moves[0].point == (1,1)
    assert len(game.position(0).board_data['stones']) == 2


@pytest.mark.parametrize('data', [b'',b'nonsense',b'(;SZ[9]',b'(;SZ[9];B[zz])',
    b'(;SZ[9];B[aa];W[aa])',b'(;GM[2])',b'(;SZ[9];AB[aa])',
    b'(;SZ[9];B[aa]W[bb])',b'(;SZ[9])(;SZ[13])',b'(;SZ[9]AB[aa]AW[aa])'])
def test_invalid(data):
    with pytest.raises(ValueError): parse_sgf(data)


def output():
    return {'id':'position','turnNumber':0,'rootInfo':{'winrate':.6,'scoreLead':2.3,'currentPlayer':'B','visits':500},
        'moveInfos':[{'move':'pass','order':1,'winrate':.4,'scoreLead':-1,'prior':.1,'visits':10,'pv':['pass','D4']},
                     {'move':'D4','order':0,'winrate':.6,'scoreMean':2.3,'prior':.3,'visits':490,'pv':['D4','E5']}],
        'policy':[.1,-1,.9]}


def test_normalize():
    result = normalize_output(output())
    assert result.status == 'ok' and result.best_move == 'D4'
    assert result.winrate == .6 and result.score_lead == 2.3
    assert result.candidates[0].probability == .3 and result.policy == [.1,-1,.9]
    assert result.perspective == 'black'


@pytest.mark.parametrize('data',[{}, {'error':'bad rules'}, {'rootInfo':None},
    {**output(),'rootInfo':{'winrate':2,'scoreLead':0}},
    {**output(),'rootInfo':{'winrate':float('nan'),'scoreLead':0}}])
def test_malformed(data):
    assert normalize_output(data).status == 'error'


def test_unavailable():
    assert LocalKataGoAdapter().analyze(parse_sgf(b'(;SZ[9])').position()).status == 'unavailable'


@pytest.fixture
def configured(tmp_path):
    config = tmp_path/'analysis.cfg'; config.touch()
    model = tmp_path/'model.bin'; model.touch()
    return LocalKataGoAdapter('katago',str(config),str(model)), parse_sgf(b'(;SZ[9])').position()


def test_process_contract(configured):
    adapter, position = configured
    warning = {'id':'position','warning':'rule adjusted'}
    with patch('katago_adapter.shutil.which',return_value='katago'), patch('katago_adapter.subprocess.run') as run:
        run.return_value = subprocess.CompletedProcess([],0,json.dumps(warning)+'\n'+json.dumps(output()),'')
        result = adapter.analyze(position)
        assert result.status == 'ok' and result.warnings == ['rule adjusted']
        query = json.loads(run.call_args.kwargs['input'])
        assert query['includePolicy'] is True
        assert 'reportAnalysisWinratesAs=BLACK' in run.call_args.args[0]
        assert run.call_args.args[0][1] == 'analysis'


@pytest.mark.parametrize('response',[
    subprocess.CompletedProcess([],1,'','startup failed'),
    subprocess.CompletedProcess([],0,'garbage',''),
    subprocess.CompletedProcess([],0,json.dumps({'id':'position','error':'invalid rules'}),''),
    subprocess.CompletedProcess([],0,json.dumps({**output(),'isDuringSearch':True}),''),
    subprocess.CompletedProcess([],0,json.dumps({**output(),'turnNumber':1}),''),
    subprocess.TimeoutExpired('katago',1), OSError('launch failed')])
def test_process_errors(configured,response):
    adapter, position = configured
    with patch('katago_adapter.shutil.which',return_value='katago'), patch('katago_adapter.subprocess.run') as run:
        if isinstance(response,Exception): run.side_effect = response
        else: run.return_value = response
        assert adapter.analyze(position).status == 'error'


def test_static_position():
    query = build_query(Position({'board_size':9,'stones':[{'x':8,'y':8,'color':'white'}]},'white'))
    assert query['moves'] == [] and query['initialStones'] == [['W','J1']]
    assert query['initialPlayer'] == 'W'


def test_complete_normalized_fields_and_terminal_pass():
    raw = output()
    raw['moveInfos'][0]['pv'] = []
    result = normalize_output(raw)
    assert result.current_player == 'black'
    assert result.visits == 500 and result.move_number == 0
    assert result.pv == ['D4','E5']
    assert result.candidates[0].visits == 490
    assert result.candidates[1].pv == []
    raw['rootInfo']['currentPlayer'] = 'W'
    assert normalize_output(raw).current_player == 'white'
    assert normalize_output(raw).winrate == .6


@pytest.mark.parametrize('field,value', [('visits',-1),('visits',2.5),('visits',True),('currentPlayer','X')])
def test_invalid_root_fields(field,value):
    raw = output(); raw['rootInfo'][field] = value
    assert normalize_output(raw).status == 'error'


@pytest.mark.parametrize('value',[None,'D4',[1]])
def test_invalid_pv(value):
    raw = output(); raw['moveInfos'][0]['pv'] = value
    assert normalize_output(raw).status == 'error'


def test_env_precedence_and_no_global_mutation(tmp_path,monkeypatch):
    import os
    env_file = tmp_path/'.env'
    env_file.write_text('KATAGO_EXECUTABLE=file.exe\nKATAGO_TIMEOUT=45\nKATAGO_MODEL=model.bin\n')
    monkeypatch.setenv('KATAGO_EXECUTABLE','process.exe')
    monkeypatch.delenv('KATAGO_TIMEOUT',raising=False)
    monkeypatch.delenv('KATAGO_MODEL',raising=False)
    before = dict(os.environ)
    adapter = LocalKataGoAdapter.from_env(env_file)
    assert adapter.executable == 'process.exe' and float(adapter.timeout) == 45
    assert adapter.model == 'model.bin' and dict(os.environ) == before


@pytest.mark.parametrize('timeout',['invalid','nan','inf','0','-1'])
def test_invalid_timeout_is_result(configured,timeout):
    adapter,position = configured
    adapter.timeout = timeout
    with patch('katago_adapter.shutil.which',return_value='katago'), patch('katago_adapter.subprocess.run') as run:
        assert adapter.analyze(position).status == 'error'
        run.assert_not_called()


def test_runtime_path_and_visit_budget(configured,tmp_path):
    import os
    adapter,position = configured
    runtime = tmp_path/'runtime with spaces'; runtime.mkdir()
    adapter.dll_directory = str(runtime)
    before = os.environ.get('PATH','')
    with patch('katago_adapter.shutil.which',return_value='katago'), patch('katago_adapter.subprocess.run') as run:
        run.return_value = subprocess.CompletedProcess([],0,json.dumps(output()),'')
        assert adapter.analyze(position,max_visits=100).status == 'ok'
        assert json.loads(run.call_args.kwargs['input'])['maxVisits'] == 100
        assert run.call_args.kwargs['env']['PATH'] == str(runtime)+os.pathsep+before
        assert os.environ.get('PATH','') == before
        assert 'reportAnalysisWinratesAs=BLACK' in run.call_args.args[0]


def test_missing_runtime(configured):
    adapter,position = configured; adapter.dll_directory = str(adapter.config)+'/missing'
    with patch('katago_adapter.shutil.which',return_value='katago'):
        assert adapter.analyze(position).status == 'unavailable'


@pytest.mark.parametrize('visits',[0,-1,False,1.5])
def test_invalid_budget(configured,visits):
    adapter,position = configured
    with patch('katago_adapter.shutil.which',return_value='katago'), patch('katago_adapter.subprocess.run') as run:
        assert adapter.analyze(position,max_visits=visits).status == 'error'
        run.assert_not_called()


def test_mismatched_player(configured):
    adapter,position = configured
    raw = output(); raw['rootInfo']['currentPlayer'] = 'W'
    with patch('katago_adapter.shutil.which',return_value='katago'), patch('katago_adapter.subprocess.run') as run:
        run.return_value = subprocess.CompletedProcess([],0,json.dumps(raw),'')
        assert adapter.analyze(position).status == 'error'


class FakeStdout:
    def __init__(self):
        self.lines = queue.Queue()

    def __iter__(self):
        return self

    def __next__(self):
        line = self.lines.get()
        if line is None:
            raise StopIteration
        return line

    def close(self):
        self.lines.put(None)


class FakeStdin:
    def __init__(self, process, respond=True, empty_pv=False):
        self.process = process
        self.respond = respond
        self.empty_pv = empty_pv
        self.closed = False

    def write(self, line):
        query_data = json.loads(line)
        self.process.queries.append(query_data)
        if self.respond:
            raw = output()
            raw['id'] = query_data['id']
            raw['turnNumber'] = len(query_data['moves'])
            raw['rootInfo']['currentPlayer'] = query_data['initialPlayer']
            if self.empty_pv:
                raw['moveInfos'][1]['pv'] = []
            self.process.response_ids.append(raw['id'])
            self.process.stdout.lines.put(json.dumps({**raw, 'id': 'another-query'}) + '\n')
            self.process.stdout.lines.put(json.dumps(raw) + '\n')
        return len(line)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, pid, respond=True, empty_pv=False):
        self.pid = pid
        self.returncode = None
        self.queries = []
        self.response_ids = []
        self.stdout = FakeStdout()
        self.stdin = FakeStdin(self, respond, empty_pv)
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self.stdout.lines.put(None)

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.stdout.lines.put(None)

    def wait(self, timeout=None):
        return self.returncode


class FakePopen:
    def __init__(self, *, respond=True, empty_pv=False):
        self.respond = respond
        self.empty_pv = empty_pv
        self.processes = []

    def __call__(self, *args, **kwargs):
        process = FakeProcess(4100 + len(self.processes), self.respond, self.empty_pv)
        self.processes.append(process)
        return process


@pytest.fixture
def persistent(configured):
    adapter, position = configured
    return PersistentKataGoAdapter(adapter.executable, adapter.config, adapter.model), position


def test_persistent_reuses_process_and_matches_unique_response_ids(persistent):
    adapter, position = persistent
    popen = FakePopen()
    with patch('katago_adapter.shutil.which', return_value='katago'), \
            patch('katago_adapter.subprocess.Popen', side_effect=popen):
        first = adapter.analyze(position)
        pid = adapter.pid
        second = adapter.analyze(position)
        adapter.close()
    assert first.status == second.status == 'ok'
    assert len(popen.processes) == 1 and pid == 4100
    process = popen.processes[0]
    query_ids = [item['id'] for item in process.queries]
    assert query_ids == process.response_ids
    assert len(set(query_ids)) == 2


def test_persistent_accepts_empty_best_pv_and_shutdown_stops_process(persistent):
    adapter, position = persistent
    popen = FakePopen(empty_pv=True)
    with patch('katago_adapter.shutil.which', return_value='katago'), \
            patch('katago_adapter.subprocess.Popen', side_effect=popen):
        result = adapter.analyze(position)
        adapter.shutdown()
    assert result.status == 'ok' and result.pv == []
    assert popen.processes[0].terminated and adapter.pid is None


def test_persistent_timeout_cleans_process(persistent):
    adapter, position = persistent
    adapter.timeout = .01
    popen = FakePopen(respond=False)
    with patch('katago_adapter.shutil.which', return_value='katago'), \
            patch('katago_adapter.subprocess.Popen', side_effect=popen), \
            pytest.raises(KataGoProcessError, match='timed out'):
        adapter.analyze(position)
    assert popen.processes[0].terminated and adapter.pid is None


def test_persistent_dead_process_errors_then_restarts(persistent):
    adapter, position = persistent
    popen = FakePopen()
    with patch('katago_adapter.shutil.which', return_value='katago'), \
            patch('katago_adapter.subprocess.Popen', side_effect=popen):
        assert adapter.analyze(position).status == 'ok'
        popen.processes[0].terminate()
        with pytest.raises(KataGoProcessError, match='exited before query'):
            adapter.analyze(position)
        assert adapter.analyze(position).status == 'ok'
        adapter.close()
    assert len(popen.processes) == 2
