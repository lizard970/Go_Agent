"""Offline Streamlit UI regressions; injected upload bytes never enter production state."""
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import analyzer
import agent_home
from agent_home import HomeReviewRun, build_review_request
from katago_adapter import (AnalysisResult, LocalKataGoAdapter,
                            PersistentKataGoAdapter, normalize_output)
import memory_store
from review_agent import ReviewAgentResult, ReviewAgentState, TraceEntry
from sgf_ingestion import parse_sgf
from move_commentary import MoveCommentary, build_move_importance
from test_move_commentary import fixture_scan
from deep_analysis import DeepAnalysisEvidence
from error_detection import ErrorEvent
from error_explanation import (ExplanationResult, StructuredExplanation,
                               build_llm_evidence)
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
    assert [button.label for button in at.button] == ["开始复盘", "图片复盘", "打开分析工作台"]
    at.switch_page("app_pages/review.py").run()
    assert [item.proto.label for item in at.get("file_uploader")] == ["上传 SGF 棋谱"]
    at.segmented_control(key="review_input_control").set_value("图片").run()
    assert [item.proto.label for item in at.get("file_uploader")] == ["上传棋局截图"]
    for page in ("app_pages/mistakes.py", "app_pages/dashboard.py"):
        at.switch_page(page).run()
        assert not at.exception


def home_result(request, *, status="completed", history=None):
    state = ReviewAgentState(
        request.goal, request.user_color, request.target_move,
        "game", "review",
    )
    state.controller_calls = 1
    state.explanation_calls = 1
    state.embedding_calls = 1
    state.history_summary = history or {}
    state.trace = [TraceEntry(0, "plan", None, {"route": "full_review"}, "succeeded")]
    return HomeReviewRun(
        request,
        parse_sgf(request.sgf_content),
        ReviewAgentResult(status, "离线 Agent 结果", state),
    )


def test_agent_home_submits_once_passes_target_and_persists(uploads, monkeypatch):
    uploads["agent_home_sgf_upload"] = SGF
    calls = []

    def run(request, **_kwargs):
        calls.append(request)
        return home_result(request)

    monkeypatch.setattr(agent_home, "run_review_request", run)
    at = AppTest.from_file("app.py", default_timeout=15).run()
    at.segmented_control(key="agent_home_user_color").set_value("W")
    at.number_input(key="agent_home_target_move").set_value(2)
    at.text_area(key="agent_home_goal").set_value("只看第 2 手")
    next(button for button in at.button if button.label == "开始复盘").click().run()
    assert not at.exception
    assert len(calls) == 1
    assert calls[0].user_color == "W" and calls[0].target_move == 2
    assert calls[0].goal == "只看第 2 手"
    assert any(item.value == "离线 Agent 结果" for item in at.markdown)
    assert [item.value for item in at.metric] == ["1", "1", "1"]
    assert len(at.dataframe) == 1
    at.run()
    assert len(calls) == 1
    assert any(item.value == "离线 Agent 结果" for item in at.markdown)


def test_agent_home_history_visibility_and_controlled_failure(uploads, monkeypatch):
    uploads["agent_home_sgf_upload"] = SGF
    request = build_review_request(SGF, "B", "帮我复盘")
    monkeypatch.setattr(agent_home, "run_review_request", lambda _request, **_kwargs: home_result(request))
    at = AppTest.from_file("app.py", default_timeout=15).run()
    at.text_area(key="agent_home_goal").set_value("帮我复盘")
    next(button for button in at.button if button.label == "开始复盘").click().run()
    assert not any("潜在相似记录" in item.value for item in at.info)

    signal = {"key_errors_with_history_signal": 1, "historical_candidate_count": 2}
    at.session_state["agent_home_review_run"] = home_result(request, history=signal)
    at.run()
    assert any("2 条较强的潜在相似记录" in item.value for item in at.info)

    failed = home_result(request, status="partial")
    failed = HomeReviewRun(request, failed.game, ReviewAgentResult(
        "partial", "KataGo 暂时不可用", failed.result.state,
    ))
    at.session_state["agent_home_review_run"] = failed
    at.run()
    assert any("KataGo 暂时不可用" in item.value for item in at.warning)


def test_agent_home_save_requires_explicit_click(uploads, monkeypatch):
    uploads["agent_home_sgf_upload"] = SGF
    request = build_review_request(SGF, "B", "帮我复盘")
    run = home_result(request)
    run.result.state.evidence_events = [SimpleNamespace(player="B")]
    save = Mock(return_value={"status": "saved", "event_ids": ["event"]})
    monkeypatch.setattr(agent_home, "run_review_request", lambda _request, **_kwargs: run)
    monkeypatch.setattr(agent_home, "save_review_run", save)
    at = AppTest.from_file("app.py", default_timeout=15).run()
    at.text_area(key="agent_home_goal").set_value("帮我复盘")
    next(button for button in at.button if button.label == "开始复盘").click().run()
    save.assert_not_called()
    at.button(key="agent_home_save").click().run()
    save.assert_called_once_with(run)
    assert any("保存成功" in item.value for item in at.success)


def test_analysis_workspace_uses_cached_player_without_new_api_calls(monkeypatch):
    request = build_review_request(SGF, "B", "帮我复盘")
    run = home_result(request)
    scan = fixture_scan()
    run.result.state.scan_result = scan
    run.result.state.move_importance = build_move_importance(scan, [], [])
    run.result.state.move_comments[2] = MoveCommentary(
        2, "succeeded", "第二手点评", "缓存中的自然语言点评。"
    )
    analyze = Mock(side_effect=AssertionError("cached navigation must not analyze"))
    monkeypatch.setattr(PersistentKataGoAdapter, "analyze", analyze)

    at = AppTest.from_file("app.py", default_timeout=15).run()
    at.session_state["agent_home_review_run"] = run
    at.switch_page("app_pages/review.py").run()
    assert not at.exception
    at.button(key="workspace_next").click().run()
    assert at.slider(key="workspace_agent_move_game").value == 2
    assert not at.exception
    assert analyze.call_count == 0
    assert any("第二手点评" in item.value for item in at.markdown)
    at.run()
    assert any("缓存中的自然语言点评" in item.value for item in at.markdown)
    at.slider(key="workspace_agent_move_game").set_value(1).run()
    assert at.slider(key="workspace_agent_move_game").value == 1


def test_mistake_book_reopens_cached_game_at_saved_move_without_api(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_store, "MEMORY_DB", tmp_path / "review.db")
    request = build_review_request(SGF, "W", "整盘复盘")
    run = home_result(request)
    scan = fixture_scan()
    run.result.state.scan_result = scan
    run.result.state.move_importance = build_move_importance(scan, [], [])
    run.result.state.move_comments[2] = MoveCommentary(
        2, "succeeded", "历史第二手", "缓存点评仍然可用。"
    )
    memory_store.save_review_run_cache(run, "full_review")
    event = ErrorEvent(2, 2, "W", [2], .08, 2.5, .08, 2.5, .08, 2.5, 2, 1.6)
    deep = DeepAnalysisEvidence(
        1, "W", "opening", 2, 2, 2, "A9", "B8",
        .55, 1.0, .63, 3.5, .08, 2.5, 500, [],
    )
    evidence = build_llm_evidence(deep)
    memory_store.save_error_memory(
        SGF, 9, "W", event, evidence,
        ExplanationResult(
            "succeeded", evidence,
            StructuredExplanation("历史第二手", "缓存点评仍然可用。", "影响", "计划", "要点"),
        ),
        embedding_fn=lambda _texts: [[1.0]], embedding_model="fake",
    )
    analyze = Mock(side_effect=AssertionError("history navigation must stay local"))
    monkeypatch.setattr(PersistentKataGoAdapter, "analyze", analyze)

    at = AppTest.from_file("app.py", default_timeout=15).run()
    at.switch_page("app_pages/mistakes.py").run()
    next(button for button in at.button if button.label == "查看").click().run()
    next(button for button in at.button if button.label == "回到棋局查看").click().run()

    assert not at.exception
    assert at.slider(key="workspace_agent_move_game").value == 2
    assert any("历史第二手" in item.value for item in at.markdown)
    assert analyze.call_count == 0


def test_sgf_result_persists_invalidates_and_uses_modes(uploads, monkeypatch):
    uploads["sgf_upload"] = SGF
    raw = output()
    raw["turnNumber"] = 4
    analyze = Mock(return_value=normalize_output(raw))
    monkeypatch.setattr(LocalKataGoAdapter, "analyze", analyze)
    def scan_response(position, *, max_visits=None):
        scan_raw = output()
        scan_raw["turnNumber"] = position.move_number
        scan_raw["rootInfo"]["currentPlayer"] = "B" if position.next_player == "black" else "W"
        return normalize_output(scan_raw)
    scan_analyze = Mock(side_effect=scan_response)
    close = Mock()
    monkeypatch.setattr(PersistentKataGoAdapter, "analyze", scan_analyze)
    monkeypatch.setattr(PersistentKataGoAdapter, "close", close)
    at = review_app()
    at.button(key="katago_analyze").click().run()
    assert not at.exception and not at.error
    assert analyze.call_args.kwargs["max_visits"] == 150
    assert any("visits：500" in item.value for item in at.caption)
    assert any("D4 → E5" in item.value for item in at.markdown)
    assert at.metric[0].value == "60.0%" and at.metric[1].value == "+2.3"
    at.segmented_control(key="sgf_user_color").set_value("W").run()
    assert analyze.call_count == 1
    assert at.metric[0].value == "40.0%" and at.metric[1].value == "-2.3"
    at.button(key="scan_game").click().run()
    assert scan_analyze.call_count == 5
    assert {call.kwargs["max_visits"] for call in scan_analyze.call_args_list} == {100}
    assert close.call_count == 1
    assert any("已完成 5 个局面、4 手" in item.value for item in at.success)
    at.segmented_control(key="sgf_user_color").set_value("B").run()
    assert scan_analyze.call_count == 5
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
    assert at.metric[0].value == "2" and at.metric[2].value == "0"
