"""UI regressions run offline. Uploaded bytes are injected only in these tests."""
import io
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
import analyzer
from katago_adapter import AnalysisResult, LocalKataGoAdapter, normalize_output
from test_sgf_katago import output

SGF = b'(;SZ[9]KM[6.5];B[ba];W[aa];B[ab];W[])'

@pytest.fixture
def uploads(monkeypatch):
    values = {}
    original = st.file_uploader
    def upload(*args, **kwargs):
        rendered = original(*args, **kwargs)
        data = values.get(kwargs.get('key'))
        return io.BytesIO(data) if data is not None else rendered
    monkeypatch.setattr(st,'file_uploader',upload)
    return values

@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(analyzer,'get_client',Mock(side_effect=AssertionError('Unexpected LLM call')))


def test_both_input_entries_without_api_key(monkeypatch):
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED','1')
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    at=AppTest.from_file('app.py',default_timeout=15).run()
    assert not at.exception
    assert [item.proto.label for item in at.get('file_uploader')] == ['上传 SGF 棋谱','上传棋局截图']


def test_sgf_result_persists_and_invalidates(uploads,monkeypatch):
    uploads['sgf_upload']=SGF
    raw=output();raw['turnNumber']=4
    analyze=Mock(return_value=normalize_output(raw))
    monkeypatch.setattr(LocalKataGoAdapter,'analyze',analyze)
    at=AppTest.from_file('app.py',default_timeout=15).run()
    at.button(key='katago_analyze').click().run()
    assert not at.exception and not at.error
    assert any('搜索次数（visits）：500' in item.value for item in at.markdown)
    assert any('D4 → E5' in item.value for item in at.markdown)
    at.checkbox[0].uncheck().run()
    assert analyze.call_count == 1
    assert any('搜索次数（visits）：500' in item.value for item in at.markdown)
    at.number_input[0].set_value(2).run()
    assert not at.exception
    assert not any('搜索次数（visits）' in item.value for item in at.markdown)
    at.button(key='katago_analyze').click().run()
    assert analyze.call_args.args[0].move_number == 2
    uploads['sgf_upload']=b'(;SZ[13];B[aa])'
    at.run()
    assert not at.exception and at.number_input[0].value == 1
    assert not any('搜索次数（visits）' in item.value for item in at.markdown)


@pytest.mark.parametrize('status',['unavailable','error'])
def test_engine_failure_keeps_image_entry(uploads,monkeypatch,status):
    uploads['sgf_upload']=SGF
    monkeypatch.setattr(LocalKataGoAdapter,'analyze',lambda *args,**kwargs:AnalysisResult(status,'engine failure'))
    at=AppTest.from_file('app.py',default_timeout=15).run()
    at.button(key='katago_analyze').click().run()
    assert not at.exception and at.error
    assert '上传棋局截图' in [item.proto.label for item in at.get('file_uploader')]


def test_image_recognition_manual_confirm_and_gpt(uploads,monkeypatch):
    from PIL import Image
    buffer=io.BytesIO();Image.new('RGB',(30,30),'white').save(buffer,format='PNG')
    uploads['board_uploader_0']=buffer.getvalue()
    client=Mock()
    client.chat.completions.create.return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content='{"board_size":9,"stones":[{"x":0,"y":0,"color":"black"}]}'))])
    monkeypatch.setattr(analyzer,'get_client',lambda:client)
    commentary=Mock(return_value={'commentary':'offline commentary'})
    monkeypatch.setattr(analyzer,'analyze_with_gpt',commentary)
    at=AppTest.from_file('app.py',default_timeout=15).run()
    assert not at.exception
    next(b for b in at.button if b.label=='确认棋盘，进入下一步').click().run()
    next(b for b in at.button if b.label=='生成复盘点评').click().run()
    assert not at.exception
    assert any(item.value=='offline commentary' for item in at.markdown)
    assert commentary.call_args.args[0]['stones']==[{'x':0,'y':0,'color':'black'}]
    assert client.chat.completions.create.call_count==1
