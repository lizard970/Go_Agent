import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import memory_store
import review_agent
from deep_analysis import DeepAnalysisEvidence
from error_detection import ErrorEvent
from error_explanation import (ExplanationResult, StructuredExplanation,
                               build_llm_evidence)
from error_ranking import RankedErrorEvent
from game_scan import GameScanResult, MoveAnalysis
from review_agent import (AgentTool, OpenAIReviewController, ReviewAgent,
                          ReviewAgentContext, ReviewAgentState, ToolRegistry,
                          _check_history_for_evidence, _explain_all,
                          default_tool_registry)


def plan(route="full_review", **overrides):
    value = {
        "route": route,
        "target_move": 87 if route == "targeted_review" else None,
        "top_k": 3,
        "history_requested": route == "memory_review",
        "save_memory": False,
    }
    value.update(overrides)
    return value


class FakePlanner:
    def __init__(self, value=None, error=None):
        self.value = value or plan()
        self.error = error
        self.calls = 0

    def decide(self, state, tools):
        self.calls += 1
        if self.error:
            raise self.error
        return self.value


class FakeExplanationService:
    def __init__(self):
        self.batch_calls = 0
        self.single_calls = 0

    @staticmethod
    def _result(evidence):
        return ExplanationResult(
            "succeeded", evidence,
            StructuredExplanation(
                "方向选择", "实战方向代价较高。", "局面评价下降。",
                "优先比较推荐着。", "关键处先列候选着。",
            ),
        )

    def explain_many(self, evidence):
        self.batch_calls += 1
        return [self._result(item) for item in evidence]

    def explain(self, evidence):
        self.single_calls += 1
        return self._result(evidence)


def make_event(player="B", move=10):
    return ErrorEvent(
        move, move, player, [move], .12, 3.0, .12, 3.0,
        .12, 3.0, move, 2.4,
    )


def make_ranked(player="B", move=10):
    event = make_event(player, move)
    return RankedErrorEvent(1, event, "middlegame", move, move, move, 2.4)


def make_deep(player="B", move=10):
    return DeepAnalysisEvidence(
        1, player, "middlegame", move, move, move, "C7", "Q10",
        .64, 4.5, .52, 1.5, .12, 3.0, 504, [],
    )


def workflow_registry(calls, *, player="B"):
    def handler(name):
        def run(state, context, arguments):
            calls.append(name)
            if name == "scan_full_game":
                state.scan_result = GameScanResult([], [], "incomplete")
            elif name == "find_key_errors":
                state.selected_errors = [make_ranked(player)]
            elif name == "deep_analyze_errors":
                state.deep_evidence = [make_deep(player)]
                state.evidence_events = [make_event(player)]
                state.explanation_results = [None]
            elif name == "analyze_target_move":
                move = arguments["move_number"]
                state.target_move = move
                state.deep_evidence = [make_deep(player, move)]
                state.evidence_events = [make_event(player, move)]
                state.explanation_results = [None]
            elif name == "save_error_memory":
                state.saved_event_ids.append("saved-event")
            return {"called": name}
        return run

    schemas = {
        "scan_full_game": {"max_visits": {"type": "integer", "minimum": 1}},
        "find_key_errors": {"limit": {"type": "integer", "minimum": 1}},
        "deep_analyze_errors": {"max_visits": {"type": "integer", "minimum": 1}},
        "analyze_target_move": {
            "move_number": {"type": "integer", "minimum": 1},
            "max_visits": {"type": "integer", "minimum": 1},
        },
        "save_error_memory": {"evidence_index": {"type": "integer", "minimum": 0}},
    }
    return ToolRegistry([
        AgentTool(
            name, name,
            {
                "type": "object", "properties": properties,
                "required": list(properties), "additionalProperties": False,
            },
            handler(name),
        )
        for name, properties in schemas.items()
    ])


def context(*, service=None, embedding_fn=None, threshold=.85):
    return ReviewAgentContext(
        game=SimpleNamespace(size=19), game_content="current-game",
        adapter=Mock(), explanation_service=service or FakeExplanationService(),
        embedding_fn=embedding_fn, embedding_model="fake",
        history_similarity_threshold=threshold,
    )


def test_full_review_uses_one_plan_call_and_local_deterministic_steps(monkeypatch):
    calls = []
    planner = FakePlanner(plan("full_review", history_requested=False))
    service = FakeExplanationService()
    embeddings = Mock(return_value=[[1.0, 0.0]])
    search = Mock(return_value=[])
    monkeypatch.setattr(review_agent, "search_similar_errors", search)

    result = ReviewAgent(
        planner, context(service=service, embedding_fn=embeddings),
        registry=workflow_registry(calls),
    ).run("复盘这盘棋，只告诉我最值得改的三个问题。", user_color="B")

    assert result.status == "completed"
    assert planner.calls == result.state.controller_calls == 1
    assert calls == ["scan_full_game", "find_key_errors", "deep_analyze_errors"]
    assert service.batch_calls == result.state.explanation_calls == 1
    assert embeddings.call_count == result.state.embedding_calls == 1
    assert search.call_count == 1
    assert result.state.history_summary["key_errors_checked"] == 1


def test_targeted_review_skips_scan_and_uses_one_explanation_call():
    calls = []
    planner = FakePlanner(plan("targeted_review", target_move=87))
    service = FakeExplanationService()
    embeddings = Mock()

    result = ReviewAgent(
        planner, context(service=service, embedding_fn=embeddings),
        registry=workflow_registry(calls),
    ).run("只看第87手，不要整盘扫描。", user_color="B", target_move=87)

    assert result.status == "completed"
    assert calls == ["analyze_target_move"]
    assert result.state.controller_calls == 1
    assert result.state.explanation_calls == 1
    assert result.state.embedding_calls == 0


def test_memory_focused_route_does_not_force_current_game_scan(monkeypatch):
    calls = []
    planner = FakePlanner(plan("memory_review"))
    monkeypatch.setattr(review_agent, "list_personal_embedded_events", Mock(return_value=[]))

    result = ReviewAgent(
        planner, context(), registry=workflow_registry(calls),
    ).run("看看我最近是不是总犯同一种错误。", user_color="B")

    assert result.status == "completed"
    assert calls == []
    assert result.state.controller_calls == 1
    assert result.state.explanation_calls == 0
    assert "暂未找到" in result.answer
    assert any(item.tool_name == "review_history" for item in result.state.observations)


def test_default_history_signal_is_candidate_not_confirmation(monkeypatch):
    match = {
        "event_id": "old-event", "game_id": "old-game",
        "similarity_score": .92, "title": "旧问题", "worst_move_number": 30,
    }
    monkeypatch.setattr(review_agent, "search_similar_errors", Mock(return_value=[match]))
    result = ReviewAgent(
        FakePlanner(plan("full_review", history_requested=False)),
        context(embedding_fn=lambda texts: [[1.0, 0.0] for _ in texts]),
        registry=workflow_registry([]),
    ).run("复盘这盘棋", user_color="B")

    assert result.state.history_summary["historical_candidate_count"] == 1
    assert "潜在相似" in result.answer
    assert "不代表已经确认" in result.answer


def test_no_history_is_silent_unless_explicitly_requested(monkeypatch):
    monkeypatch.setattr(review_agent, "search_similar_errors", Mock(return_value=[]))
    normal = ReviewAgent(
        FakePlanner(plan("full_review", history_requested=False)),
        context(embedding_fn=lambda texts: [[1.0] for _ in texts]),
        registry=workflow_registry([]),
    ).run("复盘这盘棋", user_color="B")
    explicit = ReviewAgent(
        FakePlanner(plan("full_review", history_requested=True)),
        context(embedding_fn=lambda texts: [[1.0] for _ in texts]),
        registry=workflow_registry([]),
    ).run("复盘并看看以前是否犯过", user_color="B")

    assert "历史" not in normal.answer
    assert "历史记录中暂未找到" in explicit.answer


def test_save_occurs_only_when_plan_requests_it(monkeypatch):
    monkeypatch.setattr(review_agent, "search_similar_errors", Mock(return_value=[]))
    without_calls, with_calls = [], []
    ctx = context(embedding_fn=lambda texts: [[1.0] for _ in texts])
    ReviewAgent(
        FakePlanner(plan("full_review", save_memory=False)), ctx,
        registry=workflow_registry(without_calls),
    ).run("复盘", user_color="B")
    saved = ReviewAgent(
        FakePlanner(plan("full_review", save_memory=True)), ctx,
        registry=workflow_registry(with_calls),
    ).run("复盘并保存", user_color="B")

    assert "save_error_memory" not in without_calls
    assert "save_error_memory" in with_calls
    assert saved.state.saved_event_ids == ["saved-event"]


@pytest.mark.parametrize("retries,expected_calls", [(0, 1), (1, 2)])
def test_controller_failure_stops_after_bounded_attempts(retries, expected_calls):
    planner = FakePlanner(error=TimeoutError("planner timeout"))
    calls = []
    result = ReviewAgent(
        planner, context(), registry=workflow_registry(calls),
        controller_retries=retries,
    ).run("复盘", user_color="B")

    assert result.status == "controller_error"
    assert planner.calls == result.state.controller_calls == expected_calls
    assert calls == []
    assert result.state.observations[-1].error_code == "controller_error"
    assert "TimeoutError" in result.state.observations[-1].message


def test_successful_deterministic_work_is_reused(monkeypatch):
    state = ReviewAgentState("review", "B", None, "game", "review")
    service = FakeExplanationService()
    embeddings = Mock(return_value=[[1.0]])
    ctx = context(service=service, embedding_fn=embeddings)
    scan = Mock(return_value=GameScanResult([], [], "incomplete"))
    monkeypatch.setattr(review_agent, "scan_game", scan)
    deep = Mock(return_value=[make_deep()])
    monkeypatch.setattr(review_agent, "deep_analyze_ranked_events", deep)
    search = Mock(return_value=[])
    monkeypatch.setattr(review_agent, "search_similar_errors", search)
    registry = default_tool_registry()

    registry.execute("scan_full_game", {"max_visits": 100}, state, ctx, 1)
    registry.execute("scan_full_game", {"max_visits": 100}, state, ctx, 2)
    state.selected_errors = [make_ranked()]
    state.scan_result = GameScanResult([], [], "incomplete")
    registry.execute("deep_analyze_errors", {"max_visits": 500}, state, ctx, 3)
    registry.execute("deep_analyze_errors", {"max_visits": 500}, state, ctx, 4)
    _explain_all(state, ctx)
    _explain_all(state, ctx)
    _check_history_for_evidence(state, ctx)
    _check_history_for_evidence(state, ctx)

    assert scan.call_count == deep.call_count == 1
    assert service.batch_calls == state.explanation_calls == 1
    assert embeddings.call_count == state.embedding_calls == 1
    assert search.call_count == 1


def test_find_key_errors_respects_user_color():
    def move(number, player):
        return MoveAnalysis(
            number, player, f"M{number}", "Q10", .6, 3.0, .4, 0.0,
            .2, 3.0, 100, number, .2, "middlegame", [],
        )
    state = ReviewAgentState("review", "W", None, "game", "review")
    state.scan_result = GameScanResult([], [move(1, "B"), move(2, "W")], "incomplete")

    observation = default_tool_registry().execute(
        "find_key_errors", {"limit": 1}, state, context(), 1
    )

    assert observation.status == "succeeded"
    assert state.selected_errors[0].event.player == "W"


def test_opponent_error_cannot_be_saved_as_user_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_store, "MEMORY_DB", tmp_path / "memory.db")
    event, deep = make_event("W"), make_deep("W")
    evidence = build_llm_evidence(deep)
    state = ReviewAgentState("review", "B", None, "game", "review")
    state.deep_evidence = [deep]
    state.evidence_events = [event]
    state.explanation_results = [FakeExplanationService._result(evidence)]

    observation = default_tool_registry().execute(
        "save_error_memory", {"evidence_index": 0}, state, context(), 1
    )

    assert observation.status == "error"
    assert "user's color" in observation.message
    assert state.saved_event_ids == []


def test_unknown_tool_and_invalid_arguments_remain_rejected():
    registry = default_tool_registry()
    state = ReviewAgentState("review", "B", None, "game", "review")
    unknown = registry.execute("unknown", {}, state, context(), 1)
    invalid = registry.execute("scan_full_game", {"max_visits": 0}, state, context(), 2)
    assert unknown.error_code == "unknown_tool"
    assert invalid.error_code == "invalid_arguments"


def test_openai_planner_uses_one_structured_configurable_call():
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
            plan("targeted_review", target_move=87)
        )))]
    )
    planner = OpenAIReviewController(client=client, model="planner-test")
    result = planner.decide(
        {"user_request": "只看第87手"}, default_tool_registry().schemas()
    )
    assert result["route"] == "targeted_review"
    assert client.chat.completions.create.call_count == 1
    call = client.chat.completions.create.call_args.kwargs
    assert call["model"] == "planner-test"
    assert call["response_format"]["json_schema"]["strict"] is True
