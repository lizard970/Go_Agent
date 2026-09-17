from types import SimpleNamespace
from unittest.mock import Mock

import agent_home
import memory_store
from agent_home import (HomeReviewRun, build_review_request,
                        run_review_request, save_review_run)
from deep_analysis import DeepAnalysisEvidence
from error_detection import ErrorEvent
from error_explanation import (ExplanationResult, StructuredExplanation,
                               build_llm_evidence)
from review_agent import ReviewAgentResult, ReviewAgentState, TraceEntry
from sgf_ingestion import parse_sgf


SGF = b"(;SZ[9];B[aa];W[bb])"


def test_run_builds_real_context_closes_adapter_and_disables_automatic_save():
    request = build_review_request(SGF, "W", "只看第二手", 2)
    adapter = Mock()
    delegated = Mock()
    delegated.decide.return_value = {
        "route": "targeted_review", "target_move": 2, "top_k": 1,
        "history_requested": False, "save_memory": True,
    }
    captured = {}

    class FakeAgent:
        def __init__(self, controller, context):
            captured["context"] = context
            captured["plan"] = controller.decide({}, [])

        def run(self, goal, *, user_color, target_move):
            captured["arguments"] = goal, user_color, target_move
            state = ReviewAgentState(goal, user_color, target_move, "g", "r")
            return ReviewAgentResult("completed", "ok", state)

    run = run_review_request(
        request,
        adapter_factory=lambda: adapter,
        controller_factory=lambda: delegated,
        explanation_service_factory=lambda: SimpleNamespace(),
        agent_factory=FakeAgent,
    )
    assert run.game.size == 9
    assert captured["context"].game_content == SGF
    assert captured["context"].adapter is adapter
    assert captured["arguments"] == ("只看第二手", "W", 2)
    assert captured["plan"]["save_memory"] is False
    adapter.close.assert_called_once_with()


def test_request_validation():
    for arguments in ((b"", "B", "goal"), (SGF, "X", "goal"), (SGF, "B", "")):
        try:
            build_review_request(*arguments)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid request was accepted")


def completed_run(request):
    state = ReviewAgentState(request.goal, request.user_color, request.target_move, "g", "r")
    state.trace = [TraceEntry(
        0, "plan", None,
        {"route": "targeted_review" if request.target_move else "full_review"},
        "succeeded",
    )]
    return HomeReviewRun(
        request, parse_sgf(request.sgf_content),
        ReviewAgentResult("completed", "cached answer", state),
    )


def test_completed_review_is_restored_without_expensive_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_store, "MEMORY_DB", tmp_path / "review.db")
    request = build_review_request(SGF, "B", "整盘复盘")
    original = completed_run(request)
    memory_store.save_review_run_cache(original, "full_review")
    launch = Mock(side_effect=AssertionError("cache hit must not launch KataGo"))
    monkeypatch.setattr(agent_home.PersistentKataGoAdapter, "from_env", launch)
    progress = []

    restored = run_review_request(request, progress_callback=progress.append)

    assert restored.result.answer == "cached answer"
    assert launch.call_count == 0
    assert progress[-1] == "分析完成（已读取缓存）"
    assert memory_store.load_latest_review_run_cache().result.answer == "cached answer"
    assert memory_store.load_review_run_cache(SGF, "W", "整盘复盘", None) is None


def test_save_feedback_is_idempotent_and_event_appears_in_mistake_read_path(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_store, "MEMORY_DB", tmp_path / "review.db")
    request = build_review_request(SGF, "B", "整盘复盘")
    run = completed_run(request)
    event = ErrorEvent(1, 1, "B", [1], .12, 3.0, .12, 3.0, .12, 3.0, 1, 2.4)
    deep = DeepAnalysisEvidence(
        1, "B", "opening", 1, 1, 1, "A9", "B8",
        .60, 3.0, .48, 0.0, .12, 3.0, 500, [],
    )
    evidence = build_llm_evidence(deep)
    explanation = ExplanationResult(
        "succeeded", evidence,
        StructuredExplanation(
            "方向选择", "实战代价较高。", "局面评价下降。",
            "优先比较推荐着。", "关键处先列候选。",
        ),
    )
    state = run.result.state
    state.evidence_events = [event]
    state.deep_evidence = [deep]
    state.explanation_results = [explanation]
    state.embedding_vectors[0] = [1.0, 0.0]

    first = save_review_run(run)
    second = save_review_run(run)
    records = memory_store.load_mistake_records()
    context = memory_store.load_mistake_game_context(first["event_ids"][0])

    assert first["status"] == "saved"
    assert second["status"] == "already_saved"
    assert any(record["id"] == first["event_ids"][0] for record in records)
    assert context["source_content"] == SGF
    assert context["worst_move_number"] == 1
    assert context["board_snapshot"] == {
        "black": [[0, 0]], "board_size": 9, "move_number": 1, "white": []
    }
    assert context["review_run"].result.answer == "cached answer"


def test_save_feedback_reports_non_saveable_position():
    request = build_review_request(SGF, "B", "只分析第二手", 2)
    assert save_review_run(completed_run(request))["status"] == "not_saveable"


def test_dashboard_counts_distinct_games_and_structured_events(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_store, "MEMORY_DB", tmp_path / "review.db")
    request = build_review_request(SGF, "B", "整盘复盘")
    run = completed_run(request)
    memory_store.save_review_run_cache(run, "full_review")

    for number, actual in ((1, "A9"), (2, "B8")):
        event = ErrorEvent(number, number, "B", [number], .1, 2.5, .1, 2.5,
                           .1, 2.5, number, 2.0)
        deep = DeepAnalysisEvidence(
            number, "B", "opening", number, number, number, actual, "C7",
            .6, 3.0, .5, .5, .1, 2.5, 500, [],
        )
        evidence = build_llm_evidence(deep)
        explanation = ExplanationResult(
            "succeeded", evidence,
            StructuredExplanation("标题不是类型", "说明", "影响", "计划", "要点"),
        )
        memory_store.save_error_memory(
            SGF, 9, "B", event, evidence, explanation,
            embedding_fn=lambda _texts: [[1.0, 0.0]], embedding_model="fake",
        )

    summary = memory_store.load_dashboard_summary()
    assert summary["total_reviews"] == 1
    assert summary["mistake_count"] == 2
    assert summary["phase_counts"] == {"opening": 2}
    assert sum(summary["games_by_date"].values()) == 1
