"""Offline Streamlit UI regressions; injected upload bytes never enter production state."""
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import analyzer
from katago_adapter import AnalysisResult, LocalKataGoAdapter, normalize_output
import memory_store
from test_sgf_katago import output


SGF = b'(;SZ[9]KM[6.5];B[ba];W[aa];B[ab];W[])'


@pytest.fixture
def uploads(monkeypatch):
    values = {}
    original = st.file_uploader

    def upload(*args, **kwargs):
        rendered = original(*args, **kwargs)
        data = values.get(kwargs.get("key"))
        if data is None:
            return rendered
        stream = io.BytesIO(data)
        stream.name = "test.sgf" if kwargs.get("key") == "sgf_upload" else "board.png"
        return stream

    monkeypatch.setattr(st, "file_uploader", upload)
    return values


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(analyzer, "get_client", Mock(side_effect=AssertionError("Unexpected LLM call")))


def review_app(input_kind="SGF"):
    at = AppTest.from_file("app.py", default_timeout=15).run()
    at.session_state["review_input"] = input_kind
    return at.switch_page("app_pages/review.py").run()


def test_navigation_and_input_routes_without_api_key(monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    at = AppTest.from_file("app.py", default_timeout=15).run()
    assert not at.exception
    assert [button.label for button in at.button] == ["上传棋盘截图", "上传 SGF 棋谱"]
    at.switch_page("app_pages/review.py").run()
    assert [item.proto.label for item in at.get("file_uploader")] == ["上传 SGF 棋谱"]
    at.segmented_control(key="review_input_control").set_value("图片").run()
    assert [item.proto.label for item in at.get("file_uploader")] == ["上传棋局截图"]
    for page in ("app_pages/mistakes.py", "app_pages/dashboard.py"):
        at.switch_page(page).run()
        assert not at.exception


def test_sgf_result_persists_invalidates_and_uses_modes(uploads, monkeypatch):
    uploads["sgf_upload"] = SGF
    raw = output()
    raw["turnNumber"] = 4
    analyze = Mock(return_value=normalize_output(raw))
    monkeypatch.setattr(LocalKataGoAdapter, "analyze", analyze)
    at = review_app()
    at.button(key="katago_analyze").click().run()
    assert not at.exception and not at.error
    assert analyze.call_args.kwargs["max_visits"] == 150
    assert any("visits：500" in item.value for item in at.caption)
    assert any("D4 → E5" in item.value for item in at.markdown)
    at.segmented_control(key="review_depth").set_value("深度").run()
    assert not any("visits：500" in item.value for item in at.caption)
    at.button(key="katago_analyze").click().run()
    assert analyze.call_args.kwargs["max_visits"] == 500
    at.slider[0].set_value(2).run()
    assert not any("visits：500" in item.value for item in at.caption)
    uploads["sgf_upload"] = b'(;SZ[13];B[aa])'
    at.run()
    assert not at.exception and at.slider[0].value == 1


@pytest.mark.parametrize("status", ["unavailable", "error"])
def test_engine_failure_is_handled(uploads, monkeypatch, status):
    uploads["sgf_upload"] = SGF
    monkeypatch.setattr(LocalKataGoAdapter, "analyze",
                        lambda *args, **kwargs: AnalysisResult(status, "engine failure"))
    at = review_app()
    at.button(key="katago_analyze").click().run()
    assert not at.exception and at.error


def test_image_recognition_manual_confirm_gpt_add_and_read(uploads, monkeypatch, tmp_path):
    from PIL import Image

    monkeypatch.setattr(memory_store, "MEMORY_DB", tmp_path / "review.db")
    buffer = io.BytesIO()
    Image.new("RGB", (30, 30), "white").save(buffer, format="PNG")
    uploads["board_uploader_0"] = buffer.getvalue()
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content='{"board_size":9,"stones":[{"x":0,"y":0,"color":"black"}]}'))])
    monkeypatch.setattr(analyzer, "get_client", lambda: client)
    monkeypatch.setattr(analyzer, "analyze_with_gpt", Mock(return_value={"commentary": "offline commentary"}))
    high = {"commentary": "deep commentary", "position_summary": "真实测试局面",
            "count_estimate": {"lead": "black"},
            "issues": [{"issue_type": "忽视断点", "region": "左上", "severity": "high",
                        "evidence": "A9 邻近断点", "improvement": "先补断点", "embedding_text": "断点测试"}]}
    monkeypatch.setattr(analyzer, "analyze_high_with_gpt", Mock(return_value=high))
    monkeypatch.setattr(analyzer, "get_embeddings", Mock(return_value=[[1.0, 0.0], [1.0, 0.0]]))

    at = review_app("图片")
    next(button for button in at.button if button.label == "确认棋盘").click().run()
    next(button for button in at.button if button.label == "生成快速点评").click().run()
    assert any(item.value == "offline commentary" for item in at.markdown)
    next(button for button in at.button if button.label == "深度分析并加入错题本").click().run()
    assert not at.exception
    second = {**high, "commentary": "second commentary", "position_summary": "另一真实测试局面"}
    memory_store.save_record(
        {"board_size": 9, "stones": []}, "white", [], "平实", "second low", second,
        "second embedding", [0.0, 1.0], [[0.0, 1.0]], "offline-test",
    )
    records = memory_store.load_mistake_records()
    assert len(records) == 2 and records[0]["issues"][0]["issue_type"] == "忽视断点"
    at.switch_page("app_pages/mistakes.py").run()
    assert not at.exception
    assert any("真实测试局面" in item.value for item in at.markdown)
    next(button for button in at.button if button.label == "查看").click().run()
    assert any(item.value == "second commentary" for item in at.markdown)
    assert [button.label for button in at.button].count("收起") == 1
    assert [button.label for button in at.button].count("查看") == 1
    assert any("真实测试局面" in item.value for item in at.markdown)
    at.switch_page("app_pages/dashboard.py").run()
    assert not at.exception
    assert at.metric[0].value == "2" and at.metric[2].value == "忽视断点"
