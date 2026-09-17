import json
from types import SimpleNamespace
from unittest.mock import Mock

from deep_analysis import DeepAnalysisEvidence
from error_detection import ErrorEvent
from error_ranking import RankedErrorEvent
from game_scan import GameScanResult, MoveAnalysis
from katago_adapter import AnalysisResult, Candidate
from move_commentary import (MoveCommentary, MoveCommentaryService,
                             build_commentary_evidence,
                             build_move_importance,
                             importance_timeline_data)
from error_explanation import StructuredExplanation
from review_agent import ReviewAgent, ReviewAgentContext
import review_agent
from sgf_ingestion import parse_sgf


def analysis(number, *, best="D4", candidates=None):
    candidates = candidates or [
        Candidate(best, .55, 1.0, .4, 60, [best, "E5"]),
        Candidate("Q16", .54, .7, .3, 30, ["Q16"]),
    ]
    return AnalysisResult(
        "ok", winrate=.55, score_lead=1.0, best_move=best,
        candidates=candidates, current_player="black", visits=100,
        pv=list(candidates[0].pv), move_number=number,
    )


def move(number, player, actual, wr_loss, score_loss):
    return MoveAnalysis(
        number, player, actual, "D4", .60, 3.0,
        .60 - wr_loss if player == "B" else .60 + wr_loss,
        3.0 - score_loss if player == "B" else 3.0 + score_loss,
        wr_loss, score_loss, 100, number, number / 81, "opening", [],
    )


def event(number, player="B", severity=3.0):
    return ErrorEvent(
        number, number, player, [number], .12, 3.0, .12, 3.0,
        .12, 3.0, number, severity,
    )


def fixture_scan():
    positions = [analysis(i) for i in range(5)]
    # Move 4 is a distinguished best move for White: lower Black evaluation.
    positions[3] = analysis(3, best="D4", candidates=[
        Candidate("D4", .40, -2.0, .6, 70, ["D4", "C3"]),
        Candidate("Q16", .44, -1.0, .2, 20, ["Q16"]),
    ])
    moves = [
        move(1, "B", "C3", .001, .1),
        move(2, "W", "Q16", .03, 1.0),
        move(3, "B", "C4", .12, 3.0),
        move(4, "W", "D4", 0.0, 0.0),
    ]
    return GameScanResult(positions, moves, "incomplete")


def test_every_move_is_scored_and_genuinely_severe_top_error_is_critical():
    scan = fixture_scan()
    minor_event, top_event = event(2, "W", 1.2), event(3, "B", 3.0)
    ranked = [RankedErrorEvent(1, top_event, "opening", 3, 3, 3, 3.0)]
    importance = build_move_importance(scan, [minor_event, top_event], ranked)
    assert [item.move_number for item in importance] == [1, 2, 3, 4]
    assert importance[0].commentary_level == "quiet"
    assert importance[2].commentary_level == "critical"
    assert importance[2].is_top_error is True
    assert importance[3].polarity == "positive"
    assert all(0 <= item.importance_score <= 1 for item in importance)

    game = SimpleNamespace(
        size=9, moves=[1, 2, 3, 4], rules="japanese", komi=6.5,
        metadata={},
    )
    selected = build_commentary_evidence(
        game, scan, importance, user_color="B",
        deep_evidence=[DeepAnalysisEvidence(
            1, "B", "opening", 3, 3, 3, "C4", "D4",
            .6, 3.0, .48, 0.0, .12, 3.0, 500, [], ["D4", "E5"], [], {},
        )],
    )
    assert [item.move_number for item in selected] == [3, 4]
    assert selected[0].commentary_level == "critical"
    assert selected[0].is_user_move is True
    assert selected[1].polarity == "positive"
    assert selected[1].is_user_move is False
    assert selected[0].pv
    assert selected[0].deep_evidence is not None


def test_low_loss_top_one_remains_minor():
    scan = fixture_scan()
    low_event = event(2, "W", .6)
    ranked = [RankedErrorEvent(1, low_event, "opening", 2, 2, 2, .6)]
    importance = build_move_importance(scan, [low_event], ranked)
    item = importance[1]
    assert item.is_top_error is True
    assert item.commentary_level == "minor"
    assert item.importance_score < .58


def test_one_batch_maps_results_by_move_number_and_supplies_levels():
    evidence = [
        SimpleNamespace(move_number=2, to_dict=lambda: {
            "move_number": 2, "commentary_level": "minor"
        }),
        SimpleNamespace(move_number=8, to_dict=lambda: {
            "move_number": 8, "commentary_level": "critical"
        }),
    ]
    comments = [
        {"move_number": number, "headline": f"第{number}手", "commentary": "自然点评。",
         "why_it_matters": "影响判断。", "better_plan": "比较推荐着。", "learning_point": "先列候选。"}
        for number in (8, 2)
    ]
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"comments": comments}, ensure_ascii=False)))]
    )
    result = MoveCommentaryService(client=client, model="fake").comment_many(evidence)
    assert client.chat.completions.create.call_count == 1
    assert [item.move_number for item in result] == [2, 8]
    prompt = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert '"commentary_level":"minor"' in prompt
    assert '"commentary_level":"critical"' in prompt


def test_oversized_commentary_is_split_and_merged_in_input_order():
    evidence = [SimpleNamespace(
        move_number=number,
        to_dict=lambda number=number: {
            "move_number": number, "commentary_level": "major",
            "is_user_move": True,
        },
    ) for number in range(1, 6)]

    def response(numbers):
        values = [
            {"move_number": number, "headline": f"第{number}手", "commentary": "点评。",
             "why_it_matters": "影响。", "better_plan": "改进。", "learning_point": "要点。"}
            for number in reversed(numbers)
        ]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"comments": values}, ensure_ascii=False)
        ))])

    client = Mock()
    client.chat.completions.create.side_effect = [
        response([1, 2]), response([3, 4]), response([5]),
    ]
    service = MoveCommentaryService(client=client, model="fake", max_batch_size=2)
    result = service.comment_many(evidence)
    assert client.chat.completions.create.call_count == 3
    assert service.provider_call_count == 3
    assert [item.move_number for item in result] == [1, 2, 3, 4, 5]


def test_opponent_minor_is_local_only_but_major_and_standout_positive_are_selected():
    scan = fixture_scan()
    game = SimpleNamespace(
        size=9, moves=[1, 2, 3, 4], rules="japanese", komi=6.5,
        metadata={},
    )
    importance = build_move_importance(scan, [], [])
    # Move 2 is an ordinary opponent minor; move 4 is a distinguished positive.
    selected = build_commentary_evidence(
        game, scan, importance, user_color="B"
    )
    assert 2 not in [item.move_number for item in selected]
    opponent = next(item for item in selected if item.move_number == 4)
    assert opponent.is_user_move is False
    assert opponent.commentary_level == "major"
    assert opponent.polarity == "positive"
    value = {"move_number": 4, "headline": "对手的强手", "commentary": "注意对手的选择。",
             "why_it_matters": "形成压力。", "better_plan": "提前应对。", "learning_point": "观察对手候选。"}
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"comments": [value]}, ensure_ascii=False)
        ))]
    )
    MoveCommentaryService(client=client, model="fake").comment_many([opponent])
    prompt = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert '"is_user_move":false' in prompt
    assert "必须明确写成对手的选择" in prompt

    scan.moves[1] = move(2, "W", "Q16", .06, 2.5)
    importance = build_move_importance(scan, [], [])
    selected = build_commentary_evidence(game, scan, importance, user_color="B")
    opponent_major = next(item for item in selected if item.move_number == 2)
    assert opponent_major.is_user_move is False
    assert opponent_major.commentary_level == "major"
    assert opponent_major.polarity == "negative"

    scan.moves[1] = move(2, "W", "Q16", .12, 5.0)
    importance = build_move_importance(scan, [], [])
    selected = build_commentary_evidence(game, scan, importance, user_color="B")
    opponent_critical = next(item for item in selected if item.move_number == 2)
    assert opponent_critical.is_user_move is False
    assert opponent_critical.commentary_level == "critical"


def test_timeline_spans_whole_game_and_marks_current_move():
    scan = fixture_scan()
    importance = build_move_importance(scan, [], [])
    rows = importance_timeline_data(importance, 3)
    assert [row["move_number"] for row in rows] == [1, 2, 3, 4]
    assert [row["is_current"] for row in rows] == [False, False, True, False]


def test_targeted_non_error_uses_two_positions_and_gets_detailed_commentary(monkeypatch):
    raw = b"(;SZ[9];B[aa];W[bb])"
    game = parse_sgf(raw)
    adapter = Mock()

    def analyze_position(position, *, max_visits):
        number = position.move_number
        winrate, score = ((.60, 3.0) if number == 0 else (.59, 2.8))
        candidate = Candidate("A9", winrate, score, .7, 500, ["A9", "B8"])
        return AnalysisResult(
            "ok", winrate=winrate, score_lead=score, best_move="A9",
            candidates=[candidate], current_player="black", visits=500,
            pv=list(candidate.pv), move_number=number,
        )

    adapter.analyze.side_effect = analyze_position

    class Planner:
        def decide(self, state, tools):
            return {"route": "targeted_review", "target_move": 1, "top_k": 3,
                    "history_requested": False, "save_memory": False}

    class Commentary:
        def __init__(self):
            self.calls = []

        def comment_many(self, evidence):
            self.calls.append(evidence)
            item = evidence[0]
            semantic = StructuredExplanation(
                "值得研究的选择", "这手与推荐方向一致。", "可作为比较基准。",
                "继续核对后续变化。", "好手也要理解替代方案。",
            )
            return [MoveCommentary(
                item.move_number, "succeeded", semantic.title,
                semantic.summary, semantic,
            )]

    commentary = Commentary()
    progress = []
    scan = Mock(side_effect=AssertionError("targeted review must not scan full game"))
    monkeypatch.setattr("review_agent.scan_game", scan)
    context = ReviewAgentContext(
        game, raw, adapter, SimpleNamespace(), commentary_service=commentary,
        progress_callback=progress.append,
    )
    result = ReviewAgent(Planner(), context).run(
        "只分析第1手", user_color="B", target_move=1
    )
    assert result.status == "completed"
    assert adapter.analyze.call_count == 2
    assert scan.call_count == 0
    assert len(commentary.calls) == 1
    evidence = commentary.calls[0][0]
    assert evidence.game_context["review_mode"] == "targeted"
    assert evidence.is_top_error is False
    assert result.state.evidence_events == [None]
    assert result.state.move_comments[1].headline == "值得研究的选择"
    assert progress == ["KataGo 正在深度分析第 1 手…", "正在生成点评…"]


def test_full_review_keeps_one_planner_commentary_and_embedding_call(monkeypatch):
    raw = b"(;SZ[9];B[aa];W[bb];B[cc];W[dd])"
    game = parse_sgf(raw)
    scan_result = fixture_scan()
    deep = DeepAnalysisEvidence(
        1, "B", "opening", 3, 3, 3, "C4", "D4",
        .60, 3.0, .48, 0.0, .12, 3.0, 500, [], ["D4", "E5"], [], {},
    )
    monkeypatch.setattr(review_agent, "scan_game", Mock(return_value=scan_result))
    monkeypatch.setattr(review_agent, "deep_analyze_ranked_events", Mock(return_value=[deep]))
    monkeypatch.setattr(review_agent, "search_similar_errors", Mock(return_value=[]))

    class Planner:
        calls = 0

        def decide(self, state, tools):
            self.calls += 1
            return {"route": "full_review", "target_move": None, "top_k": 3,
                    "history_requested": False, "save_memory": False}

    class Commentary:
        calls = 0

        def comment_many(self, evidence):
            self.calls += 1
            return [MoveCommentary(
                item.move_number, "succeeded", "关键选择", "自然语言深度点评。",
                StructuredExplanation(
                    "关键选择", "自然语言深度点评。", "局面评价下降。",
                    "比较推荐着。", "关键处先列候选。",
                ),
            ) for item in evidence]

    planner, commentary = Planner(), Commentary()
    embeddings = Mock(return_value=[[1.0, 0.0]])
    progress = []
    context = ReviewAgentContext(
        game, raw, Mock(), SimpleNamespace(), commentary_service=commentary,
        embedding_fn=embeddings, embedding_model="fake",
        progress_callback=progress.append,
    )
    result = ReviewAgent(planner, context).run("找三个问题", user_color="B")
    assert result.status == "completed"
    assert planner.calls == result.state.controller_calls == 1
    assert commentary.calls == result.state.explanation_calls == 1
    assert embeddings.call_count == result.state.embedding_calls == 1
    assert "正在筛选值得复盘的位置…" in progress
    assert "正在生成点评…" in progress
    assert "正在检查历史记录…" in progress
